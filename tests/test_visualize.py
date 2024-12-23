import os
import subprocess
from pathlib import Path

from matplotlib.testing.compare import compare_images

from akkudoktoreos.config import get_working_dir, load_config

filename = "example_report.pdf"

working_dir = get_working_dir()
config = load_config(working_dir)
output_dir = config.working_dir / config.directories.output

# If self.filename is already a valid path, use it; otherwise, combine it with output_dir
if os.path.isabs(filename):
    output_file = filename
else:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = os.path.join(output_dir, filename)

DIR_TESTDATA = Path(__file__).parent / "testdata"
reference_file = DIR_TESTDATA / "test_example_report.pdf"


def test_generate_pdf_main():
    # Delete the old generated file if it exists
    if os.path.isfile(output_file):
        os.remove(output_file)

    # Execute the __main__ block of visualize.py by running it as a script
    script_path = Path(__file__).parent.parent / "src" / "akkudoktoreos" / "utils" / "visualize.py"
    subprocess.run(["python", str(script_path)], check=True)

    # Check if the file exists
    assert os.path.isfile(output_file)

    # Compare the generated file with the reference file
    comparison = compare_images(str(reference_file), str(output_file), tol=0)

    # Assert that there are no differences
    assert comparison is None, f"Images differ: {comparison}"
