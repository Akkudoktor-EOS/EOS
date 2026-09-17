"""Semantic report tests with synthetic data; no forecast provider or live EMS run."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pendulum
import pytest
from pypdf import PdfReader

from akkudoktoreos.optimization.genetic import geneticvisualize as visualize
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.genetic.terminalvalue import (
    TailDiagnostics,
    TailPlanSlot,
    TerminalValueCurve,
    TerminalValueResult,
)


@pytest.fixture
def report_solution():
    start = pendulum.datetime(2026, 10, 25, 1, 45, tz="Europe/Berlin")
    energy = [100.0, 150.0, 200.0, 250.0]
    result = SimpleNamespace(
        load_wh_per_hour=energy,
        home_appliance_wh_per_hour=energy,
        grid_feed_in_wh_per_hour=energy,
        grid_consumption_wh_per_hour=energy,
        losses_per_hour=[1.0] * 4,
        battery_soc_per_hour=[20.0, 25.0, 30.0, 35.0],
        ev_soc_per_hour=[0.0] * 4,
        costs_per_hour=[0.03] * 4,
        revenue_per_hour=[0.01] * 4,
        total_costs=0.12,
        total_revenue=0.04,
        total_balance=0.08,
        home_appliance_energy_wh={"water-heater": energy},
    )
    return SimpleNamespace(
        parameters=SimpleNamespace(
            ems=SimpleNamespace(
                total_load=energy,
                pv_forecast_wh=energy,
                feed_in_tariff_per_wh=[0.00007] * 4,
                electricity_price_per_wh=[0.0003] * 4,
            ),
            temperature_forecast=[12.0] * 4,
        ),
        interval_seconds=900,
        start_solution_datetime=start,
        controls_start_at_now=True,
        start_hour=1,
        ac_charge=[0.0, 0.25, 0.5, 1.0],
        dc_charge=[1.0] * 4,
        discharge_allowed=[0, 0, 1, 1],
        battery_grid_export_allowed=[0, 0, 0, 1],
        battery_grid_export_factor=[0.0, 0.0, 0.0, 0.5],
        result=result,
        extra_data=None,
        fitness_history=None,
        fixed_seed=None,
        appliance_starts={"water-heater": [start.add(minutes=15)]},
        appliance_deadline_missed={"water-heater": True},
        terminal_value=TerminalValueResult(
            mode="TAIL", control_horizon_hours=1, requested_tail_hours=2,
            effective_tail_hours=0.25, tail_end_hour=1.25,
            battery_energy_wh=500, credited_euro=0.13,
            tail_operating_euro=0.1, continuation_value_euro=0.03,
            continuation_mode="AUTO", reason="forecast gap limits tail",
            curve=TerminalValueCurve(energy_wh=[0, 1000], value_euro=[0, 0.26]),
            tail_diagnostics=TailDiagnostics(slots=1, slot_hours=0.25),
            tail_plan=[TailPlanSlot(
                slot=0, hour_from_start=1, action="discharge",
                soc_start_percentage=40, soc_end_percentage=35,
                pv_wh=0, load_wh=100, grid_import_wh=0, grid_export_wh=0,
                battery_charge_wh=0, battery_discharge_wh=100,
                import_price_euro_per_kwh=0.3, feed_in_tariff_euro_per_kwh=0.07,
                slot_value_euro=0.03, remaining_value_euro=0.1,
                ac_charge_factor=0, dc_charge_allowed=1, discharge_allowed=1,
                battery_grid_export_factor=0,
            )],
        ),
    )


@pytest.mark.parametrize("interval_seconds", [900, 3600])
def test_report_preserves_run_timing_energy_and_export_controls(
    config_eos, monkeypatch, report_solution, interval_seconds
):
    report_solution.interval_seconds = interval_seconds
    captured = []
    original = visualize.GeneticVisualizationReport.create_line_chart_date

    def capture(self, start_date, y_list, **kwargs):
        captured.append((start_date, y_list, kwargs, self.interval_seconds))
        return original(self, start_date, y_list, **kwargs)

    monkeypatch.setattr(visualize.GeneticVisualizationReport, "create_line_chart_date", capture)
    monkeypatch.setattr(visualize, "get_ems", lambda: pytest.fail("Snapshot must own report timing"))
    pdf = visualize.genetic_prepare_visualize(report_solution)
    assert pdf.startswith(b"%PDF-")
    assert all(row[0] == report_solution.start_solution_datetime for row in captured)
    assert all(row[3] == interval_seconds for row in captured)
    load = next(row for row in captured if row[2].get("title") == "Load Profile")
    assert load[1] == [[100, 150, 200, 250]]  # No repeated wall-clock trimming or Wh scaling.
    controls = next(row for row in captured if row[2].get("title") == "Executable Battery Controls")
    assert controls[1][-1] == [0, 0, 0, 0.5]
    assert len(controls[1]) == 5
    text = " ".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    for expected in (
        "Energy Flow per Interval", "water-heater", "Deadline missed: True",
        "forecast gap limits tail", "effective tail: 0.25 h", "0.13 EUR",
        "not executable controls", "not realized revenue", "Residual Battery Value Curve",
    ):
        assert expected in text
    assert "Energy Flow per Hour" not in text


def test_short_quarterhour_chart_has_elapsed_fractional_hours(config_eos):
    report = visualize.GeneticVisualizationReport(interval_seconds=900)
    start = pendulum.datetime(2026, 10, 25, 2, 45, tz="Europe/Berlin", fold=0)
    report.create_line_chart_date(start, [[100.0, 200.0, 300.0, 400.0]], ylabel="Wh")
    fig, axis = plt.subplots()
    try:
        report.current_group[0]()
        dates = mdates.num2date(axis.lines[0].get_xdata())
        assert np.diff([value.timestamp() for value in dates]).tolist() == [900] * 3
        assert np.asarray(axis.lines[0].get_ydata()).tolist() == [100, 200, 300, 400]
        assert [label.get_text() for label in fig.axes[1].get_xticklabels()] == ["0", "0.25", "0.5", "0.75"]
    finally:
        plt.close(fig)


def test_empty_and_mismatched_date_series(config_eos):
    report = visualize.GeneticVisualizationReport()
    start = pendulum.now("UTC")
    report.create_line_chart_date(start, [[]], ylabel="Wh")
    assert report.current_group == []
    with pytest.raises(ValueError, match="same intervals"):
        report.create_line_chart_date(start, [[1.0, 2.0], [1.0]], ylabel="Wh")


def test_existing_hourly_solution_renders_without_terminal_metadata(config_eos, monkeypatch):
    payload = (Path(__file__).parent / "testdata/genetic/optimize_result_1.json").read_text()
    solution = GeneticSolution.model_validate_json(payload)
    assert not solution.controls_start_at_now
    assert solution.terminal_value is None
    start = pendulum.datetime(2026, 9, 16, solution.start_hour, tz="UTC")
    monkeypatch.setattr(visualize, "get_ems", lambda: SimpleNamespace(start_datetime=start))
    pdf = visualize.genetic_prepare_visualize(solution)
    assert len(PdfReader(BytesIO(pdf)).pages) >= 4


def test_fixed_fallback_does_not_invent_tail_charts(config_eos, report_solution):
    report_solution.terminal_value = TerminalValueResult(
        mode="FIXED", reason="No complete tail forecast", credited_euro=0.05,
        battery_energy_wh=500, requested_tail_hours=24,
    )
    report_solution.result.home_appliance_energy_wh = {}
    pdf = visualize.genetic_prepare_visualize(report_solution)
    text = " ".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    assert "No complete tail forecast" in text
    assert "effective tail: 0 h" in text
    assert "Deadline missed: True" in text  # Report unscheduled consumers even with no energy series.
    assert "Residual Battery Value Curve" not in text
    assert "Tail Lookahead:" not in text


def test_tail_chart_starts_after_control_horizon(config_eos, report_solution):
    report = visualize.GeneticVisualizationReport(interval_seconds=900)
    start = report_solution.start_solution_datetime
    visualize._add_solution_diagnostics(report, report_solution, start)
    fig, axis = plt.subplots()
    try:
        report.groups[-1][0]()
        expected = mdates.date2num(start.add(hours=1))
        assert np.asarray(axis.lines[0].get_xdata()).tolist() == [expected]
        assert np.asarray(axis.lines[3].get_ydata()).tolist() == [100]
        assert all(line.get_marker() == "o" for line in axis.lines)
        left, right = axis.get_xlim()
        assert left < expected < right
        assert (right - left) * 86400 == pytest.approx(900)
        assert "Not Executable" in axis.get_title()
        assert report_solution.discharge_allowed == [0, 0, 1, 1]
    finally:
        plt.close(fig)


@pytest.mark.parametrize("tariff", [0.00007, [0.00007] * 6])
def test_pdf_clips_forecast_tail_and_accepts_historical_diagnostics(
    config_eos, monkeypatch, report_solution, tariff
):
    report_solution.parameters.ems.total_load = [100.0] * 6
    report_solution.parameters.ems.pv_forecast_wh = [200.0] * 6
    report_solution.parameters.ems.feed_in_tariff_per_wh = tariff
    report_solution.extra_data = {
        "verluste": [1.0, 2.0, 3.0], "bilanz": [0.1, 0.2, 0.3],
        "nebenbedingung": [0.0, 0.0, 0.0],
    }
    captured = []
    original = visualize.GeneticVisualizationReport.create_line_chart_date

    def capture(self, start_date, y_list, **kwargs):
        captured.append((y_list, kwargs))
        return original(self, start_date, y_list, **kwargs)

    monkeypatch.setattr(visualize.GeneticVisualizationReport, "create_line_chart_date", capture)
    pdf = visualize.genetic_prepare_visualize(report_solution)
    assert pdf.startswith(b"%PDF-")
    tariff_plot = next(series for series, kwargs in captured if kwargs.get("title") == "Remuneration")
    assert np.asarray(tariff_plot[0]).tolist() == [0.00007] * 4
    assert all(len(series[0]) == 4 for series, _ in captured)
    assert "verluste" in report_solution.extra_data  # Rendering must not mutate the retained run.
