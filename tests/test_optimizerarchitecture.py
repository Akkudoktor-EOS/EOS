"""Architecture test for the optimizer boundaries (ADR 0003 to 0011).

Parses the imports of ``src/akkudoktoreos`` and checks these rules:

- ``cross-optimizer``: an optimizer never imports another optimizer.
- ``internals``: code outside an optimizer imports only the optimizer's package
  (its ``__init__.py``), never its modules.
- ``shared-config``: the shared device configuration never imports an
  optimizer, not even its package; settings do not build optimization
  parameters (ADR 0004).
- ``world-access``: only the parameter preparation of an optimizer
  (``<name>prepare.py`` or a ``prepare`` subpackage) reads configuration,
  predictions and measurements; no module of an optimizer, the preparation
  included, reads EMS state or the server. The optimizer's settings model
  (``<name>settings.py``) may import ``akkudoktoreos.config.configabc`` to define
  ``optimization.<name>``.
- ``preparation``: inside an optimizer only ``<name>optimizer.py``, the
  package ``__init__.py`` and the parameter preparation itself import the
  parameter preparation, so no other module reaches the configuration through
  it. The package and ``<name>optimizer.py`` may re-export the preparation, so
  the other modules import neither; only the package imports
  ``<name>optimizer.py``.
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
from typing import Iterable, Iterator, Mapping, NamedTuple, Optional

import pytest

PACKAGE = "akkudoktoreos"
SRC_DIR = Path(__file__).parent.parent / "src"

Optimizers = Mapping[str, tuple[str, ...]]


def _is_package(path: Path) -> bool:
    """A regular or namespace package: a directory holding Python modules."""
    return path.is_dir() and any(path.rglob("*.py"))


def _is_module(module: str, src_dir: Path = SRC_DIR) -> bool:
    path = src_dir / Path(*module.split("."))
    return path.with_suffix(".py").is_file() or _is_package(path)


def _discover_optimizers(src_dir: Path = SRC_DIR) -> dict[str, tuple[str, ...]]:
    """Optimizer name -> packages that belong to it.

    Every subpackage ``optimization/<name>`` is an optimizer; ``devices/<name>``
    holds its simulation devices, if present. Discovered, so that adding or
    removing an optimizer does not touch this test.
    """
    optimizers: dict[str, tuple[str, ...]] = {}
    for path in sorted((src_dir / PACKAGE / "optimization").iterdir()):
        if not _is_package(path):
            continue
        name = path.name
        packages = [f"{PACKAGE}.optimization.{name}"]
        if _is_module(f"{PACKAGE}.devices.{name}", src_dir):
            packages.append(f"{PACKAGE}.devices.{name}")
        optimizers[name] = tuple(packages)
    return optimizers


OPTIMIZERS: dict[str, tuple[str, ...]] = _discover_optimizers()

# Modules an optimizer reads only during parameter preparation: configuration,
# predictions, measurements, EMS state, the server, and the shared device
# configuration (device settings, profiles, operation-mode enums).
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
    "akkudoktoreos.server",
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
    "akkudoktoreos.utils.gridinterpolator": frozenset({"configured_grid_interpolator_backend"}),
}

# Modules that mix the output contract with configuration: outside parameter
# preparation an optimizer imports only the listed contract names (ADR 0004).
CONTRACT_NAMES: dict[str, frozenset[str]] = {
    "akkudoktoreos.optimization.optimization": frozenset({"OptimizationSolution"}),
}

# EMS state and the server, banned in parameter preparation too: the energy
# management passes the run start and the warm start as arguments (ADR 0006),
# and the optimizer never imports server code (ADR 0011).
PREPARATION_BANNED_MODULES: tuple[str, ...] = (
    "akkudoktoreos.core.ems",
    "akkudoktoreos.core.cache",
    "akkudoktoreos.server",
)
PREPARATION_BANNED_NAMES: dict[str, frozenset[str]] = {
    "akkudoktoreos.core.coreabc": frozenset(
        {"EnergyManagementSystemMixin", "StartMixin", "get_ems"}
    ),
}

# The shared device configuration: never imports an optimizer (ADR 0004).
SHARED_CONFIG_MODULES: tuple[str, ...] = (
    "akkudoktoreos.devices.settings",
    "akkudoktoreos.devices.profile",
    "akkudoktoreos.devices.devices",
    "akkudoktoreos.devices.devicesabc",
)

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


def _within_any(module: str, packages: Iterable[str]) -> bool:
    return any(_within(module, package) for package in packages)


def _module_name(path: Path, src_dir: Path = SRC_DIR) -> str:
    parts = list(path.relative_to(src_dir).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _optimizer_of(module: str, optimizers: Optimizers) -> Optional[str]:
    for name, packages in optimizers.items():
        if _within_any(module, packages):
            return name
    return None


def _is_preparation(module: str, optimizer: str, src_dir: Path = SRC_DIR) -> bool:
    """``optimization/<name>/<name>prepare.py`` or a package ``optimization/<name>/prepare/``."""
    package = f"akkudoktoreos.optimization.{optimizer}"
    subpackage = f"{package}.prepare"
    return (
        module == f"{package}.{optimizer}prepare"
        or module.startswith(subpackage + ".")
        or (module == subpackage and _is_package(src_dir / Path(*subpackage.split("."))))
    )


def _has_role(module: str, optimizer: str, role: str) -> bool:
    """Module ``optimization/<name>/<name><role>.py`` (ADR 0010)."""
    return module == f"akkudoktoreos.optimization.{optimizer}.{optimizer}{role}"


def _names_hit(names: Mapping[str, frozenset[str]], source: str, name: str) -> bool:
    """``source:name`` is one of ``names``; ``*`` hits every module that has some."""
    listed = names.get(source, frozenset())
    return name in listed or (name == "*" and bool(listed))


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


def _check(module: str, imported: str, optimizers: Optimizers) -> Iterator[Violation]:
    source, _, name = imported.partition(":")
    own = _optimizer_of(module, optimizers)
    if (
        own is not None
        and not name
        and _within_any(source, PYDANTIC_MODULES)
        and not _is_preparation(module, own)
        and not any(_has_role(module, own, role) for role in PYDANTIC_ROLES)
    ):
        yield Violation("pydantic", module, source)
    if not _within(source, PACKAGE):
        return
    other = _optimizer_of(source, optimizers)

    if own is None:
        if other is None:
            return
        if _within_any(module, SHARED_CONFIG_MODULES):
            # Settings never build an optimizer's parameters.
            yield Violation("shared-config", module, source)
        elif source not in optimizers[other]:
            # Outside code may import an optimizer's package, not its modules.
            yield Violation("internals", module, source)
        return

    if other is not None and other != own:
        yield Violation("cross-optimizer", module, source)
        return

    if _is_preparation(module, own):
        if _names_hit(PREPARATION_BANNED_NAMES, source, name) or (
            not name and _within_any(source, PREPARATION_BANNED_MODULES)
        ):
            yield Violation("world-access", module, imported if name else source)
        return
    # The preparation and the modules that may re-export it: the package and
    # <name>optimizer.py. Only the package imports the optimizer module.
    package = f"akkudoktoreos.optimization.{own}"
    source_is_optimizer = _has_role(source, own, "optimizer")
    if not name and (_is_preparation(source, own) or source == package or source_is_optimizer):
        if module != package and not (
            _has_role(module, own, "optimizer") and not source_is_optimizer
        ):
            yield Violation("preparation", module, source)
        return
    if name:
        if _names_hit(WORLD_NAMES, source, name) or (
            source in CONTRACT_NAMES and name not in CONTRACT_NAMES[source]
        ):
            yield Violation("world-access", module, imported)
        return
    if _within_any(source, WORLD_MODULES):
        if _has_role(module, own, "settings") and _within_any(source, SETTINGS_ALLOWED_MODULES):
            return
        yield Violation("world-access", module, source)


@functools.cache
def collect_violations() -> frozenset[Violation]:
    """All boundary violations in the source tree."""
    violations: set[Violation] = set()
    for path in sorted((SRC_DIR / PACKAGE).rglob("*.py")):
        module = _module_name(path)
        for imported in _imports(path, module):
            violations.update(_check(module, imported, OPTIMIZERS))
    return frozenset(violations)


# Known violations when the test was introduced. Remove fixed entries; add only
# the existing uses revealed by a new list entry (ADR 0007).
BASELINE: frozenset[Violation] = frozenset(
    {
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
            "akkudoktoreos.optimization.optimization",
            "akkudoktoreos.optimization.genetic.geneticsettings",
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
            "akkudoktoreos.optimization.genetic0.genetic0convert",
            "akkudoktoreos.core.pydantic",
        ),
        Violation(
            "shared-config",
            "akkudoktoreos.devices.settings.batterysettings",
            "akkudoktoreos.devices.genetic.battery",
        ),
        Violation(
            "shared-config",
            "akkudoktoreos.devices.settings.homeappliancesettings",
            "akkudoktoreos.devices.genetic.homeappliance",
        ),
        Violation(
            "shared-config",
            "akkudoktoreos.devices.settings.invertersettings",
            "akkudoktoreos.devices.genetic.inverter",
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
            "akkudoktoreos.optimization.genetic0.genetic0convert",
            "akkudoktoreos.devices.devicesabc",
        ),
        Violation(
            "world-access",
            "akkudoktoreos.optimization.genetic0.genetic0request",
            "akkudoktoreos.config.configabc",
        ),
    }
)

# Made-up optimizers for the rule checks, so no real one is named in them.
SYNTHETIC_OPTIMIZERS: dict[str, tuple[str, ...]] = {
    "alpha": ("akkudoktoreos.optimization.alpha", "akkudoktoreos.devices.alpha"),
    "beta": ("akkudoktoreos.optimization.beta", "akkudoktoreos.devices.beta"),
}


def _format(violations: Iterable[Violation]) -> str:
    return "\n".join(f"  {v.rule}: {v.module} -> {v.imported}" for v in sorted(violations))


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestOptimizerArchitecture:
    """Optimizer boundaries of ADR 0003 to 0011."""

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
            # The shared device configuration importing an optimizer, even its package.
            (
                "akkudoktoreos.devices.settings.batterysettings",
                "akkudoktoreos.devices.alpha.battery",
                "shared-config",
            ),
            (
                "akkudoktoreos.devices.devicesabc",
                "akkudoktoreos.optimization.alpha",
                "shared-config",
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
            # The API or visualization reaching configuration through a server helper.
            (
                "akkudoktoreos.optimization.alpha.alphaapi",
                "akkudoktoreos.server.rest.error",
                "world-access",
            ),
            # Configuration settings next to the output contract.
            (
                "akkudoktoreos.optimization.alpha.alphaconvert",
                "akkudoktoreos.optimization.optimization:OptimizationCommonSettings",
                "world-access",
            ),
            (
                "akkudoktoreos.optimization.alpha.alphaconvert",
                "akkudoktoreos.optimization.optimization:*",
                "world-access",
            ),
            # A module that only ends in "prepare" is not parameter preparation.
            (
                "akkudoktoreos.optimization.alpha.unprepare",
                "akkudoktoreos.config.config",
                "world-access",
            ),
            # Neither is a plain module prepare.py.
            (
                "akkudoktoreos.optimization.alpha.prepare",
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
            # Configuration reached through the parameter preparation.
            (
                "akkudoktoreos.optimization.alpha.alphaconvert",
                "akkudoktoreos.optimization.alpha.alphaprepare",
                "preparation",
            ),
            (
                "akkudoktoreos.optimization.alpha.alphavisualize",
                "akkudoktoreos.optimization.alpha.prepare.alphadevices",
                "preparation",
            ),
            # The parameter preparation reaching the server.
            (
                "akkudoktoreos.optimization.alpha.alphaprepare",
                "akkudoktoreos.server.rest.error",
                "world-access",
            ),
            # The package may re-export the preparation.
            (
                "akkudoktoreos.optimization.alpha.alphaconvert",
                "akkudoktoreos.optimization.alpha",
                "preparation",
            ),
            # The optimizer module may re-export the preparation.
            (
                "akkudoktoreos.optimization.alpha.alphaconvert",
                "akkudoktoreos.optimization.alpha.alphaoptimizer",
                "preparation",
            ),
            (
                "akkudoktoreos.devices.alpha.battery",
                "akkudoktoreos.optimization.alpha.alphaoptimizer",
                "preparation",
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
    def test_detects_violation(self, module: str, imported: str, rule: str) -> None:
        assert [v.rule for v in _check(module, imported, SYNTHETIC_OPTIMIZERS)] == [rule]

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
            (
                "akkudoktoreos.optimization.alpha.alphaprepare",
                "akkudoktoreos.optimization.optimization:OptimizationCommonSettings",
            ),
            # The settings model defines optimization.<name>.
            (
                "akkudoktoreos.optimization.alpha.alphasettings",
                "akkudoktoreos.config.configabc",
            ),
            # The optimizer, the package and the preparation itself use the preparation.
            (
                "akkudoktoreos.optimization.alpha.alphaoptimizer",
                "akkudoktoreos.optimization.alpha.alphaprepare",
            ),
            (
                "akkudoktoreos.optimization.alpha",
                "akkudoktoreos.optimization.alpha.alphaprepare",
            ),
            (
                "akkudoktoreos.optimization.alpha.prepare.alphadevices",
                "akkudoktoreos.optimization.alpha.alphaprepare",
            ),
            (
                "akkudoktoreos.optimization.alpha.alphaoptimizer",
                "akkudoktoreos.optimization.alpha",
            ),
            ("akkudoktoreos.optimization.alpha", "akkudoktoreos.optimization.alpha.alphaoptimizer"),
            # Generic utilities and the output contract.
            ("akkudoktoreos.devices.alpha.battery", "akkudoktoreos.utils.datetimeutil"),
            ("akkudoktoreos.optimization.alpha.alphasolution", "akkudoktoreos.core.emplan"),
            (
                "akkudoktoreos.optimization.alpha.alphaconvert",
                "akkudoktoreos.optimization.optimization:OptimizationSolution",
            ),
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
    def test_allows(self, module: str, imported: str) -> None:
        assert list(_check(module, imported, SYNTHETIC_OPTIMIZERS)) == []

    def test_discovers_optimizers(self, tmp_path: Path) -> None:
        """Packages under ``optimization/`` are optimizers, with ``devices/<name>`` if present."""
        package = tmp_path / PACKAGE
        _write(package / "optimization" / "optimization.py")
        _write(package / "optimization" / "alpha" / "__init__.py")
        _write(package / "optimization" / "beta" / "betaoptimizer.py")
        (package / "optimization" / "empty").mkdir()
        _write(package / "devices" / "alpha" / "battery.py")
        assert _discover_optimizers(tmp_path) == {
            "alpha": ("akkudoktoreos.optimization.alpha", "akkudoktoreos.devices.alpha"),
            "beta": ("akkudoktoreos.optimization.beta",),
        }

    def test_recognises_preparation_package(self, tmp_path: Path) -> None:
        """``prepare/`` counts as parameter preparation only as a package."""
        module = "akkudoktoreos.optimization.alpha.prepare"
        assert not _is_preparation(module, "alpha", tmp_path)
        _write(tmp_path / PACKAGE / "optimization" / "alpha" / "prepare" / "__init__.py")
        assert _is_preparation(module, "alpha", tmp_path)

    def test_settings_imports_configabc_as_module(self, tmp_path: Path) -> None:
        """``from akkudoktoreos.config import configabc`` depends on configabc only."""
        path = tmp_path / "alphasettings.py"
        _write(path, "from akkudoktoreos.config import configabc\n")
        module = "akkudoktoreos.optimization.alpha.alphasettings"
        imported = list(_imports(path, module))
        assert "akkudoktoreos.config" not in imported
        assert [v for i in imported for v in _check(module, i, SYNTHETIC_OPTIMIZERS)] == []

    def test_resolves_relative_imports(self, tmp_path: Path) -> None:
        """A relative import is checked like the absolute one it stands for."""
        path = tmp_path / "alphaconvert.py"
        _write(path, "from ...config import config\n")
        module = "akkudoktoreos.optimization.alpha.alphaconvert"
        imported = list(_imports(path, module))
        assert "akkudoktoreos.config.config" in imported
        violations = {v for i in imported for v in _check(module, i, SYNTHETIC_OPTIMIZERS)}
        assert violations == {Violation("world-access", module, "akkudoktoreos.config.config")}
