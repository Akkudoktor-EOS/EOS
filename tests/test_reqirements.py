from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import re


def check_package_version(package_spec):
    try:
        # If the package_spec contains "==", it has a version specified
        if "==" in package_spec:
            # Split the package_spec string (e.g., "pytest==8.3.3") into name and version
            package_name, required_version = package_spec.split("==")

            # Check if the package has extras (e.g., 'fastapi[standard]')
            if "[" in package_name:
                # Remove the extras part (everything after '[')
                package_name = re.sub(r"\[.*\]", "", package_name)

            # Get the installed version of the package
            installed_version = version(package_name)

            # Compare the installed version with the required version
            if installed_version == required_version:
                return True  # Package version matches
            else:
                print(
                    f"[ERROR] Version mismatch for {package_name}: installed {installed_version}, required {required_version}"
                )
                return False
        else:
            # If no version is specified, we just check if the package is installed
            # Check if the package has extras (e.g., 'fastapi[standard]')
            if "[" in package_spec:
                # Remove the extras part (everything after '[')
                package_spec = re.sub(r"\[.*\]", "", package_spec)

            try:
                version(package_spec)
                print(f"[INFO] {package_spec} is installed (no version check performed).")
                return True  # No version specified, so we just check if installed
            except PackageNotFoundError:
                print(f"[ERROR] {package_spec} is not installed.")
                return False

    except Exception as e:
        print(f"[ERROR] An error occurred: {e}")
        return False


def test_requirements_versions():
    # Get the project directory (two levels above the current script)
    project_dir = Path(__file__).parent.parent
    print("[INFO] Starting version check...")

    # Define paths to the requirements files
    requirements_files = [project_dir / "requirements.txt", project_dir / "requirements-dev.txt"]

    all_passed = True  # To track if all checks pass

    for req_file in requirements_files:
        if req_file.exists():
            print(f"[INFO] Checking packages in {req_file}...")
            # Read each line in the requirements file
            with open(req_file, "r") as f:
                for line in f:
                    # Skip empty lines, comments, and lines with -r (include another requirements file)
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-r"):
                        print(f"[INFO] Checking package: {line}")
                        if not check_package_version(line):
                            all_passed = False
        else:
            print(f"[ERROR] {req_file} not found.")
            all_passed = False

    print("[INFO] Version check completed.")

    # Assertion to ensure the test fails if any check fails
    assert all_passed, "Some package versions do not match the requirements."
