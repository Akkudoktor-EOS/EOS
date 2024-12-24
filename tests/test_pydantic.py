from typing import Optional

import pandas as pd
import pendulum
import pytest
from pydantic import Field, ValidationError

from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeData,
    PydanticDateTimeDataFrame,
    PydanticDateTimeSeries,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime


class PydanticTestModel(PydanticBaseModel):
    datetime_field: pendulum.DateTime = Field(
        ..., description="A datetime field with pendulum support."
    )
    optional_field: Optional[str] = Field(default=None, description="An optional field.")


class TestPydanticBaseModel:
    def test_valid_pendulum_datetime(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt)
        assert model.datetime_field == dt

    def test_invalid_datetime_string(self):
        with pytest.raises(ValidationError, match="Input should be an instance of DateTime"):
            PydanticTestModel(datetime_field="invalid_datetime")

    def test_iso8601_serialization(self):
        dt = pendulum.datetime(2024, 12, 21, 15, 0, 0)
        model = PydanticTestModel(datetime_field=dt)
        serialized = model.to_dict()
        expected_dt = to_datetime(dt)
        result_dt = to_datetime(serialized["datetime_field"])
        assert compare_datetimes(result_dt, expected_dt)

    def test_reset_to_defaults(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt, optional_field="some value")
        model.reset_to_defaults()
        assert model.datetime_field == dt
        assert model.optional_field is None

    def test_from_dict_and_to_dict(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt)
        data = model.to_dict()
        restored_model = PydanticTestModel.from_dict(data)
        assert restored_model.datetime_field == dt

    def test_to_json_and_from_json(self):
        dt = pendulum.now()
        model = PydanticTestModel(datetime_field=dt)
        json_data = model.to_json()
        restored_model = PydanticTestModel.from_json(json_data)
        assert restored_model.datetime_field == dt


class TestPydanticDateTimeData:
    def test_valid_list_lengths(self):
        data = {
            "timestamps": ["2024-12-21T15:00:00+00:00"],
            "values": [100],
        }
        model = PydanticDateTimeData(root=data)
        assert pendulum.parse(model.root["timestamps"][0]) == pendulum.parse(
            "2024-12-21T15:00:00+00:00"
        )

    def test_invalid_list_lengths(self):
        data = {
            "timestamps": ["2024-12-21T15:00:00+00:00"],
            "values": [100, 200],
        }
        with pytest.raises(
            ValidationError, match="All lists in the dictionary must have the same length"
        ):
            PydanticDateTimeData(root=data)


class TestPydanticDateTimeDataFrame:
    def test_valid_dataframe(self):
        df = pd.DataFrame(
            {
                "value": [100, 200],
            },
            index=pd.to_datetime(["2024-12-21", "2024-12-22"]),
        )
        model = PydanticDateTimeDataFrame.from_dataframe(df)
        result = model.to_dataframe()

        # Check index
        assert len(result.index) == len(df.index)
        for i, dt in enumerate(df.index):
            expected_dt = to_datetime(dt)
            result_dt = to_datetime(result.index[i])
            assert compare_datetimes(result_dt, expected_dt).equal


class TestPydanticDateTimeSeries:
    def test_valid_series(self):
        series = pd.Series([100, 200], index=pd.to_datetime(["2024-12-21", "2024-12-22"]))
        model = PydanticDateTimeSeries.from_series(series)
        result = model.to_series()

        # Check index
        assert len(result.index) == len(series.index)
        for i, dt in enumerate(series.index):
            expected_dt = to_datetime(dt)
            result_dt = to_datetime(result.index[i])
            assert compare_datetimes(result_dt, expected_dt).equal
