"""The report endpoint selects retained results and renders outside the event loop."""

import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.server import eos
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def retained_result():
    path = Path(__file__).parent / "testdata/genetic/optimize_result_1.json"
    solution = GeneticSolution.model_validate_json(path.read_text())
    solution.start_solution_datetime = to_datetime("2026-09-16T10:00:00+02:00")
    return solution


def test_missing_algorithm_report_returns_404(config_eos, monkeypatch):
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(genetic_solution=lambda: None))
    response = TestClient(eos.app).get("/v1/energy-management/optimization/solution/GENETIC/pdf")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_retained_algorithm_result_renders_real_pdf(config_eos, monkeypatch, retained_result):
    solution = retained_result
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(genetic_solution=lambda: solution))
    # A later run and changed configuration must not retime the retained report.
    from akkudoktoreos.core.coreabc import get_ems
    from akkudoktoreos.optimization.genetic import geneticvisualize

    get_ems().set_start_datetime(to_datetime("2027-01-01T00:00:00+01:00"))
    config_eos.optimization.genetic.interval_sec = 900
    monkeypatch.setattr(
        geneticvisualize, "get_ems", lambda: pytest.fail("Report must retain its own time grid")
    )
    before = solution.model_dump_json()
    response = TestClient(eos.app).get("/v1/energy-management/optimization/solution/GENETIC/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "genetic" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    assert len(PdfReader(BytesIO(response.content)).pages) >= 4
    assert solution.model_dump_json() == before


@pytest.mark.asyncio
async def test_render_is_offloaded_and_uses_an_owned_copy(config_eos, monkeypatch, retained_result):
    solution = retained_result
    caller_thread = threading.get_ident()
    captured = []
    monkeypatch.setattr(
        eos,
        "get_ems",
        lambda: SimpleNamespace(
            genetic_solution=lambda: solution, genetic0_solution=lambda: solution
        ),
    )
    original_controls = list(solution.ac_charge)

    def render(*, solution):
        captured.append((threading.get_ident(), solution))
        solution.ac_charge[0] = 0.123
        return b"%PDF-isolated-render"

    monkeypatch.setattr(eos, "genetic_prepare_visualize", render)
    response = await eos.fastapi_energy_management_optimization_solution_genetic_pdf_get()
    assert response.body == b"%PDF-isolated-render"
    assert captured[0][0] != caller_thread
    assert captured[0][1] is not solution
    assert solution.ac_charge == original_controls
