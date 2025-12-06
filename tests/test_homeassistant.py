import os
import subprocess

import pytest
import yaml


class TestHomeAssistantAddon:
    """Tests to ensure the repository root is a valid Home Assistant add-on.

    Simulates the Home Assitant Supervisor's expectations.
    """

    @property
    def root(self):
        # Repository root (repo == addon)
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def test_config_yaml_exists(self):
        """Ensure config.yaml exists in the repo root."""
        cfg_path = os.path.join(self.root, "config.yaml")
        assert os.path.isfile(cfg_path), "config.yaml must exist in repository root."

    def test_config_yaml_loadable(self):
        """Verify that config.yaml parses and contains required fields."""
        cfg_path = os.path.join(self.root, "config.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        required_fields = ["name", "version", "slug"]
        for field in required_fields:
            assert field in cfg, f"Missing required field '{field}' in config.yaml."

    def test_docs_md_exists(self):
        """Ensure DOCS.md exists in the repo root (for Home Assistant add-on documentation)."""
        docs_path = os.path.join(self.root, "DOCS.md")
        assert os.path.isfile(docs_path), "DOCS.md must exist in the repository root for add-on documentation."

    @pytest.mark.docker
    def test_dockerfile_exists(self):
        """Ensure Dockerfile exists in the repo root."""
        dockerfile = os.path.join(self.root, "Dockerfile")
        assert os.path.isfile(dockerfile), "Dockerfile must exist in repository root."

    @pytest.mark.docker
    def test_docker_build_context_valid(self):
        """Runs a Docker build using the root of the repo as Supervisor would.

        Fails if the build context is invalid or Dockerfile has syntax errors.
        """
        cmd = ["docker", "build", "-t", "ha-addon-test:latest", self.root]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print("STDOUT:\n", e.stdout)
            print("STDERR:\n", e.stderr)
            pytest.fail("Docker build failed. This simulates a Supervisor build failure.")
