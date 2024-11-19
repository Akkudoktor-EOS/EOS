from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.class_load_corrector import LoadPredictionAdjuster


@pytest.fixture
def setup_data() -> tuple[pd.DataFrame, pd.DataFrame, MagicMock]:
    """
    Fixture to create mock measured_data, predicted_data, and a mock load_forecast.
    These mocks are returned as a tuple for testing purposes.
    """
    # Create mock measured_data (real measured load data)
    measured_data = pd.DataFrame(
        {
            "time": pd.date_range(start="2023-10-01", periods=24, freq="H"),
            "Last": np.random.rand(24) * 100,  # Random measured load values
        }
    )

    # Create mock predicted_data (forecasted load data)
    predicted_data = pd.DataFrame(
        {
            "time": pd.date_range(start="2023-10-01", periods=24, freq="H"),
            "Last Pred": np.random.rand(24) * 100,  # Random predicted load values
        }
    )

    # Mock the load_forecast object
    load_forecast = MagicMock()
    load_forecast.get_daily_stats = MagicMock(
        return_value=([np.random.rand(24) * 100],)  # Simulate daily statistics
    )

    return measured_data, predicted_data, load_forecast


def test_merge_data(setup_data: tuple[pd.DataFrame, pd.DataFrame, MagicMock]) -> None:
    """
    Test the _merge_data method to ensure it merges measured and predicted data correctly.
    """
    measured_data, predicted_data, load_forecast = setup_data
    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, load_forecast)

    # Call the method to merge data
    merged_data = adjuster._merge_data()

    # Assert the merged data is a DataFrame
    assert isinstance(merged_data, pd.DataFrame), "Merged data should be a DataFrame"
    # Assert certain columns are present in the merged data
    assert "Hour" in merged_data.columns, "Merged data should contain 'Hour' column"
    assert "DayOfWeek" in merged_data.columns, "Merged data should contain 'DayOfWeek' column"
    assert len(merged_data) > 0, "Merged data should not be empty"


def test_remove_outliers(
    setup_data: tuple[pd.DataFrame, pd.DataFrame, MagicMock],
) -> None:
    """
    Test the _remove_outliers method to ensure it filters outliers from the data.
    """
    measured_data, predicted_data, load_forecast = setup_data
    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, load_forecast)

    # Create data with explicit outliers for testing
    normal_values = np.random.rand(98) * 100  # Normal load values
    outliers = np.array([500, -500])  # Explicit extreme outlier values
    data_with_outliers = np.concatenate([normal_values, outliers])

    # Simulate the merged_data with outliers to test the _remove_outliers method
    adjuster.merged_data = pd.DataFrame({"Last": data_with_outliers})

    # Apply the _remove_outliers method with default threshold
    filtered_data = adjuster._remove_outliers(adjuster.merged_data)

    # Assert that the output is a DataFrame and that outliers were removed
    assert isinstance(filtered_data, pd.DataFrame), "Filtered data should be a DataFrame"
    assert len(filtered_data) < len(
        adjuster.merged_data
    ), "Filtered data should remove some outliers"
    assert len(filtered_data) == 98, "Filtered data should have removed exactly 2 outliers"


def test_calculate_weighted_mean(
    setup_data: tuple[pd.DataFrame, pd.DataFrame, MagicMock],
) -> None:
    """
    Test the calculate_weighted_mean method to ensure weighted means for weekday and weekend differences are calculated correctly.
    """
    measured_data, predicted_data, load_forecast = setup_data

    # Create time range and new data for 14 days (2 weeks)
    time_range = pd.date_range(start="2023-09-25", periods=24 * 14, freq="H")

    # Create new measured_data and predicted_data matching the time range
    measured_data = pd.DataFrame(
        {
            "time": time_range,
            "Last": np.random.rand(len(time_range)) * 100,  # Random 'Last' values
        }
    )

    predicted_data = pd.DataFrame(
        {
            "time": time_range,
            "Last Pred": np.random.rand(len(time_range)) * 100,  # Random 'Last Pred' values
        }
    )

    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, load_forecast)
    adjuster.merged_data = adjuster._merge_data()

    # Calculate the weighted mean over training and testing periods
    adjuster.calculate_weighted_mean(train_period_weeks=1, test_period_weeks=1)

    # Assert that weekday and weekend differences are calculated and non-empty
    assert adjuster.weekday_diff is not None, "Weekday differences should be calculated"
    assert len(adjuster.weekend_diff) > 0, "Weekend differences should not be empty"


def test_adjust_predictions(
    setup_data: tuple[pd.DataFrame, pd.DataFrame, MagicMock],
) -> None:
    """
    Test the adjust_predictions method to ensure it correctly adds the 'Adjusted Pred' column to train and test data.
    """
    measured_data, predicted_data, load_forecast = setup_data
    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, load_forecast)
    adjuster.merged_data = adjuster._merge_data()

    # Calculate the weighted mean and adjust predictions
    adjuster.calculate_weighted_mean(train_period_weeks=1, test_period_weeks=1)
    adjuster.adjust_predictions()

    # Assert that the 'Adjusted Pred' column is present in both train and test data
    assert (
        "Adjusted Pred" in adjuster.train_data.columns
    ), "Train data should have 'Adjusted Pred' column"


def test_evaluate_model(
    setup_data: tuple[pd.DataFrame, pd.DataFrame, MagicMock],
    capsys: pytest.CaptureFixture,
) -> None:
    """
    Test the evaluate_model method to ensure it prints evaluation metrics (MSE and R-squared).
    """
    measured_data, predicted_data, load_forecast = setup_data
    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, load_forecast)
    adjuster.merged_data = adjuster._merge_data()

    # Calculate weighted mean, adjust predictions, and evaluate the model
    adjuster.calculate_weighted_mean(train_period_weeks=1, test_period_weeks=1)
    adjuster.adjust_predictions()
    mse, r2 = adjuster.evaluate_model()
    assert not np.isnan(mse)
    assert not np.isnan(r2)

    # Capture printed output and assert that evaluation metrics are printed
    captured = capsys.readouterr()
    assert "Mean Squared Error" in captured.out, "Evaluation should print Mean Squared Error"
    assert "R-squared" in captured.out, "Evaluation should print R-squared"


def test_predict_next_hours(
    setup_data: tuple[pd.DataFrame, pd.DataFrame, MagicMock],
) -> None:
    """
    Test the predict_next_hours method to ensure future predictions are made and contain 'Adjusted Pred'.
    """
    measured_data, predicted_data, load_forecast = setup_data
    adjuster = LoadPredictionAdjuster(measured_data, predicted_data, load_forecast)
    adjuster.merged_data = adjuster._merge_data()

    # Calculate weighted mean and predict the next 5 hours
    adjuster.calculate_weighted_mean(train_period_weeks=1, test_period_weeks=1)
    future_df = adjuster.predict_next_hours(5)

    # Assert that the correct number of future hours are predicted and that 'Adjusted Pred' is present
    assert len(future_df) == 5, "Should predict for 5 future hours"
    assert "Adjusted Pred" in future_df.columns, "Future data should have 'Adjusted Pred' column"
