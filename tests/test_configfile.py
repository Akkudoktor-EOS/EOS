import json
from pathlib import Path
from unittest.mock import patch

import pytest

from akkudoktoreos.config.config import ConfigEOS, GeneralSettings


class TestConfigEOSToConfigFile:

    def test_to_config_file_writes_file(self, config_eos):
        config_path = config_eos.general.config_file_path

        # Remove file to test writing
        config_path.unlink(missing_ok=True)

        config_eos.to_config_file()

        assert config_path.exists()
        assert config_path.read_text().strip().startswith("{")

    def test_to_config_file_excludes_computed_fields(self, config_eos):
        config_path = config_eos.general.config_file_path

        config_eos.to_config_file()
        data = json.loads(config_path.read_text())

        assert "timezone" not in data["general"]
        assert "data_output_path" not in data["general"]
        assert "config_folder_path" not in data["general"]
        assert "config_file_path" not in data["general"]

    def test_to_config_file_excludes_defaults(self, config_eos):
        """Ensure fields with default values are excluded when saving config."""

        # Pick fields that have defaults
        default_latitude = GeneralSettings.model_fields["latitude"].default
        default_longitude = GeneralSettings.model_fields["longitude"].default

        # Ensure fields are at default values
        config_eos.general.latitude = default_latitude
        config_eos.general.longitude = default_longitude

        # Save the config using the correct path managed by config_eos
        config_eos.to_config_file()

        # Read back JSON from the correct path
        config_file_path = config_eos.general.config_file_path
        content = json.loads(config_file_path.read_text(encoding="utf-8"))

        # Default fields should not appear
        assert "latitude" not in content["general"]
        assert "longitude" not in content["general"]

        # Non-default value should appear
        config_eos.general.latitude = 48.0
        config_eos.to_config_file()
        content = json.loads(config_file_path.read_text(encoding="utf-8"))
        assert content["general"]["latitude"] == 48.0

    def test_to_config_file_excludes_none_fields(self, config_eos):
        config_eos.general.latitude = None

        config_path = config_eos.general.config_file_path
        config_eos.to_config_file()

        data = json.loads(config_path.read_text())

        assert "latitude" not in data["general"]

    def test_to_config_file_includes_version(tmp_path, config_eos):
        """Ensure general.version is always included."""
        # Save config
        config_eos.to_config_file()

        # Read back JSON
        config_file_path = config_eos.general.config_file_path
        content = json.loads(config_file_path.read_text(encoding="utf-8"))

        # Assert 'version' is included even if default
        assert content["general"]["version"] == config_eos.general.version

    def test_to_config_file_roundtrip(self, config_eos):
        config_eos.merge_settings_from_dict(
            {
                "general": {"latitude": 48.0},
                "server": {"port": 9000},
            }
        )

        config_path = config_eos.general.config_file_path
        config_eos.to_config_file()

        raw_data = json.loads(config_path.read_text())
        reloaded = ConfigEOS.model_validate(raw_data)

        assert reloaded.general.latitude == 48.0
        assert reloaded.server.port == 9000
