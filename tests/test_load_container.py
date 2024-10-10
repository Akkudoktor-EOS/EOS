import pytest

from akkudoktoreos.class_load_container import (
    LoadAggregator,  # Adjust the import statement based on where your class is defined
)


@pytest.fixture
def load_data():
    """Fixture that provides common load data for tests."""
    return {
        "household": [
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
            22,
            23,
            24,
        ],
        "heat_pump": [
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
        ],
        "invalid_length": [1, 2, 3],  # This will be used to test invalid input
    }


class TestTotalLoad:
    def test_initialization(self):
        """Test that the object initializes with the correct default prediction hours."""
        total_load = LoadAggregator()
        assert total_load.prediction_hours == 24
        assert total_load.loads == {}

    def test_add_load_valid_list(self, load_data):
        """Test adding a valid load array using a list."""
        total_load = LoadAggregator()
        total_load.add_load("Household", load_data["household"])
        assert "Household" in total_load.loads
        assert total_load.loads["Household"] == load_data["household"]

    def test_add_load_valid_tuple(self, load_data):
        """Test adding a valid load array using a tuple."""
        total_load = LoadAggregator()
        total_load.add_load(
            "Heat Pump", tuple(load_data["household"])
        )  # Converting list to tuple
        assert "Heat Pump" in total_load.loads
        assert total_load.loads["Heat Pump"] == list(load_data["household"])

    def test_add_load_invalid_length(self, load_data):
        """Test adding an array with an invalid length."""
        total_load = LoadAggregator()
        with pytest.raises(
            ValueError, match="Total load inconsistent lengths in arrays: test 3"
        ):
            total_load.add_load(
                "test", load_data["invalid_length"]
            )  # Should raise an error since length is 3

    def test_calculate_total_load_no_loads(self):
        """Test calculating total load when no loads have been added."""
        total_load = LoadAggregator()
        assert total_load.calculate_total_load() == []

    def test_calculate_total_load_with_loads(self, load_data):
        """Test calculating total load when loads have been added."""
        total_load = LoadAggregator()
        total_load.add_load("Household", load_data["household"])
        total_load.add_load("Heat Pump", load_data["heat_pump"])
        total_loads = total_load.calculate_total_load()
        assert total_loads == [
            2.0,
            3.0,
            4.0,
            5.0,
            6.0,
            7.0,
            8.0,
            9.0,
            10.0,
            11.0,
            12.0,
            13.0,
            14.0,
            15.0,
            16.0,
            17.0,
            18.0,
            19.0,
            20.0,
            21.0,
            22.0,
            23.0,
            24.0,
            25.0,
        ]

    def test_add_load_invalid_type(self):
        """Test adding an array with an invalid type."""
        total_load = LoadAggregator()
        with pytest.raises(TypeError):
            total_load.add_load(
                "Invalid", 123
            )  # Should raise an error since 123 is not a list or tuple
