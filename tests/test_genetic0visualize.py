from pathlib import Path

from matplotlib.testing.compare import compare_images

from akkudoktoreos.optimization.genetic0.genetic0visualize import (
    genetic0_generate_example_report,
)

filename = "example_report.pdf"


DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic0"
reference_file = DIR_TESTDATA / "test_example_report.pdf"
reference_png_file = DIR_TESTDATA / "test_example_report_pdf.png"
output_file = DIR_TESTDATA / "test_example_report_new.pdf"
output_png_file = DIR_TESTDATA / "test_example_report_new_pdf.png"



def test_generate_pdf_example(config_eos):
    """Test generation of example visualization report."""
    # Generate PDF
    genetic0_generate_example_report(filename=str(output_file))

    # Check if the file exists
    assert output_file.exists()

    # Compare the generated file with the reference file
    comparison = compare_images(str(reference_file), str(output_file), tol=0)

    # Assert that there are no differences
    assert comparison is None, f"Images differ: {comparison}"

    # Everything passed, remove compare file
    reference_png_file.unlink()
    output_file.unlink()
    output_png_file.unlink()
