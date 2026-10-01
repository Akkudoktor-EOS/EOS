"""Linear interpolation on a 2-D rectilinear grid without SciPy.

The self-consumption probability tables shipped in ``akkudoktoreos/data`` are
pickled ``scipy.interpolate.RegularGridInterpolator`` objects. Importing SciPy
only to evaluate them costs about 40 MB of resident memory, which matters on
small devices. ``load_grid_interpolator()`` reads those pickles without
importing SciPy and returns a ``LinearGridInterpolator`` that evaluates
the same piecewise bilinear function.

The arithmetic follows SciPy's ``method="linear"`` 2-D path step by step
(interval search, normalised distances, corner weights summed in the same
order), so the results are bit-identical to SciPy builds that do not fuse
multiply-add operations (e.g. the Linux wheels). Builds that do fuse them
(e.g. the macOS arm64 wheel) differ only by rounding (about 1e-16).
"""

import pickle
from pathlib import Path
from typing import Any, BinaryIO, Union

import numpy as np


class LinearGridInterpolator:
    """Piecewise bilinear interpolation on a 2-D rectilinear grid.

    Behaves like ``scipy.interpolate.RegularGridInterpolator(grid, values,
    method="linear", bounds_error=False, fill_value=fill_value)``: points
    outside the grid get ``fill_value``, points with a NaN coordinate get NaN.

    Args:
        grid: Two strictly ascending 1-D coordinate arrays, each with at least two points.
        values: Values on the grid, shape ``(len(grid[0]), len(grid[1]))``.
        fill_value: Value for points outside the grid. ``None`` extrapolates linearly.
    """

    def __init__(
        self,
        grid: tuple[Any, Any],
        values: Any,
        fill_value: Union[float, None] = np.nan,
    ) -> None:
        if len(grid) != 2:
            raise ValueError(f"Expected a 2-D grid, got {len(grid)} dimensions.")
        self._grid = tuple(np.asarray(axis, dtype=float) for axis in grid)
        self._values = np.asarray(values, dtype=float)
        for axis in self._grid:
            if axis.ndim != 1 or axis.shape[0] < 2:
                raise ValueError("Every grid axis needs at least two points.")
            if not np.all(np.diff(axis) > 0):
                raise ValueError("Grid axes must be strictly ascending.")
        if self._values.shape != (self._grid[0].shape[0], self._grid[1].shape[0]):
            raise ValueError(
                f"Values shape {self._values.shape} does not match the grid "
                f"({self._grid[0].shape[0]}, {self._grid[1].shape[0]})."
            )
        self.fill_value = fill_value

    @property
    def grid(self) -> tuple[np.ndarray, ...]:
        """Grid coordinates per axis."""
        return self._grid

    @property
    def values(self) -> np.ndarray:
        """Values on the grid."""
        return self._values

    def __call__(self, xi: Any) -> np.ndarray:
        """Interpolate at points ``xi`` of shape ``(..., 2)``."""
        xi = np.asarray(xi, dtype=float)
        if xi.shape[-1] != 2:
            raise ValueError(f"Points must have 2 coordinates, got {xi.shape[-1]}.")
        result_shape = xi.shape[:-1]
        points = xi.reshape(-1, 2)

        indices = []
        distances = []
        out_of_bounds = np.zeros(points.shape[0], dtype=bool)
        for axis, coordinates in zip(self._grid, points.T):
            # Interval i with axis[i] <= x < axis[i + 1]; the last interval also
            # takes x == axis[-1] and points outside extrapolate from the edges.
            index = np.searchsorted(axis, coordinates, side="right") - 1
            index = np.clip(index, 0, axis.shape[0] - 2)
            indices.append(index)
            distances.append((coordinates - axis[index]) / (axis[index + 1] - axis[index]))
            out_of_bounds |= coordinates < axis[0]
            out_of_bounds |= coordinates > axis[-1]

        i0, i1 = indices
        y0, y1 = distances
        values = self._values
        result = values[i0, i1] * (1 - y0) * (1 - y1)
        result = result + values[i0, i1 + 1] * (1 - y0) * y1
        result = result + values[i0 + 1, i1] * y0 * (1 - y1)
        result = result + values[i0 + 1, i1 + 1] * y0 * y1

        if self.fill_value is not None:
            result[out_of_bounds] = self.fill_value
        result[np.any(np.isnan(points), axis=-1)] = np.nan
        return result.reshape(result_shape)


class _PickledRegularGridInterpolator:
    """Stand-in for ``scipy.interpolate.RegularGridInterpolator`` while unpickling."""

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.state = state


class _GridInterpolatorUnpickler(pickle.Unpickler):
    """Unpickler that only accepts a pickled SciPy RegularGridInterpolator.

    SciPy classes are replaced by stand-ins, so SciPy is never imported; any
    other global is rejected.
    """

    _NUMPY_GLOBALS = {
        ("numpy._core.multiarray", "_reconstruct"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy", "ndarray"),
        ("numpy", "dtype"),
    }

    def find_class(self, module: str, name: str) -> Any:
        if module.startswith("scipy.interpolate") and name == "RegularGridInterpolator":
            return _PickledRegularGridInterpolator
        if module.startswith("scipy.") and name == "asarray":
            # Array conversion helper stored on the interpolator; not needed here.
            return np.asarray
        if (module, name) in self._NUMPY_GLOBALS:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"Unexpected object {module}.{name} in grid interpolator.")


def load_grid_interpolator(source: Union[str, Path, BinaryIO]) -> LinearGridInterpolator:
    """Load a pickled 2-D linear ``RegularGridInterpolator`` without SciPy.

    Args:
        source: Path to the pickle file or an open binary file.

    Returns:
        The interpolator as ``LinearGridInterpolator``.

    Raises:
        pickle.UnpicklingError: If the file holds anything else than a 2-D linear
            ``RegularGridInterpolator``.
    """
    if isinstance(source, (str, Path)):
        with open(source, "rb") as file:
            pickled = _GridInterpolatorUnpickler(file).load()
    else:
        pickled = _GridInterpolatorUnpickler(source).load()
    if not isinstance(pickled, _PickledRegularGridInterpolator):
        raise pickle.UnpicklingError("File does not hold a RegularGridInterpolator.")
    state = pickled.state
    if state.get("method") != "linear":
        raise pickle.UnpicklingError(f"Unsupported interpolation method {state.get('method')}.")
    if state.get("bounds_error"):
        raise pickle.UnpicklingError("Interpolators with bounds_error=True are not supported.")
    return LinearGridInterpolator(
        grid=state["_grid"], values=state["_values"], fill_value=state.get("fill_value")
    )
