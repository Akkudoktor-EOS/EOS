from pathlib import Path

from matplotlib.testing.compare import compare_images

from akkudoktoreos.utils.visualize import generate_example_report

filename = "example_report.pdf"


DIR_TESTDATA = Path(__file__).parent / "testdata"
reference_file = DIR_TESTDATA / "test_example_report.pdf"


def test_generate_pdf_example(config_eos):
    """Test generation of example visualization report."""
    output_dir = config_eos.data_output_path
    assert output_dir is not None
    output_file = output_dir / filename
    assert not output_file.exists()

    # Generate PDF
    generate_example_report()

    # Check if the file exists
    assert output_file.exists()

    # Compare the generated file with the reference file
    comparison = compare_images(str(reference_file), str(output_file), tol=0)

    # Assert that there are no differences
    assert comparison is None, f"Images differ: {comparison}"
