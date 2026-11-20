import ast
import os
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, List, Optional, Union
from unittest.mock import patch

import pandas as pd
import pendulum
import pytest
import pytest_asyncio
from pydantic import Field

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.prediction import PredictionCommonSettings
from akkudoktoreos.prediction.predictionabc import (
    PredictionABC,
    PredictionContainer,
    PredictionProvider,
    PredictionRecord,
    PredictionSequence,
    cache_request,
    request_config_context,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

# Derived classes for testing
# ---------------------------


class DerivedConfig(PredictionCommonSettings):
    env_var: Optional[int] = Field(default=None, description="Test config by environment var")
    instance_field: Optional[str] = Field(default=None, description="Test config by instance field")
    class_constant: Optional[int] = Field(default=None, description="Test config by class constant")


class DerivedBase(PredictionABC):
    instance_field: Optional[str] = Field(default=None, description="Field Value")
    class_constant: ClassVar[int] = 30


class DerivedRecord(PredictionRecord):
    prediction_value: Optional[float] = Field(default=None, description="Prediction Value")


class DerivedSequence(PredictionSequence[DerivedRecord]):
    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord


class DerivedPredictionProvider(PredictionProvider[DerivedRecord]):
    """A concrete subclass of PredictionProvider for testing purposes."""

    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )
    provider_enabled: ClassVar[bool] = False
    provider_updated: ClassVar[bool] = False

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    # Implement abstract methods for test purposes
    def provider_id(self) -> str:
        return "DerivedPredictionProvider"

    def enabled(self) -> bool:
        return self.provider_enabled

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Simulate update logic
        DerivedPredictionProvider.provider_updated = True


class DerivedPredictionContainer(PredictionContainer):
    providers: List[Union[DerivedPredictionProvider, PredictionProvider]] = Field(
        default_factory=list, description="List of prediction providers"
    )


# Tests
# ----------


class TestPredictionABC:
    @pytest.fixture
    def base(self, monkeypatch):
        # Provide default values for configuration
        monkeypatch.setenv("EOS_PREDICTION__HOURS", "10")
        derived = DerivedBase()
        derived.config.reset_settings()
        assert derived.config.prediction.hours == 10
        return derived

    def test_config_value_from_env_variable(self, base, monkeypatch):
        # From Prediction Config
        monkeypatch.setenv("EOS_PREDICTION__HOURS", "2")
        base.config.reset_settings()
        assert base.config.prediction.hours == 2

    def test_config_value_from_field_default(self, base, monkeypatch):
        assert base.config.prediction.__class__.model_fields["historic_hours"].default == 48
        assert base.config.prediction.historic_hours == 48
        monkeypatch.setenv("EOS_PREDICTION__HISTORIC_HOURS", "128")
        base.config.reset_settings()
        assert base.config.prediction.historic_hours == 128
        monkeypatch.delenv("EOS_PREDICTION__HISTORIC_HOURS")
        base.config.reset_settings()
        assert base.config.prediction.historic_hours == 48

    def test_get_config_value_key_error(self, base):
        with pytest.raises(AttributeError):
            base.config.prediction.non_existent_key


# TestPredictionRecord fully covered by TestDataRecord
# ----------------------------------------------------


# TestPredictionSequence fully covered by TestDataSequence
# --------------------------------------------------------


# TestPredictionStartEndKeepMixin fully covered by TestPredictionContainer
# --------------------------------------------------------


@pytest.mark.asyncio
class TestPredictionProvider:
    # Fixtures and helper functions
    @pytest.fixture
    def provider(self, monkeypatch):
        """Fixture to provide an instance of TestPredictionProvider for testing."""
        # monkeypatch restores the class flags, so later tests do not inherit them.
        monkeypatch.setattr(DerivedPredictionProvider, "provider_enabled", True)
        monkeypatch.setattr(DerivedPredictionProvider, "provider_updated", False)
        return DerivedPredictionProvider()

    @pytest.fixture
    def sample_start_datetime(self):
        """Fixture for a sample start datetime."""
        return to_datetime(datetime(2024, 11, 1, 12, 0))

    def create_test_record(self, date, value):
        """Helper function to create a test PredictionRecord."""
        return DerivedRecord(date_time=date, prediction_value=value)

    # Tests

    async def test_singleton_behavior(self, provider):
        """Test that PredictionProvider enforces singleton behavior."""
        instance1 = provider
        instance2 = DerivedPredictionProvider()
        assert instance1 is instance2, (
            "Singleton pattern is not enforced; instances are not the same."
        )

    async def test_update_computed_fields(self, provider, sample_start_datetime):
        """Test that computed fields `end_datetime` and `keep_datetime` are correctly calculated."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(sample_start_datetime)
        provider.config.prediction.hours = 24  # 24 hours into the future
        provider.config.prediction.historic_hours = 48  # 48 hours into the past

        expected_end_datetime = sample_start_datetime + to_duration(
            provider.config.prediction.hours * 3600
        )
        expected_keep_datetime = sample_start_datetime - to_duration(
            provider.config.prediction.historic_hours * 3600
        )

        assert provider.end_datetime == expected_end_datetime, (
            "End datetime is not calculated correctly."
        )
        assert provider.keep_datetime == expected_keep_datetime, (
            "Keep datetime is not calculated correctly."
        )

    @pytest.mark.parametrize(
        "timezone, end, total_hours",
        [
            # No DST change within the 48 hours.
            ("Europe/Berlin", "2024-11-03 13:00:00", 48),
            # DST ends on 2024-11-03: the window ends at the same wall-clock time,
            # 49 hours after the start (#1570).
            ("America/Los_Angeles", "2024-11-03 05:00:00", 49),
        ],
    )
    async def test_update_method_with_defaults(
        self,
        provider,
        sample_start_datetime,
        config_eos,
        monkeypatch,
        timezone: str,
        end: str,
        total_hours: int,
    ) -> None:
        """Test the `update` method with default parameters.

        The start is given in a fixed zone, so the result does not depend on the local zone
        of the test run.
        """
        # EOS config supersedes
        ems_eos = get_ems()
        # The following values are currently not set in EOS config, we can override
        monkeypatch.setenv("EOS_PREDICTION__HISTORIC_HOURS", "2")
        assert os.getenv("EOS_PREDICTION__HISTORIC_HOURS") == "2"
        provider.config.reset_settings()

        start = to_datetime(sample_start_datetime, in_timezone=timezone)
        ems_eos.set_start_datetime(start)
        await provider.update_data()

        # Default prediction horizon: 48 hours
        assert provider.config.prediction.hours == config_eos.prediction.hours == 48
        assert provider.config.prediction.historic_hours == 2
        assert provider.ems_start_datetime == start
        assert provider.end_datetime == to_datetime(end, in_timezone=timezone)
        assert provider.total_hours == total_hours
        assert provider.keep_datetime == start - to_duration("2 hours")

    async def test_update_method_force_enable(self, provider, monkeypatch):
        """Test that `update` executes when `force_enable` is True, even if `enabled` is False."""
        # Preset values that are needed by update
        monkeypatch.setenv("EOS_GENERAL__LATITUDE", "37.7749")
        monkeypatch.setenv("EOS_GENERAL__LONGITUDE", "-122.4194")

        # Override enabled to return False for this test
        DerivedPredictionProvider.provider_enabled = False
        DerivedPredictionProvider.provider_updated = False
        await provider.update_data(force_enable=True)
        assert provider.enabled() is False, "Provider should be disabled, but enabled() is True."
        assert DerivedPredictionProvider.provider_updated is True, (
            "Provider should have been executed, but was not."
        )

    async def test_delete_by_datetime(self, provider, sample_start_datetime):
        """Test `delete_by_datetime` method for removing records by datetime range."""
        # Add records to the provider for deletion testing
        records = [
            self.create_test_record(sample_start_datetime - to_duration("3 hours"), 1),
            self.create_test_record(sample_start_datetime - to_duration("1 hour"), 2),
            self.create_test_record(sample_start_datetime + to_duration("1 hour"), 3),
        ]
        for record in records:
            await provider.insert_by_datetime(record)

        await provider.delete_by_datetime(
            start_datetime=sample_start_datetime - to_duration("2 hours"),
            end_datetime=sample_start_datetime + to_duration("2 hours"),
        )
        assert len(provider) == 1, (
            "Only one record should remain after deletion by datetime."
        )
        assert provider.records[0].date_time == sample_start_datetime - to_duration("3 hours"), (
            "Unexpected record remains."
        )


@pytest.mark.asyncio
class TestPredictionContainer:
    # Fixture and helpers
    @pytest.fixture
    def container(self):
        container = DerivedPredictionContainer()
        return container

    @pytest_asyncio.fixture
    async def container_with_providers(self, monkeypatch):
        # The container only counts enabled providers. Enable it here instead of relying
        # on a TestPredictionProvider test having run before in the same process.
        monkeypatch.setattr(DerivedPredictionProvider, "provider_enabled", True)
        records = [
            # Test records - include 'prediction_value' key
            self.create_test_record(datetime(2023, 11, 5), 1),
            self.create_test_record(datetime(2023, 11, 6), 2),
            self.create_test_record(datetime(2023, 11, 7), 3),
        ]
        provider = DerivedPredictionProvider()
        await provider.delete_by_datetime(start_datetime=None, end_datetime=None)
        assert len(provider) == 0
        for record in records:
            await provider.insert_by_datetime(record)
        assert len(provider) == 3
        container = DerivedPredictionContainer()
        container.providers.clear()
        assert len(container.providers) == 0
        container.providers.append(provider)
        assert len(container.providers) == 1
        return container

    def create_test_record(self, date, value):
        """Helper function to create a test PredictionRecord."""
        return DerivedRecord(date_time=date, prediction_value=value)

    # Tests
    @pytest.mark.parametrize(
        "start, hours, end",
        [
            ("2024-11-10 00:00:00", 24, "2024-11-11 00:00:00"),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, "2024-08-11 00:00:00"),  # DST in Germany
            ("2024-03-31 00:00:00", 24, "2024-04-01 00:00:00"),  # DST change (23 hours/ day)
            ("2024-10-27 00:00:00", 24, "2024-10-28 00:00:00"),  # DST change (25 hours/ day)
            ("2024-11-10 00:00:00", 48, "2024-11-12 00:00:00"),  # No DST in Germany
            ("2024-08-10 00:00:00", 48, "2024-08-12 00:00:00"),  # DST in Germany
            ("2024-03-31 00:00:00", 48, "2024-04-02 00:00:00"),  # DST change (47 hours/ day)
            ("2024-10-27 00:00:00", 48, "2024-10-29 00:00:00"),  # DST change (49 hours/ day)
        ],
    )
    async def test_end_datetime(self, container, start, hours, end):
        """Test end datetime calculation from start datetime."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "hours": hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        expected = to_datetime(end, in_timezone="Europe/Berlin")
        assert compare_datetimes(container.end_datetime, expected).equal

    @pytest.mark.parametrize(
        "start, historic_hours, expected_keep",
        [
            # Standard case
            (
                pendulum.datetime(2024, 8, 10, 0, 0, tz="Europe/Berlin"),
                24,
                pendulum.datetime(2024, 8, 9, 0, 0, tz="Europe/Berlin"),
            ),
            # With DST, but should not affect historical data
            (
                pendulum.datetime(2024, 4, 1, 0, 0, tz="Europe/Berlin"),
                24,
                pendulum.datetime(2024, 3, 30, 23, 0, tz="Europe/Berlin"),
            ),
        ],
    )
    async def test_keep_datetime(self, container, start, historic_hours, expected_keep):
        """Test the `keep_datetime` property."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "historic_hours": historic_hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        expected = to_datetime(expected_keep, in_timezone="Europe/Berlin")
        assert compare_datetimes(container.keep_datetime, expected).equal

    @pytest.mark.parametrize(
        "start, hours, expected_hours",
        [
            ("2024-11-10 00:00:00", 24, 24),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, 24),  # DST in Germany
            ("2024-03-31 00:00:00", 24, 23),  # DST change in Germany (23 hours/ day)
            ("2024-10-27 00:00:00", 24, 25),  # DST change in Germany (25 hours/ day)
        ],
    )
    async def test_total_hours(self, container, start, hours, expected_hours):
        """Test the `total_hours` property."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "hours": hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        assert container.total_hours == expected_hours

    @pytest.mark.parametrize(
        "start, historic_hours, expected_hours",
        [
            ("2024-11-10 00:00:00", 24, 24),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, 24),  # DST in Germany
            ("2024-04-01 00:00:00", 24, 24),  # DST change on 2024-03-31 in Germany (23 hours/ day)
            ("2024-10-28 00:00:00", 24, 24),  # DST change on 2024-10-27 in Germany (25 hours/ day)
        ],
    )
    async def test_keep_hours(self, container, start, historic_hours, expected_hours):
        """Test the `keep_hours` property."""
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
        settings = {
            "prediction": {
                "historic_hours": historic_hours,
            }
        }
        container.config.merge_settings_from_dict(settings)
        assert container.keep_hours == expected_hours

    async def test_append_provider(self, container):
        assert len(container.providers) == 0
        container.providers.append(DerivedPredictionProvider())
        assert len(container.providers) == 1
        assert isinstance(container.providers[0], DerivedPredictionProvider)

    @pytest.mark.skip(reason="type check not implemented")
    async def test_append_provider_invalid_type(self, container):
        with pytest.raises(ValueError, match="must be an instance of PredictionProvider"):
            container.providers.append("not_a_provider")

    async def test_len(self, container_with_providers):
        assert len(container_with_providers) == 2

    async def test_repr(self, container_with_providers):
        representation = repr(container_with_providers)
        assert representation.startswith("DerivedPredictionContainer(")
        assert "DerivedPredictionProvider" in representation

    async def test_to_json(self, container_with_providers):
        json_str = container_with_providers.to_json()
        container_other = DerivedPredictionContainer.from_json(json_str)
        assert container_other == container_with_providers

    async def test_from_json(self, container_with_providers):
        json_str = container_with_providers.to_json()
        container = DerivedPredictionContainer.from_json(json_str)
        assert isinstance(container, DerivedPredictionContainer)
        assert len(container.providers) == 1
        assert container.providers[0] == container_with_providers.providers[0]

    async def test_provider_by_id(self, container_with_providers):
        provider = container_with_providers.provider_by_id("DerivedPredictionProvider")
        assert isinstance(provider, DerivedPredictionProvider)


class _ConfigHolder:
    """Stands in for a provider: the request cache context only reads ``config``."""

    def __init__(self, config) -> None:
        self.config = config


def test_request_config_context_follows_only_its_settings(config_eos):
    """The context changes with the settings a request reads, and only with those."""
    context = request_config_context("general/latitude", "general/longitude")
    provider = _ConfigHolder(config_eos)
    before = context(provider)
    assert context(provider) == before

    # Other settings, e.g. pushed by an adapter on every run, leave the context alone.
    config_eos.merge_settings_from_dict({"prediction": {"hours": 72}})
    config_eos.set_nested_value(
        "elecprice/elecpriceimport/import_json", '{"elecprice_marketprice_wh": [0.0003]}'
    )
    assert context(provider) == before

    config_eos.merge_settings_from_dict({"general": {"latitude": 48.14, "longitude": 11.58}})
    moved = context(provider)
    assert moved != before
    config_eos.set_nested_value("general/latitude", 52.52)
    assert context(provider) not in (before, moved)


def test_request_config_context_is_a_digest_reused_until_the_settings_change(config_eos):
    """The context is a short digest, computed once per settings generation."""
    import akkudoktoreos.prediction.predictionabc as predictionabc

    context = request_config_context("general/latitude", "pvforecast/planes")
    provider = _ConfigHolder(config_eos)
    with patch.object(predictionabc, "json", wraps=predictionabc.json) as json_spy:
        first = context(provider)
        for _ in range(3):
            assert context(provider) == first
        assert len(first) == 32
        int(first, 16)
        assert json_spy.dumps.call_count == 1

        config_eos.merge_settings_from_dict({"general": {"latitude": 48.14}})
        assert context(provider) != first
        assert json_spy.dumps.call_count == 2
        context(provider)
        assert json_spy.dumps.call_count == 2


def test_request_config_context_needs_a_path():
    with pytest.raises(ValueError):
        request_config_context()


@pytest.mark.parametrize(
    "cache_kwargs, expected",
    [
        ({}, {"with_ttl": "1 hour", "key_context_hold": "1 hour"}),
        # The TTL also sets the hold: at most one settings-driven request per TTL.
        ({"with_ttl": "3 hours"}, {"with_ttl": "3 hours", "key_context_hold": "3 hours"}),
        ({"with_ttl": None}, {"with_ttl": "1 hour", "key_context_hold": "1 hour"}),
        (
            {"with_ttl": "3 hours", "key_context_hold": "30 minutes"},
            {"with_ttl": "3 hours", "key_context_hold": "30 minutes"},
        ),
        (
            {"key_context_hold": None},
            {"with_ttl": "1 hour", "key_context_hold": None},
        ),
        # Everything else goes to cache_in_file as is.
        (
            {"ignore_params": ["token"], "suffix": ".json", "mode": "w+"},
            {
                "with_ttl": "1 hour",
                "key_context_hold": "1 hour",
                "ignore_params": ["token"],
                "suffix": ".json",
                "mode": "w+",
            },
        ),
    ],
)
def test_cache_request_passes_its_options_to_cache_in_file(cache_kwargs, expected):
    import akkudoktoreos.prediction.predictionabc as predictionabc

    with patch.object(predictionabc, "cache_in_file") as cache_in_file:
        cache_request("general/latitude", **cache_kwargs)

    kwargs = dict(cache_in_file.call_args.kwargs)
    key_context = kwargs.pop("key_context")
    assert key_context.config_paths == ("general/latitude",)
    assert kwargs == expected


def test_cache_request_builds_the_key_context_itself():
    with pytest.raises(TypeError):
        cache_request("general/latitude", key_context=lambda *args, **kwargs: None)


def _cached_requests() -> list[tuple[str, ast.FunctionDef, list[str], dict]]:
    """The ``@cache_request`` methods of the prediction package, with their module's methods."""
    import akkudoktoreos.prediction as prediction_package

    found = []
    for path in sorted(Path(prediction_package.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for method in methods.values():
            for decorator in method.decorator_list:
                if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", "") == (
                    "cache_request"
                ):
                    paths = [arg.value for arg in decorator.args]  # type: ignore[attr-defined]
                    found.append((path.name, method, paths, methods))
    return found


# Inherited properties that read the configuration.
_INHERITED_CONFIG_READS = {
    "end_datetime": "prediction/hours",
    "keep_datetime": "prediction/historic_hours",
}


def _config_reads(method: ast.FunctionDef, methods: dict, seen: set) -> set[str]:
    """Configuration paths a method reads, following ``self.<method>`` of its module.

    The time stamp of the response (``self.update_datetime = ...``) is not part of the
    request and left out.
    """

    def walk(node):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute) and target.attr == "update_datetime"
            for target in node.targets
        ):
            return
        yield node
        for child in ast.iter_child_nodes(node):
            yield from walk(child)

    reads: set[str] = set()
    for node in walk(method):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            if node.attr in _INHERITED_CONFIG_READS:
                reads.add(_INHERITED_CONFIG_READS[node.attr])
            if node.attr in methods and node.attr not in seen:
                seen.add(node.attr)
                reads |= _config_reads(methods[node.attr], methods, seen)
        parts = []
        inner: ast.expr = node
        while isinstance(inner, ast.Attribute):
            parts.append(inner.attr)
            inner = inner.value
        if isinstance(inner, ast.Name) and inner.id == "self" and parts[-1] == "config":
            if len(parts) > 1:
                reads.add("/".join(reversed(parts[:-1])))
    return {read for read in reads if not any(other.startswith(read + "/") for other in reads)}


def test_provider_requests_are_cached_with_cache_request():
    """Provider requests use ``cache_request``, which keys on their settings (#1559)."""
    import akkudoktoreos.prediction as prediction_package

    package = Path(prediction_package.__file__).parent
    plain = [
        path.name
        for path in sorted(package.glob("*.py"))
        if "@cache_in_file(" in path.read_text(encoding="utf-8")
    ]
    assert plain == []
    assert len(_cached_requests()) >= 16


def test_cached_requests_declare_every_setting_they_read(config_eos):
    """A setting the request reads but the key leaves out gives stale responses (#1559).

    A declared setting the request does not read costs extra requests.
    """
    problems = []
    for module, method, paths, methods in _cached_requests():
        for path in paths:
            config_eos.get_nested_value(path)  # raises for an unknown setting
        reads = _config_reads(method, methods, {method.name})
        missing = sorted(
            read for read in reads if not any(read == p or read.startswith(p + "/") for p in paths)
        )
        unused = sorted(
            p
            for p in paths
            if not any(read == p or read.startswith(p + "/") or p.startswith(read + "/") for read in reads)
        )
        if missing or unused:
            problems.append((module, method.name, missing, unused))
    assert problems == []
