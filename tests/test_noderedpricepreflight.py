"""Run the importable Node-RED example's regression tests when Node.js is available."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from akkudoktoreos.core.pydantic import PydanticDateTimeSeries
from akkudoktoreos.utils.datetimeutil import to_datetime


class TestNodeRedPricePreflight:
    """The Function nodes of docs/_static/nodered-price-preflight.json."""

    def test_nodered_price_preflight(self, tmp_path: Path) -> None:
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node-RED Function-node regression tests require Node.js.")
        start = to_datetime("2026-10-10T00:00:00+02:00", in_timezone="Europe/Berlin")
        series = PydanticDateTimeSeries(
            data={start.add(minutes=15 * i).isoformat(): 0.0001 for i in range(96)},
            dtype="float64",
            tz="Europe/Berlin",
        )
        api_response = tmp_path / "raw-price-series.json"
        api_response.write_text(series.model_dump_json(), encoding="utf-8")
        result = subprocess.run(  # noqa: S603
            [node, "--test", str(Path(__file__).with_name("nodered_price_preflight.cjs"))],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "EOS_PRICE_TEST_SERIES": str(api_response)},
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
