import json
from pathlib import Path

import pytest
from matplotlib.testing.compare import compare_images

from akkudoktoreos.config import AppConfig
from akkudoktoreos.visualize import visualisiere_ergebnisse

DIR_TESTDATA = Path(__file__).parent / "testdata"
DIR_IMAGEDATA = DIR_TESTDATA / "images"


@pytest.mark.parametrize(
    "fn_in, fn_out, fn_out_base",
    [("visualize_input_1.json", "visualize_output_1.pdf", "visualize_base_output_1.pdf")],
)
def test_visualisiere_ergebnisse(fn_in, fn_out, fn_out_base, tmp_config: AppConfig):
    with open(DIR_TESTDATA / fn_in, "r") as f:
        input_data = json.load(f)
    visualisiere_ergebnisse(config=tmp_config, **input_data)
    output_file: Path = tmp_config.working_dir / tmp_config.directories.output / fn_out

    assert output_file.is_file()
    assert (
        compare_images(
            str(output_file),
            str(DIR_IMAGEDATA / fn_out_base),
            0,
        )
        is None
    )
