import pytest

from akkudoktoreos.prediction.load_aggregator import LoadAggregator


def test_initialization():
    aggregator = LoadAggregator()
    assert aggregator.prediction_hours == 24
    assert aggregator.loads == {}


def test_add_load_valid():
    aggregator = LoadAggregator(prediction_hours=3)
    aggregator.add_load("Source1", [10.0, 20.0, 30.0])
    assert aggregator.loads["Source1"] == [10.0, 20.0, 30.0]


def test_add_load_invalid_length():
    aggregator = LoadAggregator(prediction_hours=3)
    with pytest.raises(ValueError, match="Total load inconsistent lengths in arrays: Source1 2"):
        aggregator.add_load("Source1", [10.0, 20.0])


def test_calculate_total_load_empty():
    aggregator = LoadAggregator()
    assert aggregator.calculate_total_load() == []


def test_calculate_total_load():
    aggregator = LoadAggregator(prediction_hours=3)
    aggregator.add_load("Source1", [10.0, 20.0, 30.0])
    aggregator.add_load("Source2", [5.0, 15.0, 25.0])
    assert aggregator.calculate_total_load() == [15.0, 35.0, 55.0]


def test_calculate_total_load_single_source():
    aggregator = LoadAggregator(prediction_hours=3)
    aggregator.add_load("Source1", [10.0, 20.0, 30.0])
    assert aggregator.calculate_total_load() == [10.0, 20.0, 30.0]
