# Test data directory and known migration pairs
import copy
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from akkudoktoreos.config import configmigrate
from akkudoktoreos.config.config import ConfigEOS, SettingsEOSDefaults
from akkudoktoreos.config.configmigrate import migrate_config_data
from akkudoktoreos.core.version import __version__

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

MIGRATION_PAIRS = [
    (
        DIR_TESTDATA / "eos_config_minimal_0_1_0.json",
        DIR_TESTDATA / "eos_config_minimal_now.json",
    ),
    (
        DIR_TESTDATA / "eos_config_andreas_0_1_0.json",
        DIR_TESTDATA / "eos_config_andreas_now.json",
    ),
    (
        DIR_TESTDATA / "eos_config_unstripped.json",
        DIR_TESTDATA / "eos_config_stripped.json",
    ),
    # Add more pairs here:
    # (DIR_TESTDATA / "old_config_X.json", DIR_TESTDATA / "expected_config_X.json"),
]

# Any sentinel in expected data
_ANY_SENTINEL = "__ANY__"


def _dict_contains(superset: Any, subset: Any, path="") -> list[str]:
    """Recursively verify that all key-value pairs from a subset dictionary or list exist in a superset.

    Supports nested dictionaries and lists. Extra keys in superset are allowed.
    Numeric values (int/float) are compared with tolerance.

    Args:
        superset (Any): The dictionary or list that should contain all items from `subset`.
        subset (Any): The expected dictionary or list.
        path (str, optional): Current nested path used for error reporting. Defaults to "".

    Returns:
        list[str]: A list of strings describing mismatches or missing keys. Empty list if all subset items are present.
    """
    errors = []

    if isinstance(subset, dict) and isinstance(superset, dict):
        for key, sub_value in subset.items():
            full_path = f"{path}/{key}" if path else key
            if key not in superset:
                errors.append(f"Missing key: {full_path}")
                continue
            errors.extend(_dict_contains(superset[key], sub_value, full_path))

    elif isinstance(subset, list) and isinstance(superset, list):
        for i, elem in enumerate(subset):
            if i >= len(superset):
                full_path = f"{path}[{i}]" if path else f"[{i}]"
                errors.append(f"List too short at {full_path}: expected element {elem}")
                continue
            errors.extend(_dict_contains(superset[i], elem, f"{path}[{i}]" if path else f"[{i}]"))

    else:
        # "__ANY__" in expected means "accept whatever value the migration produces"
        if subset == _ANY_SENTINEL:
            return errors
        # Compare values (with numeric tolerance)
        if isinstance(subset, (int, float)) and isinstance(superset, (int, float)):
            if abs(float(subset) - float(superset)) > 1e-6:
                errors.append(f"Value mismatch at {path}: expected {subset}, got {superset}")
        elif subset != superset:
            errors.append(f"Value mismatch at {path}: expected {subset}, got {superset}")

    return errors


class TestConfigMigration:
    """Tests for migrate_config_file()"""

    @pytest.fixture
    def tmp_config_file(self, config_default_dirs) -> Path:
        """Create a temporary valid config file with an invalid version."""
        config_default_dir_user, _, _, _ = config_default_dirs
        config_file_user = config_default_dir_user.joinpath(ConfigEOS.CONFIG_FILE_NAME)

        # Create a default config object (simulates the latest schema)
        default_config = SettingsEOSDefaults()

        # Dump to JSON
        config_json = json.loads(default_config.model_dump_json())

        # Corrupt the version (simulate outdated config)
        config_json["general"]["version"] = "0.0.0-old"
        config_json["general"].pop("schema_version", None)

        # Write file
        with config_file_user.open("w", encoding="utf-8") as f:
            json.dump(config_json, f, indent=4)

        return config_file_user


    def test_migrate_config_file_from_invalid_version(self, tmp_config_file: Path):
        """Test that migration updates an outdated config version successfully."""
        backup_file = tmp_config_file.with_suffix(".bak")

        # Run migration
        result = configmigrate.migrate_config_file(tmp_config_file, backup_file)

        # Verify success
        assert result is True, "Migration should succeed even from invalid version."

        # Verify backup exists
        assert backup_file.exists(), "Backup file should be created before migration."

        # Verify version updated
        with tmp_config_file.open("r", encoding="utf-8") as f:
            migrated_data = json.load(f)
        assert migrated_data["general"]["version"] == __version__, \
            "Migrated config should have updated version."

        # Verify it still matches the structure of SettingsEOSDefaults
        new_model = SettingsEOSDefaults(**migrated_data)
        assert isinstance(new_model, SettingsEOSDefaults)

    def test_migrate_config_file_already_current(self, tmp_config_file: Path):
        """Test that a current config file returns True immediately."""
        backup_file = tmp_config_file.with_suffix(".bak")

        # Run migration
        result = configmigrate.migrate_config_file(tmp_config_file, backup_file)
        assert result is True, "Migration should succeed even from invalid version."

        backup_file.unlink()
        assert not backup_file.exists()

        result = configmigrate.migrate_config_file(tmp_config_file, backup_file)
        assert result is True
        assert not backup_file.exists(), "No backup should be made if config is already current."


    @pytest.mark.parametrize("old_file, expected_file", MIGRATION_PAIRS)
    def test_migrate_old_version_config(self, tmp_path: Path, old_file: Path, expected_file: Path):
        """Ensure migration from old → new schema produces the expected output."""
        # --- Prepare temporary working file based on expected file name ---
        working_file = tmp_path / (expected_file.stem + ".new")
        shutil.copy(old_file, working_file)

        # Backup file path (inside tmp_path to avoid touching repo files)
        backup_file = tmp_path / f"{old_file.stem}.bak"

        failed = False
        try:
            assert working_file.exists(), f"Working config file is missing: {working_file}"

            # --- Perform migration ---
            result = configmigrate.migrate_config_file(working_file, backup_file)

            # --- Assertions ---
            assert result is True, f"Migration failed for {old_file.name}"

            assert configmigrate.mapped_count >= 1, f"No mapped migrations for {old_file.name}"
            assert configmigrate.auto_count >= 1, f"No automatic migrations for {old_file.name}"

            assert len(configmigrate.skipped_paths) <= 3, (
                f"Too many skipped paths in {old_file.name}: {configmigrate.skipped_paths}"
            )

            assert backup_file.exists(), f"Backup file not created for {old_file.name}"

            # --- Compare migrated result with expected output ---
            old_data = json.loads(old_file.read_text(encoding="utf-8"))
            new_data = json.loads(working_file.read_text(encoding="utf-8"))
            expected_data = json.loads(expected_file.read_text(encoding="utf-8"))

            # Check version
            assert new_data["general"]["version"] == __version__, (
                f"Expected version {__version__}, got {new_data['general']['version']}"
            )

            # Recursive subset comparison
            errors = _dict_contains(new_data, expected_data)
            assert not errors, (
                f"Migrated config for {old_file.name} is missing or mismatched fields:\n" +
                "\n".join(errors) + f"\n{new_data}"
            )

            # --- Compare migrated result with migration map ---
            # Ensure all expected mapped fields are actually migrated and correct
            missing_migrations = []
            mismatched_values = []

            for old_path, mapping in configmigrate.MIGRATION_MAP.items():
                if mapping is None:
                    continue  # skip intentionally dropped fields

                # Determine new path (string or tuple)
                new_path = mapping[0] if isinstance(mapping, tuple) else mapping

                # Get value from expected data (if present)
                expected_value = configmigrate._get_json_nested_value(expected_data, new_path)
                if expected_value is None:
                    continue  # new field not present in expected config

                # Check that migration recorded this old path
                if old_path.strip("/") not in configmigrate.migrated_source_paths:
                    missing_migrations.append(f"{old_path} → {new_path}")
                    continue

                # Verify the migrated value matches the expected one
                new_value = configmigrate._get_json_nested_value(new_data, new_path)
                value_errors = _dict_contains(new_value, expected_value, new_path)

                if value_errors:
                    # Check if this mapping uses _KEEP_DEFAULT and the old value was None/missing
                    old_value = configmigrate._get_json_nested_value(old_data, old_path)
                    keep_default = (
                        isinstance(mapping, tuple)
                        and configmigrate._KEEP_DEFAULT in mapping
                    )

                    if keep_default and old_value is None:
                        continue  # acceptable: old was None, new model keeps its default

                    mismatched_values.extend(value_errors)

            assert not missing_migrations, (
                "Some expected migration map entries were not migrated:\n"
                + "\n".join(missing_migrations)
            )
            assert not mismatched_values, (
                "Migrated values differ from expected results:\n"
                + "\n".join(mismatched_values)
            )

            # Validate migrated config with schema
            new_model = SettingsEOSDefaults(**new_data)
            assert isinstance(new_model, SettingsEOSDefaults)

        except Exception:
            # mark failure and re-raise so pytest records the error and the working_file is kept
            failed = True
            raise
        finally:
            # Remove the .new working file only if the test passed (failed == False)
            if not failed and working_file.exists():
                working_file.unlink(missing_ok=True)


class TestDeviceMigration:
    @pytest.mark.parametrize("as_list", [False, True])
    def test_migration_preserves_lcos_and_input(self, as_list):
        battery = {"device_id": "house", "capacity_wh": 12000,
                   "levelized_cost_of_storage_kwh": 0.123}
        raw = {"devices": {"batteries": [battery] if as_list else {"house": battery}}}
        original = copy.deepcopy(raw)
        migrated = migrate_config_data(raw)
        assert migrated.devices.batteries is not None
        assert migrated.devices.batteries["house"].levelized_cost_of_storage_amt_kwh == 0.123
        assert raw == original

class TestMigrationSafety:
    def test_native_thermal_config_survives_code_version_change(self):
        from test_thermalconfig import _staged_config
        raw = _staged_config()
        raw['general'] = {'version': 'previous-code-build'}
        raw['presence'] = {'history_days': 70, 'absence_delay_seconds': 900}
        original = copy.deepcopy(raw)
        expected = SettingsEOSDefaults.model_validate(raw)
        result = migrate_config_data(raw)
        assert result.thermal.model_dump() == expected.thermal.model_dump()
        assert result.presence.model_dump() == expected.presence.model_dump()
        assert result.devices.model_dump()["heat_sources"] == expected.devices.model_dump()["heat_sources"]
        assert raw == original

    @pytest.mark.parametrize("raw", [
        {"general": {"schema_version": 2}},
        {"general": {"schema_version": True}},
        {"general": {"schema_version": "1"}},
        {"thermal": {"typo_in_setting": True}},
        {"devices": {"batteries": {"b": {"device_id": "b", "unknown_setting": 1}}}},
    ])
    def test_invalid_input_is_not_silently_accepted(self, tmp_path, raw):
        config = tmp_path / "config.json"
        original = json.dumps(raw).encode()
        config.write_bytes(original)
        backup = tmp_path / "backup.json"
        assert not configmigrate.migrate_config_file(config, backup)
        assert config.read_bytes() == original
        assert not backup.exists()

    def test_malformed_json_stays_untouched(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_bytes(b'{"general":')
        assert not configmigrate.migrate_config_file(config, tmp_path / "backup")
        assert config.read_bytes() == b'{"general":'

    def test_current_schema_code_update_does_not_rewrite(self, tmp_path):
        config = tmp_path / "config.json"
        original = b'{"general": {"schema_version": 1, "version": "older-build"}}'
        config.write_bytes(original)
        backup = tmp_path / "backup"
        assert configmigrate.migrate_config_file(config, backup)
        assert config.read_bytes() == original
        assert not backup.exists()

    def test_backup_exact_and_repeat_idempotent(self, tmp_path):
        config = tmp_path / "config.json"
        original = b'{ "general": {"version":"old"} }\n'
        config.write_bytes(original)
        backup = tmp_path / "backup"
        assert configmigrate.migrate_config_file(config, backup)
        assert backup.read_bytes() == original
        migrated = config.read_bytes()
        assert json.loads(migrated)["general"]["schema_version"] == 1
        assert configmigrate.migrate_config_file(config, backup)
        assert config.read_bytes() == migrated
        assert backup.read_bytes() == original

    @pytest.mark.parametrize("failure", ["flush", "replace", "backup", "concurrent_edit"])
    def test_write_failures_keep_original_or_external_edit(self, tmp_path, monkeypatch, failure):
        config = tmp_path / "config.json"
        original = b'{"general":{"version":"old"}}'
        edited = b'{"general":{"version":"external-edit"}}'
        config.write_bytes(original)
        backup = tmp_path / "backup"
        def fail(*args, **kwargs):
            raise OSError("Injected storage failure")
        if failure == "flush":
            monkeypatch.setattr(configmigrate.os, "fsync", fail)
        elif failure == "replace":
            monkeypatch.setattr(configmigrate.os, "replace", fail)
        elif failure == "backup":
            backup.write_bytes(b"existing-backup")
        else:
            original_fsync = configmigrate.os.fsync
            count = 0
            def edit_during_backup(fd):
                nonlocal count
                original_fsync(fd)
                count += 1
                if count == 2:
                    config.write_bytes(edited)
            monkeypatch.setattr(configmigrate.os, "fsync", edit_during_backup)
        assert not configmigrate.migrate_config_file(config, backup)
        assert config.read_bytes() == (edited if failure == "concurrent_edit" else original)
        assert not list(tmp_path.glob(".eos-migrate-*"))
        if failure == "backup":
            assert backup.read_bytes() == b"existing-backup"

    @pytest.mark.parametrize("after_replace", [False, True])
    def test_process_crash_at_commit_boundary(self, tmp_path, after_replace):
        config = tmp_path / "config.json"
        original = b'{"general":{"version":"old"}}'
        config.write_bytes(original)
        backup = tmp_path / "backup"
        code = textwrap.dedent("""
            import os, sys
            from pathlib import Path
            from akkudoktoreos.config import configmigrate
            replace = os.replace
            def crash(source, destination):
                if sys.argv[3] == 'True':
                    replace(source, destination)
                os._exit(73)
            configmigrate.os.replace = crash
            configmigrate.migrate_config_file(Path(sys.argv[1]), Path(sys.argv[2]))
        """)
        env = dict(os.environ, PYTHONPATH=str(Path(configmigrate.__file__).parents[2]))
        result = subprocess.run(
            [sys.executable, "-c", code, str(config), str(backup), str(after_replace)],
            env=env, capture_output=True, timeout=30,
        )
        assert result.returncode == 73, result.stderr.decode(errors="replace")
        assert backup.read_bytes() == original
        loaded = json.loads(config.read_bytes())
        if after_replace:
            assert loaded["general"]["schema_version"] == 1
        else:
            assert config.read_bytes() == original
        # A killed process cannot leave the OS lock held. Retry is safe.
        assert configmigrate.migrate_config_file(config, tmp_path / "retry-backup")

    def test_concurrent_migrator_is_rejected(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text("{}", encoding="utf-8")
        with configmigrate._migration_lock(config):
            assert not configmigrate.migrate_config_file(config, tmp_path / "backup")
        assert config.read_text(encoding="utf-8") == "{}"
        assert configmigrate.migrate_config_file(config, tmp_path / "backup")

    def test_invalid_comfort_bounds_abort_without_replacing(self, tmp_path):
        from test_thermalconfig import _staged_config
        raw = _staged_config()
        raw["thermal"]["zones"]["zone-a"]["comfort"]["min_temperature_c"] = 22.0
        config = tmp_path / "config.json"
        original = json.dumps(raw).encode()
        config.write_bytes(original)
        assert not configmigrate.migrate_config_file(config, tmp_path / "backup")
        assert config.read_bytes() == original

    def test_conflicting_legacy_and_current_fields_abort(self):
        raw = {"feedintariff": {
            "provider_settings": {"FeedInTariffFixed": {"feed_in_tariff_kwh": 0.1}},
            "feedintarifffixed": {"feed_in_tariff_kwh": 0.2},
        }}
        with pytest.raises(ValueError, match="Conflicting"):
            migrate_config_data(raw)
