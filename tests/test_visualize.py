import os
from pathlib import Path

from matplotlib.testing.compare import compare_images

from akkudoktoreos.config.config import get_config
from akkudoktoreos.utils.visualize import generate_example_report

filename = "example_report.pdf"

config = get_config()
output_dir = config.data_output_path
output_dir.mkdir(parents=True, exist_ok=True)
output_file = os.path.join(output_dir, filename)

DIR_TESTDATA = Path(__file__).parent / "testdata"
reference_file = DIR_TESTDATA / "test_example_report.pdf"


def test_generate_pdf_example():
    """Test generation of example visualization report."""
    # Delete the old generated file if it exists
    if os.path.isfile(output_file):
        os.remove(output_file)

    generate_example_report(filename)

    # Check if the file exists
    assert os.path.isfile(output_file)

    # Compare the generated file with the reference file
    comparison = compare_images(str(reference_file), str(output_file), tol=0)

    # Assert that there are no differences
    assert comparison is None, f"Images differ: {comparison}"
