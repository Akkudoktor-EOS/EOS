import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from akkudoktoreos.config import configmigrate
from akkudoktoreos.config.config import ConfigEOS, SettingsEOSDefaults
from akkudoktoreos.core.version import __version__

# Test data directory and known migration pairs
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

    def test_migrate_config_file_already_current(self, tmp_path: Path):
        """Test that a current config file returns True immediately."""
        config_path = tmp_path / "EOS_current.json"
        default = SettingsEOSDefaults()
        with config_path.open("w", encoding="utf-8") as f:
            f.write(default.model_dump_json(indent=4))

        backup_file = config_path.with_suffix(".bak")

        result = configmigrate.migrate_config_file(config_path, backup_file)
        assert result is True
        assert not backup_file.exists(), "No backup should be made if config is already current."


    @pytest.mark.parametrize("old_file, expected_file", MIGRATION_PAIRS)
    def test_migrate_old_version_config(self, tmp_path: Path, old_file: Path, expected_file: Path):
        """Ensure migration from old → new schema produces the expected output."""
        # --- Prepare temporary working file based on expected file name ---
        working_file = expected_file.with_suffix(".new")
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
                if new_value != expected_value:
                    # Check if this mapping uses _KEEP_DEFAULT and the old value was None/missing
                    old_value = configmigrate._get_json_nested_value(old_data, old_path)
                    keep_default = (
                        isinstance(mapping, tuple)
                        and configmigrate._KEEP_DEFAULT in mapping
                    )
                    if keep_default and old_value is None:
                        continue  # acceptable: old was None, new model keeps its default
                    mismatched_values.append(
                        f"{old_path} → {new_path}: expected {expected_value!r}, got {new_value!r}"
                    )

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
