from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest
from matplotlib.testing.compare import compare_images

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic0 import genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0optimizer import Genetic0Optimizer
from akkudoktoreos.optimization.genetic0.genetic0visualize import (
    genetic0_generate_example_report,
    genetic0_prepare_visualize,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prediction_hours, start",
    [
        (6, "2026-08-01T00:00:00+00:00"),  # fewer than 12 hours shown
        (30, "2026-08-01T14:00:00+00:00"),  # not the 48 hours the labels once assumed
    ],
)
async def test_report_of_a_run_that_is_not_48_hours_long(config_eos, prediction_hours, start):
    """The report shows a run of any length the run accepts."""
    config_eos.merge_settings_from_dict(
        {
            "general": {"timezone_override": "UTC"},
            "prediction": {"hours": prediction_hours},
            "optimization": {"genetic0": {"individuals": 10, "generations": 10, "seed": 1}},
            "elecfee": {"provider": None},
            "devices": {
                "max_batteries": 0,
                "max_electric_vehicles": 0,
                "max_inverters": 0,
                "max_home_appliances": 0,
            },
        }
    )
    get_ems().set_start_datetime(to_datetime(start, in_timezone="UTC"))

    async def read_array(*, start_datetime, end_datetime, interval, **kwargs):
        slots = int((end_datetime - start_datetime).total_seconds() / interval.total_seconds())
        return np.full(slots, 0.0003)

    prediction = Mock(
        update_data=AsyncMock(),
        key_to_array=AsyncMock(side_effect=read_array),
        key_to_raw_series=AsyncMock(side_effect=KeyError("no tariff")),
        record_keys=[key for key, _ in genetic0prepare.PRESENTATION_PREDICTIONS],
    )
    optimizer = Genetic0Optimizer()
    with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
        parameters = await optimizer.prepare(
            get_ems().start_datetime, get_ems().observation_datetime, None
        )
    result = optimizer.run(parameters)

    assert genetic0_prepare_visualize(parameters, result).startswith(b"%PDF-")
