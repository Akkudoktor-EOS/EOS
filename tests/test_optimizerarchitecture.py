"""Architecture test for the optimizer boundaries (ADR 0003 to 0011).

Parses the imports of ``src/akkudoktoreos`` and checks four rules:

- ``cross-optimizer``: an optimizer never imports another optimizer.
- ``internals``: code outside an optimizer imports only the optimizer's package
  (its ``__init__.py``), never its modules.
- ``world-access``: only the parameter preparation of an optimizer
  (``<name>prepare.py`` or a ``prepare`` subpackage) reads configuration,
  predictions, measurements or EMS state. The optimizer's settings model
  (``<name>settings.py``) may import ``akkudoktoreos.config.configabc`` to define
  ``optimization.<name>``.
- ``pydantic``: only the settings, request, preparation and API modules of an
  optimizer import Pydantic; optimization parameters are dataclasses.

Violations that existed when the test was introduced are listed in
``BASELINE``. The baseline only shrinks: a violation missing from it fails, and
so does an entry that no longer occurs. Remove the entries a change fixes. Add
entries only together with a new entry in the lists below, for the existing
uses it reveals (ADR 0007).
"""

import ast
import functools
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Optional

import pytest

PACKAGE = "akkudoktoreos"
SRC_DIR = Path(__file__).parent.parent / "src"


def _is_package(path: Path) -> bool:
    """A regular or namespace package: a directory holding Python modules."""
    return path.is_dir() and any(path.rglob("*.py"))


def _is_module(module: str) -> bool:
    path = SRC_DIR / Path(*module.split("."))
    return path.with_suffix(".py").is_file() or _is_package(path)


def _discover_optimizers() -> dict[str, tuple[str, ...]]:
    """Optimizer name -> packages that belong to it.

    Every subpackage ``optimization/<name>`` is an optimizer; ``devices/<name>``
    holds its simulation devices, if present. Discovered, so that adding or
    removing an optimizer does not touch this test.
    """
    optimizers: dict[str, tuple[str, ...]] = {}
    for path in sorted((SRC_DIR / PACKAGE / "optimization").iterdir()):
        if not _is_package(path):
            continue
        name = path.name
        packages = [f"{PACKAGE}.optimization.{name}"]
        if _is_module(f"{PACKAGE}.devices.{name}"):
            packages.append(f"{PACKAGE}.devices.{name}")
        optimizers[name] = tuple(packages)
    return optimizers


OPTIMIZERS: dict[str, tuple[str, ...]] = _discover_optimizers()

# Modules an optimizer reads only during parameter preparation: configuration,
# predictions, measurements, EMS state, and the shared device configuration
# (device settings, profiles, operation-mode enums).
WORLD_MODULES: tuple[str, ...] = (
    "akkudoktoreos.config",
    "akkudoktoreos.prediction",
    "akkudoktoreos.measurement",
    "akkudoktoreos.core.ems",
    "akkudoktoreos.core.emsettings",
    "akkudoktoreos.core.cachesettings",
    "akkudoktoreos.core.logsettings",
    "akkudoktoreos.core.database",
    "akkudoktoreos.core.databaseabc",
    "akkudoktoreos.core.dataabc",
    "akkudoktoreos.core.cache",
    "akkudoktoreos.adapter",
    "akkudoktoreos.devices.settings",
    "akkudoktoreos.devices.profile",
    "akkudoktoreos.devices.devices",
    "akkudoktoreos.devices.devicesabc",
)

# Names that give access to configuration, predictions, measurements or EMS state.
WORLD_NAMES: dict[str, frozenset[str]] = {
    "akkudoktoreos.core.coreabc": frozenset(
        {
            "ConfigMixin",
            "PredictionMixin",
            "MeasurementMixin",
            "EnergyManagementSystemMixin",
            "get_config",
            "get_prediction",
            "get_measurement",
            "get_ems",
            "StartMixin",
            "DatabaseMixin",
            "get_database",
            "AdapterMixin",
            "get_adapter",
            "get_resource_registry",
            "config_initialized",
            "singletons_init",
        }
    ),
    "akkudoktoreos.optimization.optimizationabc": frozenset({"OptimizationBase"}),
    "akkudoktoreos.optimization.optimization": frozenset({"OptimizationCommonSettings"}),
    "akkudoktoreos.utils.gridinterpolator": frozenset({"configured_grid_interpolator_backend"}),
}

# EMS state, banned in parameter preparation too: the energy management passes
# the run start and the warm start as arguments (ADR 0006).
EMS_MODULES: tuple[str, ...] = ("akkudoktoreos.core.ems", "akkudoktoreos.core.cache")
EMS_NAMES: dict[str, frozenset[str]] = {
    "akkudoktoreos.core.coreabc": frozenset(
        {"EnergyManagementSystemMixin", "StartMixin", "get_ems"}
    ),
}

# Pydantic, allowed only where external input arrives (ADR 0009, 0010).
PYDANTIC_MODULES: tuple[str, ...] = ("pydantic", "akkudoktoreos.core.pydantic")
PYDANTIC_ROLES: tuple[str, ...] = ("settings", "request", "api")

# What an optimizer's settings model may import beyond the generic rules.
SETTINGS_ALLOWED_MODULES: tuple[str, ...] = ("akkudoktoreos.config.configabc",)


class Violation(NamedTuple):
    """One import that breaks an optimizer boundary."""

    rule: str
    module: str
    imported: str


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC_DIR).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _optimizer_of(module: str) -> Optional[str]:
    for name, packages in OPTIMIZERS.items():
        if any(_within(module, package) for package in packages):
            return name
    return None


def _is_preparation(module: str, optimizer: str) -> bool:
    package = f"akkudoktoreos.optimization.{optimizer}"
    return module == f"{package}.{optimizer}prepare" or _within(module, f"{package}.prepare")


def _has_role(module: str, optimizer: str, role: str) -> bool:
    """Module ``optimization/<name>/<name><role>.py`` (ADR 0010)."""
    return module == f"akkudoktoreos.optimization.{optimizer}.{optimizer}{role}"


def _is_settings(module: str, optimizer: str) -> bool:
    return _has_role(module, optimizer, "settings")


def _imports(path: Path, module: str) -> Iterator[str]:
    """Yield every imported module, and ``module:name`` for each imported name.

    A module bound as a whole (``import a.b``, ``from a import b`` for a module
    ``b``, ``from a import *``) also yields ``module:*``: every name in it is
    reachable. Function-level and ``TYPE_CHECKING`` imports count: they are
    dependencies as well.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
                yield f"{alias.name}:*"
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            for alias in node.names:
                submodule = f"{source}.{alias.name}"
                if _is_module(submodule):
                    # Depends on the submodule, not on everything in ``source``.
                    yield submodule
                    yield f"{submodule}:*"
                else:
                    yield source
                    yield f"{source}:{alias.name}"


def _check(module: str, imported: str) -> Iterator[Violation]:
    source, _, name = imported.partition(":")
    own = _optimizer_of(module)
    if (
        own is not None
        and not name
        and any(_within(source, pydantic) for pydantic in PYDANTIC_MODULES)
        and not _is_preparation(module, own)
        and not any(_has_role(module, own, role) for role in PYDANTIC_ROLES)
    ):
        yield Violation("pydantic", module, source)
    if not _within(source, PACKAGE):
        return
    other = _optimizer_of(source)

    if own is None:
        # Outside code may import an optimizer's package, not its modules.
        if other is not None and source not in OPTIMIZERS[other]:
            yield Violation("internals", module, source)
        return

    if other is not None and other != own:
        yield Violation("cross-optimizer", module, source)
        return

    if _is_preparation(module, own):
        ems_names = EMS_NAMES.get(source, frozenset())
        if name in ems_names or (name == "*" and ems_names):
            yield Violation("world-access", module, imported)
        elif not name and any(_within(source, ems) for ems in EMS_MODULES):
            yield Violation("world-access", module, source)
        return
    if name:
        world_names = WORLD_NAMES.get(source, frozenset())
        if name in world_names or (name == "*" and world_names):
            yield Violation("world-access", module, imported)
        return
    if any(_within(source, world) for world in WORLD_MODULES):
        if _is_settings(module, own) and any(
            _within(source, allowed) for allowed in SETTINGS_ALLOWED_MODULES
        ):
            return
        yield Violation("world-access", module, source)


@functools.cache
def collect_violations() -> frozenset[Violation]:
    """All boundary violations in the source tree."""
    violations: set[Violation] = set()
    for path in sorted((SRC_DIR / PACKAGE).rglob("*.py")):
        module = _module_name(path)
        for imported in _imports(path, module):
            violations.update(_check(module, imported))
    return frozenset(violations)


# Known violations when the test was introduced. Remove fixed entries; add only
# the existing uses revealed by a new list entry (ADR 0007).
BASELINE: frozenset[Violation] = frozenset(
    {
        Violation(
            "cross-optimizer",
            "akkudoktoreos.optimization.genetic0.genetic0params",
            "akkudoktoreos.optimization.genetic.forecast",
        ),
        Violation(
            "internals",
            "akkudoktoreos.config.configmigrate",
            "akkudoktoreos.optimization.genetic.geneticsettings",
        ),
        Violation(
            "internals",
            "akkudoktoreos.core.ems",
            "akkudoktoreos.optimization.genetic.configrequest",
        ),
        Violation(
            "internals",
            "akkudoktoreos.core.ems",
            "akkudoktoreos.optimization.genetic.genetic",
        ),
        Violation(
            "internals",
            "akkudoktoreos.core.ems",
            "akkudoktoreos.optimization.genetic.geneticparams",
        ),
        Violation(
            "internals",
            "akkudoktoreos.core.ems",
            "akkudoktoreos.optimization.genetic.geneticsolution",
        ),
        Violation(
            "internals",
            "akkudoktoreos.core.ems",
            "akkudoktoreos.optimization.genetic0.genetic0",
        ),
        Violation(
            "internals",
            "akkudoktoreos.core.ems",
            "akkudoktoreos.optimization.genetic0.genetic0params",
        ),
        Violation(
            "internals",
            "akkudoktoreos.core.ems",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
        ),
        Violation(
            "internals",
            "akkudoktoreos.devices.settings.batterysettings",
            "akkudoktoreos.devices.genetic.battery",
        ),
        Violation(
            "internals",
            "akkudoktoreos.devices.settings.batterysettings",
            "akkudoktoreos.devices.genetic0.genetic0battery",
        ),
        Violation(
            "internals",
            "akkudoktoreos.devices.settings.homeappliancesettings",
            "akkudoktoreos.devices.genetic.homeappliance",
        ),
        Violation(
            "internals",
            "akkudoktoreos.devices.settings.homeappliancesettings",
            "akkudoktoreos.devices.genetic0.genetic0homeappliance",
        ),
        Violation(
            "internals",
            "akkudoktoreos.devices.settings.invertersettings",
            "akkudoktoreos.devices.genetic.inverter",
        ),
        Violation(
            "internals",
            "akkudoktoreos.devices.settings.invertersettings",
            "akkudoktoreos.devices.genetic0.genetic0inverter",
        ),
        Violation(
            "internals",
            "akkudoktoreos.optimization.optimization",
            "akkudoktoreos.optimization.genetic.geneticsettings",
        ),
        Violation(
            "internals",
            "akkudoktoreos.optimization.optimization",
            "akkudoktoreos.optimization.genetic0.genetic0settings",
        ),
        Violation(
            "internals",
            "akkudoktoreos.server.eos",
            "akkudoktoreos.optimization.genetic.configrequest",
        ),
        Violation(
            "internals",
            "akkudoktoreos.server.eos",
            "akkudoktoreos.optimization.genetic.geneticsolution",
        ),
        Violation(
            "internals",
            "akkudoktoreos.server.eos",
            "akkudoktoreos.optimization.genetic.geneticvisualize",
        ),
        Violation(
            "internals",
            "akkudoktoreos.server.eos",
            "akkudoktoreos.optimization.genetic0.genetic0params",
        ),
        Violation(
            "internals",
            "akkudoktoreos.server.eos",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
        ),
        Violation(
            "internals",
            "akkudoktoreos.server.eos",
            "akkudoktoreos.optimization.genetic0.genetic0visualize",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.devices.genetic.battery",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.devices.genetic.homeappliance",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.devices.genetic.inverter",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.devices.genetic0.genetic0battery",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.devices.genetic0.genetic0homeappliance",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.devices.genetic0.genetic0inverter",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.configrequest",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.genetic",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.genetic",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.geneticabc",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.geneticabc",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.geneticdevices",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.geneticparams",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.geneticsolution",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.geneticsolution",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.tailvalue",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.terminalvalue",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic.terminalvalue",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0abc",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0abc",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0devices",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0params",
            "pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "pydantic",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
            "pydantic",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic.battery",
            "akkudoktoreos.devices.devicesabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic.battery",
            "akkudoktoreos.devices.settings.batteryresolution",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic.battery",
            "akkudoktoreos.devices.settings.batterysettings",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic.homeappliance",
            "akkudoktoreos.config.configabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic.homeappliance",
            "akkudoktoreos.devices.devicesabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic.inverter",
            "akkudoktoreos.devices.devicesabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic.inverter",
            "akkudoktoreos.prediction.interpolator",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic0.genetic0battery",
            "akkudoktoreos.devices.settings.batterysettings",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.devices.genetic0.genetic0homeappliance",
            "akkudoktoreos.config.configabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.configrequest",
            "akkudoktoreos.core.coreabc:ConfigMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.configrequest",
            "akkudoktoreos.core.coreabc:MeasurementMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.configrequest",
            "akkudoktoreos.core.coreabc:PredictionMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.configrequest",
            "akkudoktoreos.core.coreabc:get_ems",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.configrequest",
            "akkudoktoreos.devices.settings.batteryresolution",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.configrequest",
            "akkudoktoreos.devices.settings.batterysettings",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.genetic",
            "akkudoktoreos.devices.devicesabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.genetic",
            "akkudoktoreos.optimization.optimizationabc:OptimizationBase",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticparams",
            "akkudoktoreos.core.coreabc:ConfigMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticparams",
            "akkudoktoreos.core.coreabc:MeasurementMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticparams",
            "akkudoktoreos.core.coreabc:PredictionMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticsolution",
            "akkudoktoreos.core.coreabc:ConfigMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticsolution",
            "akkudoktoreos.core.coreabc:get_ems",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticsolution",
            "akkudoktoreos.core.coreabc:get_prediction",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticsolution",
            "akkudoktoreos.devices.devicesabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticvisualize",
            "akkudoktoreos.core.coreabc:ConfigMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic.geneticvisualize",
            "akkudoktoreos.core.coreabc:get_ems",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0",
            "akkudoktoreos.optimization.optimizationabc:OptimizationBase",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0loadinterpolator",
            "akkudoktoreos.core.cache",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0loadinterpolator",
            "akkudoktoreos.utils.gridinterpolator:configured_grid_interpolator_backend",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0params",
            "akkudoktoreos.core.coreabc:ConfigMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0params",
            "akkudoktoreos.core.coreabc:MeasurementMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0params",
            "akkudoktoreos.core.coreabc:PredictionMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0params",
            "akkudoktoreos.core.coreabc:get_ems",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
            "akkudoktoreos.core.coreabc:ConfigMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
            "akkudoktoreos.core.coreabc:get_ems",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
            "akkudoktoreos.core.coreabc:get_prediction",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0solution",
            "akkudoktoreos.devices.devicesabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0visualize",
            "akkudoktoreos.core.coreabc:ConfigMixin",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0visualize",
            "akkudoktoreos.core.coreabc:get_ems",
        ),
    }
)


def _format(violations: Iterable[Violation]) -> str:
    return "\n".join(f"  {v.rule}: {v.module} -> {v.imported}" for v in sorted(violations))


class TestOptimizerArchitecture:
    """Optimizer boundaries of ADR 0003 to 0011."""

    @pytest.fixture
    def synthetic_optimizers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rule checks run against made-up optimizers, so no real one is named here."""
        monkeypatch.setitem(
            globals(),
            "OPTIMIZERS",
            {
                "alpha": ("akkudoktoreos.optimization.alpha", "akkudoktoreos.devices.alpha"),
                "beta": ("akkudoktoreos.optimization.beta", "akkudoktoreos.devices.beta"),
            },
        )

    def test_no_new_violations(self) -> None:
        new = collect_violations() - BASELINE
        assert not new, (
            "Imports break the optimizer boundaries (see the optimizer-architecture "
            "skill and ADR 0003 to 0011). Fix the code; do not extend BASELINE for new code:\n"
            + _format(new)
        )

    def test_baseline_has_no_stale_entries(self) -> None:
        stale = BASELINE - collect_violations()
        assert not stale, (
            "These baseline entries no longer occur. Remove them from BASELINE in "
            "tests/test_optimizerarchitecture.py:\n" + _format(stale)
        )

    @pytest.mark.parametrize(
        ("module", "imported", "rule"),
        [
            # An optimizer importing another one.
            (
                "akkudoktoreos.optimization.beta.betaparams",
                "akkudoktoreos.optimization.alpha.forecast",
                "cross-optimizer",
            ),
            # Outside code importing an optimizer's module.
            (
                "akkudoktoreos.core.ems",
                "akkudoktoreos.optimization.alpha.alpha",
                "internals",
            ),
            # The simulation reading predictions.
            (
                "akkudoktoreos.devices.alpha.inverter",
                "akkudoktoreos.prediction.interpolator",
                "world-access",
            ),
            # The solution conversion reading EMS state through a getter.
            (
                "akkudoktoreos.optimization.alpha.alphasolution",
                "akkudoktoreos.core.coreabc:get_ems",
                "world-access",
            ),
            # A getter reached through the whole module.
            (
                "akkudoktoreos.optimization.alpha.alpha",
                "akkudoktoreos.core.coreabc:*",
                "world-access",
            ),
            # EMS access through StartMixin.
            (
                "akkudoktoreos.devices.alpha.battery",
                "akkudoktoreos.core.coreabc:StartMixin",
                "world-access",
            ),
            # A module that only ends in "prepare" is not parameter preparation.
            (
                "akkudoktoreos.optimization.alpha.unprepare",
                "akkudoktoreos.config.config",
                "world-access",
            ),
            # Parameter preparation does not read EMS state either.
            (
                "akkudoktoreos.optimization.alpha.alphaprepare",
                "akkudoktoreos.core.coreabc:get_ems",
                "world-access",
            ),
            (
                "akkudoktoreos.optimization.alpha.alphaprepare",
                "akkudoktoreos.core.ems",
                "world-access",
            ),
            # The EMS run cache, also banned in parameter preparation.
            (
                "akkudoktoreos.optimization.alpha.alphaprepare",
                "akkudoktoreos.core.cache",
                "world-access",
            ),
            # Configuration read through a utility.
            (
                "akkudoktoreos.optimization.alpha.alphainterpolator",
                "akkudoktoreos.utils.gridinterpolator:configured_grid_interpolator_backend",
                "world-access",
            ),
            # Pydantic in the simulation.
            (
                "akkudoktoreos.optimization.alpha.devices.alphabattery",
                "pydantic",
                "pydantic",
            ),
            (
                "akkudoktoreos.optimization.alpha.alphaparameters",
                "akkudoktoreos.core.pydantic",
                "pydantic",
            ),
            # Only the settings model under optimization/ may import config.configabc.
            (
                "akkudoktoreos.devices.alpha.alphasettings",
                "akkudoktoreos.config.configabc",
                "world-access",
            ),
        ],
    )
    @pytest.mark.usefixtures("synthetic_optimizers")
    def test_detects_violation(self, module: str, imported: str, rule: str) -> None:
        assert [v.rule for v in _check(module, imported)] == [rule]

    @pytest.mark.parametrize(
        ("module", "imported"),
        [
            # Parameter preparation reads the world.
            ("akkudoktoreos.optimization.alpha.alphaprepare", "akkudoktoreos.config.config"),
            (
                "akkudoktoreos.optimization.alpha.prepare.alphadevices",
                "akkudoktoreos.config.config",
            ),
            (
                "akkudoktoreos.optimization.alpha.alphaprepare",
                "akkudoktoreos.core.coreabc:get_prediction",
            ),
            # The settings model defines optimization.<name>.
            (
                "akkudoktoreos.optimization.alpha.alphasettings",
                "akkudoktoreos.config.configabc",
            ),
            # Generic utilities and the output contract.
            ("akkudoktoreos.devices.alpha.battery", "akkudoktoreos.utils.datetimeutil"),
            ("akkudoktoreos.optimization.alpha.alphasolution", "akkudoktoreos.core.emplan"),
            # Outside code importing the optimizer's package.
            ("akkudoktoreos.core.ems", "akkudoktoreos.optimization.alpha"),
            ("akkudoktoreos.core.ems", "akkudoktoreos.optimization.alpha:AlphaOptimizer"),
            # Pydantic where external input arrives.
            ("akkudoktoreos.optimization.alpha.alpharequest", "pydantic"),
            ("akkudoktoreos.optimization.alpha.alphasettings", "akkudoktoreos.core.pydantic"),
            ("akkudoktoreos.optimization.alpha.alphaapi", "pydantic"),
            ("akkudoktoreos.optimization.alpha.alphaprepare", "pydantic"),
            # Pydantic outside optimizers.
            ("akkudoktoreos.core.emplan", "pydantic"),
            # Harmless names from core.coreabc.
            (
                "akkudoktoreos.optimization.alpha.alpha",
                "akkudoktoreos.core.coreabc:documentation_mode",
            ),
            # Third-party imports.
            ("akkudoktoreos.devices.alpha.battery", "numpy"),
        ],
    )
    @pytest.mark.usefixtures("synthetic_optimizers")
    def test_allows(self, module: str, imported: str) -> None:
        assert list(_check(module, imported)) == []

    def test_discovers_optimizers(self) -> None:
        assert OPTIMIZERS
        for name, packages in OPTIMIZERS.items():
            assert packages[0] == f"akkudoktoreos.optimization.{name}"

    @pytest.mark.usefixtures("synthetic_optimizers")
    def test_settings_imports_configabc_as_module(self, tmp_path: Path) -> None:
        """``from akkudoktoreos.config import configabc`` depends on configabc only."""
        path = tmp_path / "alphasettings.py"
        path.write_text("from akkudoktoreos.config import configabc\n", encoding="utf-8")
        module = "akkudoktoreos.optimization.alpha.alphasettings"
        imported = list(_imports(path, module))
        assert "akkudoktoreos.config" not in imported
        assert [v for i in imported for v in _check(module, i)] == []
