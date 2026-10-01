"""Tests for the SciPy-free evaluation of the self-consumption probability tables."""

import io
import os
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.interpolate import RegularGridInterpolator

from akkudoktoreos.utils.gridinterpolator import (
    LinearGridInterpolator,
    load_grid_interpolator,
)

DATA_DIR = Path(__file__).parent.parent / "src" / "akkudoktoreos" / "data"
TABLES = [
    DATA_DIR / "regular_grid_interpolator.pkl",
    DATA_DIR / "genetic0_load_interpolator.pkl",
]


def _reference(grid, values, point, fill_value):
    """SciPy's linear 2-D evaluation, written out in plain (unfused) float arithmetic."""
    x0, x1 = (float(c) for c in point)
    if np.isnan(x0) or np.isnan(x1):
        return float("nan")
    if not (grid[0][0] <= x0 <= grid[0][-1] and grid[1][0] <= x1 <= grid[1][-1]):
        return float(fill_value)
    corners = []
    for axis, x in zip(grid, (x0, x1)):
        i = int(np.searchsorted(axis, x, side="right")) - 1
        i = min(max(i, 0), len(axis) - 2)
        corners.append((i, (x - float(axis[i])) / (float(axis[i + 1]) - float(axis[i]))))
    (i0, y0), (i1, y1) = corners
    v = values
    result = 0.0
    result = result + float(v[i0, i1]) * (1 - y0) * (1 - y1)
    result = result + float(v[i0, i1 + 1]) * (1 - y0) * y1
    result = result + float(v[i0 + 1, i1]) * y0 * (1 - y1)
    result = result + float(v[i0 + 1, i1 + 1]) * y0 * y1
    return result


def _points(grid, n=20000, seed=0):
    rng = np.random.default_rng(seed)
    g0, g1 = grid
    random = np.column_stack(
        [
            rng.uniform(g0[0] - 100, g0[-1] + 100, n),
            rng.uniform(g1[0] - 100, g1[-1] + 100, n),
        ]
    )
    nodes = np.array(np.meshgrid(g0, g1, indexing="ij")).reshape(2, -1).T
    # Query pattern of the EOS interpolator: one mean load, PV steps of 50 W.
    pattern = np.vstack(
        [
            np.column_stack(
                [np.full(int(g1[-1] // 50) + 1, load), np.arange(0.0, g1[-1] + 1.0, 50.0)]
            )
            for load in rng.uniform(g0[0], g0[-1], 50)
        ]
    )
    edges = np.array([[g0[0], g1[0]], [g0[-1], g1[-1]], [g0[-1], g1[0]], [np.nan, g1[0]]])
    return np.vstack([random, nodes, pattern, edges])


@pytest.mark.parametrize("table", TABLES, ids=lambda path: path.name)
def test_loaded_table_matches_scipy(table: Path):
    with table.open("rb") as file:
        scipy_interpolator = pickle.load(file)  # noqa: S301 - shipped data file
    interpolator = load_grid_interpolator(table)

    for ours, theirs in zip(interpolator.grid, scipy_interpolator.grid):
        np.testing.assert_array_equal(ours, theirs)
    np.testing.assert_array_equal(interpolator.values, scipy_interpolator.values)
    assert interpolator.fill_value == scipy_interpolator.fill_value

    points = _points(interpolator.grid)
    ours = interpolator(points)
    theirs = scipy_interpolator(points)
    # Bit-identical to SciPy builds without fused multiply-add (Linux wheels);
    # builds that fuse it (macOS arm64) differ only by rounding (|diff| <= ~1e-16).
    np.testing.assert_array_equal(np.isnan(ours), np.isnan(theirs))
    finite = ~np.isnan(ours)
    np.testing.assert_allclose(ours[finite], theirs[finite], rtol=0, atol=1e-15)

    # Exactly SciPy's arithmetic, evaluated without any fusion.
    sample = points[:: max(1, len(points) // 3000)]
    expected = np.array(
        [
            _reference(interpolator.grid, interpolator.values, point, interpolator.fill_value)
            for point in sample
        ]
    )
    np.testing.assert_array_equal(interpolator(sample), expected)


def test_interpolator_matches_scipy_semantics_on_small_grid():
    grid = (np.array([0.0, 1.0, 3.0]), np.array([10.0, 20.0]))
    values = np.array([[1.0, 2.0], [3.0, 5.0], [-1.0, 0.5]])
    ours = LinearGridInterpolator(grid, values, fill_value=0)
    theirs = RegularGridInterpolator(grid, values, bounds_error=False, fill_value=0)
    points = np.array(
        [[0.0, 10.0], [3.0, 20.0], [2.5, 12.5], [-0.1, 15.0], [1.0, 20.1], [np.nan, 10.0]]
    )
    np.testing.assert_allclose(ours(points)[:-1], theirs(points)[:-1], rtol=0, atol=1e-15)
    assert np.isnan(ours(points)[-1])
    assert ours(points)[3] == 0.0
    assert ours(np.array([[2.5, 12.5]])).shape == (1,)
    assert ours(np.zeros((3, 4, 2))).shape == (3, 4)


def test_interpolator_rejects_invalid_grids():
    with pytest.raises(ValueError):
        LinearGridInterpolator((np.array([0.0]), np.array([0.0, 1.0])), np.zeros((1, 2)))
    with pytest.raises(ValueError):
        LinearGridInterpolator((np.array([1.0, 0.0]), np.array([0.0, 1.0])), np.zeros((2, 2)))
    with pytest.raises(ValueError):
        LinearGridInterpolator((np.array([0.0, 1.0]), np.array([0.0, 1.0])), np.zeros((3, 2)))


def test_loader_rejects_foreign_pickles():
    with pytest.raises(pickle.UnpicklingError):
        load_grid_interpolator(io.BytesIO(pickle.dumps(os.system)))
    with pytest.raises(pickle.UnpicklingError):
        load_grid_interpolator(io.BytesIO(pickle.dumps({"a": 1})))
    method_nearest = RegularGridInterpolator(
        (np.array([0.0, 1.0]), np.array([0.0, 1.0])), np.zeros((2, 2)), method="nearest"
    )
    with pytest.raises(pickle.UnpicklingError):
        load_grid_interpolator(io.BytesIO(pickle.dumps(method_nearest)))


def test_loading_the_tables_does_not_import_scipy():
    code = (
        "import sys\n"
        "from akkudoktoreos.utils.gridinterpolator import load_grid_interpolator\n"
        + "".join(f"load_grid_interpolator({str(table)!r})([[500.0, 100.0]])\n" for table in TABLES)
        + "print('scipy' in sys.modules)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip().splitlines()[-1] == "False"
