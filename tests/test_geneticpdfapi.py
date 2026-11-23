"""The report endpoint selects retained results and renders outside the event loop."""

import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import genetic_result_of, genetic_run_parameters
from fastapi.testclient import TestClient
from pypdf import PdfReader

from akkudoktoreos.optimization.genetic import geneticapi, geneticvisualize
from akkudoktoreos.optimization.genetic.geneticapi import GeneticSolution
from akkudoktoreos.optimization.optimizer import OptimizerRun
from akkudoktoreos.server import eos
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def retained_result():
    path = Path(__file__).parent / "testdata/genetic/optimize_result_1.json"
    solution = GeneticSolution.model_validate_json(path.read_text())
    solution.start_solution_datetime = to_datetime("2026-09-16T10:00:00+02:00")
    return genetic_result_of(solution)


def test_missing_algorithm_report_returns_404(config_eos, monkeypatch):
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(optimizer_run=lambda name: None))
    response = TestClient(eos.app).get("/v1/energy-management/optimization/solution/GENETIC/pdf")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_retained_algorithm_result_renders_real_pdf(config_eos, monkeypatch, retained_result):
    solution = retained_result
    run = OptimizerRun(parameters=genetic_run_parameters(solution.request), result=solution)
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(optimizer_run={"GENETIC": run}.get))
    # A later run and changed configuration must not retime the retained report.
    from akkudoktoreos.core.coreabc import get_ems

    get_ems().set_start_datetime(to_datetime("2027-01-01T00:00:00+01:00"))
    config_eos.optimization.genetic.interval_sec = 900
    before = geneticapi.solution_response(solution).model_dump_json()
    response = TestClient(eos.app).get("/v1/energy-management/optimization/solution/GENETIC/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "genetic" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    assert len(PdfReader(BytesIO(response.content)).pages) >= 4
    assert geneticapi.solution_response(solution).model_dump_json() == before


@pytest.mark.asyncio
async def test_render_is_offloaded_and_uses_an_owned_copy(config_eos, monkeypatch, retained_result):
    solution = retained_result
    caller_thread = threading.get_ident()
    captured = []
    run = OptimizerRun(parameters=genetic_run_parameters(solution.request), result=solution)
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(optimizer_run={"GENETIC": run}.get))
    original_controls = list(solution.ac_charge)

    def render(*, parameters, result):
        solution = result
        captured.append((threading.get_ident(), solution))
        solution.ac_charge[0] = 0.123
        return b"%PDF-isolated-render"

    # The PDF stack is imported on first use, so patch it at its source.
    monkeypatch.setattr(geneticvisualize, "genetic_prepare_visualize", render)
    response = await geneticapi.fastapi_energy_management_optimization_solution_genetic_pdf_get(
        latest=run
    )
    assert response.body == b"%PDF-isolated-render"
    assert captured[0][0] != caller_thread
    assert captured[0][1] is not solution
    assert solution.ac_charge == original_controls
