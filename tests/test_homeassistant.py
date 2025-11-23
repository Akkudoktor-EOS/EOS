import os
import subprocess
from pathlib import Path
from typing import Optional

import pytest
import yaml
from pydantic import ValidationError


class TestHomeAssistantAddon:
    """Tests to ensure the repository root is a valid Home Assistant add-on.
    Simulates the Home Assistant Supervisor's expectations.
    """

    @property
    def root(self):
        """Repository root (repo == addon)."""
        return Path(__file__).resolve().parent.parent

    def test_config_yaml_exists(self):
        """Ensure config.yaml exists in the repo root."""
        cfg_path = self.root / "config.yaml"
        assert cfg_path.is_file(), "config.yaml must exist in repository root."

    def test_config_yaml_loadable(self):
        """Verify that config.yaml parses and contains required fields."""
        cfg_path = self.root / "config.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        required_fields = ["name", "version", "slug", "description", "arch"]
        for field in required_fields:
            assert field in cfg, f"Missing required field '{field}' in config.yaml."

        # Additional validation
        assert isinstance(cfg["arch"], list), "arch must be a list"
        assert len(cfg["arch"]) > 0, "arch list cannot be empty"

        print(f"✓ config.yaml valid:")
        print(f"  Name: {cfg['name']}")
        print(f"  Version: {cfg['version']}")
        print(f"  Slug: {cfg['slug']}")
        print(f"  Architectures: {', '.join(cfg['arch'])}")

    def test_readme_exists(self):
        """Ensure README.md exists and is not empty."""
        readme_path = self.root / "README.md"
        assert readme_path.is_file(), "README.md must exist in the repository root."

        content = readme_path.read_text()
        assert len(content.strip()) > 0, "README.md is empty"

        print(f"✓ README.md exists ({len(content)} bytes)")

    def test_docs_md_exists(self):
        """Ensure DOCS.md exists in the repo root (for Home Assistant add-on documentation)."""
        docs_path = self.root / "DOCS.md"
        assert docs_path.is_file(), "DOCS.md must exist in the repository root for add-on documentation."

        content = docs_path.read_text()
        assert len(content.strip()) > 0, "DOCS.md is empty"

        print(f"✓ DOCS.md exists ({len(content)} bytes)")

    @pytest.mark.docker
    def test_dockerfile_exists(self):
        """Ensure Dockerfile exists in the repo root and has basic structure."""
        dockerfile = self.root / "Dockerfile"
        assert dockerfile.is_file(), "Dockerfile must exist in repository root."

        content = dockerfile.read_text()

        # Check for FROM statement
        assert "FROM" in content, "Dockerfile must contain FROM statement"

        # Check for common add-on patterns
        if "ARG BUILD_FROM" in content:
            print("✓ Dockerfile uses Home Assistant build args")

        print("✓ Dockerfile exists and has valid structure")

    @pytest.mark.docker
    def test_docker_build_context_valid(self):
        """Runs a Docker build using the root of the repo as Home Assistant supervisor would.
        Fails if the build context is invalid or Dockerfile has syntax errors.
        """
        # Check if Docker is available
        try:
            subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                check=True
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pytest.skip("Docker not found or not running")

        cmd = [
            "docker", "build",
            "-t", "ha-addon-test:latest",
            str(self.root),
        ]

        print(f"\nBuilding Docker image from: {self.root}")

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                cwd=str(self.root)
            )
            print("✓ Docker build successful")
            if result.stdout:
                print("\nBuild output (last 20 lines):")
                print('\n'.join(result.stdout.splitlines()[-20:]))
        except subprocess.CalledProcessError as e:
            print("\n✗ Docker build failed")
            print("\nSTDOUT:")
            print(e.stdout)
            print("\nSTDERR:")
            print(e.stderr)
            pytest.fail(
                f"Docker build failed with exit code {e.returncode}. "
                "This simulates a Supervisor build failure."
            )

    @pytest.mark.docker
    def test_addon_builder_validation(self, is_finalize: bool):
        """Validate add-on can be built using Home Assistant's builder tool.

        This is the closest to what Supervisor does when installing an add-on.
        """
        if not is_finalize:
            pytest.skip("Skipping add-on builder validation test — not full run")

        # Check if Docker is available
        try:
            subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                check=True
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pytest.skip("Docker not found or not running")

        print(f"\nValidating add-on with builder: {self.root}")

        # Read config to get architecture info
        cfg_path = self.root / "config.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        # Detect host architecture
        import platform
        machine = platform.machine().lower()

        # Map Python's platform names to Home Assistant architectures
        arch_map = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "aarch64": "aarch64",
            "arm64": "aarch64",
            "armv7l": "armv7",
            "armv7": "armv7",
        }

        host_arch = arch_map.get(machine, "amd64")

        # Check if config supports this architecture
        if host_arch not in cfg["arch"]:
            pytest.skip(
                f"Add-on doesn't support host architecture {host_arch}. "
                f"Supported: {', '.join(cfg['arch'])}"
            )

        print(f"Using builder for architecture: {host_arch}")

        # The builder expects specific arguments for building
        builder_image = f"ghcr.io/home-assistant/{host_arch}-builder:latest"
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--privileged",
                "-v", f"{self.root}:/data",
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                builder_image,
                "--generic", cfg["version"],
                "--target", "/data",
                f"--{host_arch}",
                "--test"
            ],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            check=False,
            timeout=600
        )

        # Print output for debugging
        if result.stdout:
            print("\nBuilder stdout:")
            print(result.stdout)
        if result.stderr:
            print("\nBuilder stderr:")
            print(result.stderr)

        # Check result
        if result.returncode != 0:
            # Check if it's just because the builder tool is unavailable
            if "exec format error" in result.stderr or "not found" in result.stderr:
                pytest.fail(
                    "Builder tool not compatible with this system."
                )

            pytest.fail(
                f"Add-on builder validation failed with exit code {result.returncode}"
            )

        print("✓ Add-on builder validation passed")

    def test_build_yaml_if_exists(self):
        """If build.yaml exists, validate its structure."""
        build_path = self.root / "build.yaml"

        if not build_path.exists():
            pytest.skip("build.yaml not present (optional)")

        with open(build_path) as f:
            build_cfg = yaml.safe_load(f)

        assert "build_from" in build_cfg, "build.yaml must contain 'build_from'"
        assert isinstance(build_cfg["build_from"], dict), "'build_from' must be a dictionary"

        print("✓ build.yaml structure valid")
        print(f"  Architectures defined: {', '.join(build_cfg['build_from'].keys())}")

    def test_addon_configuration_complete(self):
        """Comprehensive validation of add-on configuration.
        Checks all required fields and common configuration issues.
        """
        cfg_path = self.root / "config.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        # Required top-level fields
        required_fields = ["name", "version", "slug", "description", "arch"]
        for field in required_fields:
            assert field in cfg, f"Missing required field: {field}"

        # Validate specific fields
        assert isinstance(cfg["arch"], list), "arch must be a list"
        assert len(cfg["arch"]) > 0, "arch list cannot be empty"

        valid_archs = ["aarch64", "amd64", "armhf", "armv7", "i386"]
        for arch in cfg["arch"]:
            assert arch in valid_archs, f"Invalid architecture: {arch}"

        # Validate version format (should be semantic versioning)
        version = cfg["version"]
        assert isinstance(version, str), "version must be a string"

        # Validate slug (lowercase, no special chars except dash)
        slug = cfg["slug"]
        assert slug.islower() or "-" in slug, "slug should be lowercase"
        assert slug.replace("-", "").replace("_", "").isalnum(), \
            "slug should only contain alphanumeric characters, dash, or underscore"

        # Optional but common fields
        if "startup" in cfg:
            valid_startup = ["initialize", "system", "services", "application", "once"]
            assert cfg["startup"] in valid_startup, \
                f"Invalid startup value: {cfg['startup']}"

        if "boot" in cfg:
            valid_boot = ["auto", "manual"]
            assert cfg["boot"] in valid_boot, f"Invalid boot value: {cfg['boot']}"

        # Validate ingress configuration
        if cfg.get("ingress"):
            assert "ingress_port" in cfg, "ingress_port required when ingress is enabled"

            ingress_port = cfg["ingress_port"]
            assert isinstance(ingress_port, int), "ingress_port must be an integer"
            assert 1 <= ingress_port <= 65535, "ingress_port must be a valid port number"

            # Ingress port should NOT be in ports section
            ports = cfg.get("ports", {})
            port_key = f"{ingress_port}/tcp"
            assert port_key not in ports, \
                f"Port {ingress_port} is used for ingress and should not be in 'ports' section"

        # Validate URL if present
        if "url" in cfg:
            url = cfg["url"]
            assert url.startswith("http://") or url.startswith("https://"), \
                "URL must start with http:// or https://"

        # Validate map directories if present
        if "map" in cfg:
            assert isinstance(cfg["map"], list), "map must be a list"
            valid_mappings = ["config", "ssl", "addons", "backup", "share", "media"]
            for mapping in cfg["map"]:
                # Handle both "config:rw" and "config" formats
                base_mapping = mapping.split(":")[0]
                assert base_mapping in valid_mappings, \
                    f"Invalid map directory: {base_mapping}"

        print("✓ Add-on configuration validation passed")
        print(f"  Name: {cfg['name']}")
        print(f"  Version: {cfg['version']}")
        print(f"  Slug: {cfg['slug']}")
        print(f"  Architectures: {', '.join(cfg['arch'])}")
        if "startup" in cfg:
            print(f"  Startup: {cfg['startup']}")
        if cfg.get("ingress"):
            print(f"  Ingress: enabled on port {cfg['ingress_port']}")

    def test_ingress_configuration_consistent(self):
        """If ingress is enabled, ensure port configuration is correct."""
        cfg_path = self.root / "config.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        if not cfg.get("ingress"):
            pytest.skip("Ingress not enabled")

        # If ingress is enabled, check configuration
        assert "ingress_port" in cfg, "ingress_port must be specified when ingress is enabled"

        ingress_port = cfg["ingress_port"]

        # The ingress port should NOT be in the ports section
        ports = cfg.get("ports", {})
        port_key = f"{ingress_port}/tcp"

        if port_key in ports:
            pytest.fail(
                f"Port {ingress_port} is used for ingress but also listed in 'ports' section. "
                f"Remove it from 'ports' to avoid conflicts."
            )

        print(f"✓ Ingress configuration valid (port {ingress_port})")
