"""PV forecast module for PV power predictions."""

from typing import Any, List, Optional, Self

from pydantic import Field, computed_field, field_validator, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.prediction import get_prediction
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.prediction.pvforecastimport import PVForecastImportCommonSettings
from akkudoktoreos.utils.docs import get_model_structure_from_examples

prediction_eos = get_prediction()

# Valid PV forecast providers
pvforecast_providers = [
    provider.provider_id()
    for provider in prediction_eos.providers
    if isinstance(provider, PVForecastProvider)
]


class PVForecastPlaneSetting(SettingsBaseModel):
    """PV Forecast Plane Configuration."""

    # latitude: Optional[float] = Field(default=None, description="Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)")
    surface_tilt: Optional[float] = Field(
        default=30.0,
        ge=0.0,
        le=90.0,
        description="Tilt angle from horizontal plane. Ignored for two-axis tracking.",
        examples=[10.0, 20.0],
    )
    surface_azimuth: Optional[float] = Field(
        default=180.0,
        ge=0.0,
        le=360.0,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
        examples=[180.0, 90.0],
    )
    userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
        examples=[[10.0, 20.0, 30.0], [5.0, 15.0, 25.0]],
    )
    peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW.", examples=[5.0, 3.5]
    )
    pvtechchoice: Optional[str] = Field(
        default="crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    loss: Optional[float] = Field(default=14.0, description="Sum of PV system losses in percent")
    trackingtype: Optional[int] = Field(
        default=None,
        ge=0,
        le=5,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
        examples=[0, 1, 2, 3, 4, 5],
    )
    optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
        examples=[False],
    )
    optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
        examples=[False],
    )
    albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
        examples=[None],
    )
    module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane.", examples=[None]
    )
    inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane.", examples=[None]
    )
    inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter [W].", examples=[6000, 4000]
    )
    modules_per_string: Optional[int] = Field(
        default=None,
        description="Number of the PV modules of the strings of this plane.",
        examples=[20],
    )
    strings_per_inverter: Optional[int] = Field(
        default=None,
        description="Number of the strings of the inverter of this plane.",
        examples=[2],
    )

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        # Check if either attribute is set and add to active planes
        if self.trackingtype == 2:
            # Tilt angle from horizontal plane is ignored for two-axis tracking.
            if self.surface_azimuth is None:
                raise ValueError("If trackingtype is set, azimuth must be set as well.")
        elif self.surface_tilt is None or self.surface_azimuth is None:
            raise ValueError("surface_tilt and surface_azimuth must be set.")
        return self

    @field_validator("mountingplace")
    def validate_mountingplace(cls, mountingplace: Optional[str]) -> Optional[str]:
        if mountingplace is not None and mountingplace not in ["free", "building"]:
            raise ValueError(f"Invalid mountingplace: {mountingplace}")
        return mountingplace

    @field_validator("pvtechchoice")
    def validate_pvtechchoice(cls, pvtechchoice: Optional[str]) -> Optional[str]:
        if pvtechchoice is not None and pvtechchoice not in ["crystSi", "CIS", "CdTe", "Unknown"]:
            raise ValueError(f"Invalid pvtechchoice: {pvtechchoice}")
        return pvtechchoice


class PVForecastCommonSettings(SettingsBaseModel):
    """PV Forecast Configuration."""

    # General plane parameters
    #     https://pvlib-python.readthedocs.io/en/stable/_modules/pvlib/iotools/pvgis.html
    # Inverter Parameters
    #     https://pvlib-python.readthedocs.io/en/stable/_modules/pvlib/inverter.html

    provider: Optional[str] = Field(
        default=None,
        description="PVForecast provider id of provider to be used.",
        examples=["PVForecastAkkudoktor"],
    )

    provider_settings: Optional[PVForecastImportCommonSettings] = Field(
        default=None, description="Provider settings", examples=[None]
    )

    planes: Optional[list[PVForecastPlaneSetting]] = Field(
        default=None,
        description="Plane configuration.",
        examples=[get_model_structure_from_examples(PVForecastPlaneSetting, True)],
    )

    max_planes: Optional[int] = Field(
        default=0,
        ge=0,
        description="Maximum number of planes that can be set",
    )

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in pvforecast_providers:
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid PV forecast provider: {pvforecast_providers}."
        )

    ## Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def planes_peakpower(self) -> List[float]:
        """Compute a list of the peak power per active planes."""
        planes_peakpower = []

        if self.planes:
            for plane in self.planes:
                peakpower = plane.peakpower
                if peakpower is None:
                    # TODO calculate peak power from modules/strings
                    planes_peakpower.append(float(5000))
                else:
                    planes_peakpower.append(float(peakpower))

        return planes_peakpower

    @computed_field  # type: ignore[prop-decorator]
    @property
    def planes_azimuth(self) -> List[float]:
        """Compute a list of the azimuths per active planes."""
        planes_azimuth = []

        if self.planes:
            for plane in self.planes:
                azimuth = plane.surface_azimuth
                if azimuth is None:
                    # TODO Use default
                    planes_azimuth.append(float(180))
                else:
                    planes_azimuth.append(float(azimuth))

        return planes_azimuth

    @computed_field  # type: ignore[prop-decorator]
    @property
    def planes_tilt(self) -> List[float]:
        """Compute a list of the tilts per active planes."""
        planes_tilt = []

        if self.planes:
            for plane in self.planes:
                tilt = plane.surface_tilt
                if tilt is None:
                    # TODO Use default
                    planes_tilt.append(float(30))
                else:
                    planes_tilt.append(float(tilt))

        return planes_tilt

    @computed_field  # type: ignore[prop-decorator]
    @property
    def planes_userhorizon(self) -> Any:
        """Compute a list of the user horizon per active planes."""
        planes_userhorizon = []

        if self.planes:
            for plane in self.planes:
                userhorizon = plane.userhorizon
                if userhorizon is None:
                    # TODO Use default
                    planes_userhorizon.append([float(0), float(0)])
                else:
                    planes_userhorizon.append(userhorizon)

        return planes_userhorizon

    @computed_field  # type: ignore[prop-decorator]
    @property
    def planes_inverter_paco(self) -> Any:
        """Compute a list of the maximum power rating of the inverter per active planes."""
        planes_inverter_paco = []

        if self.planes:
            for plane in self.planes:
                inverter_paco = plane.inverter_paco
                if inverter_paco is None:
                    # TODO Use default - no clipping
                    planes_inverter_paco.append(25000.0)
                else:
                    planes_inverter_paco.append(float(inverter_paco))

        return planes_inverter_paco
