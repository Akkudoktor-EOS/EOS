"""PV forecast module for PV power predictions."""

from typing import Any, ClassVar, List, Optional

from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.prediction.pvforecastimport import PVForecastImportCommonSettings

logger = get_logger(__name__)


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
    # pvforecast0_latitude: Optional[float] = Field(default=None, description="Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)")
    # Plane 0
    pvforecast0_surface_tilt: Optional[float] = Field(
        default=None,
        description="Tilt angle from horizontal plane. Ignored for two-axis tracking.",
        examples=[10.0],
    )
    pvforecast0_surface_azimuth: Optional[float] = Field(
        default=None,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
        examples=[10.0],
    )
    pvforecast0_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
        examples=[[10.0, 20.0, 30.0]],
    )
    pvforecast0_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW.", examples=[5.0]
    )
    pvforecast0_pvtechchoice: Optional[str] = Field(
        default="crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast0_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast0_loss: Optional[float] = Field(
        default=14.0, description="Sum of PV system losses in percent"
    )
    pvforecast0_trackingtype: Optional[int] = Field(
        default=None,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
        examples=[0, 1, 2, 3, 4, 5],
    )
    pvforecast0_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
        examples=[False],
    )
    pvforecast0_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
        examples=[False],
    )
    pvforecast0_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
        examples=[None],
    )
    pvforecast0_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane.", examples=[None]
    )
    pvforecast0_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane.", examples=[None]
    )
    pvforecast0_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]", examples=[6000]
    )
    pvforecast0_modules_per_string: Optional[int] = Field(
        default=None,
        description="Number of the PV modules of the strings of this plane.",
        examples=[20],
    )
    pvforecast0_strings_per_inverter: Optional[int] = Field(
        default=None,
        description="Number of the strings of the inverter of this plane.",
        examples=[2],
    )
    # Plane 1
    pvforecast1_surface_tilt: Optional[float] = Field(
        default=None,
        description="Tilt angle from horizontal plane. Ignored for two-axis tracking.",
        examples=[20.0],
    )
    pvforecast1_surface_azimuth: Optional[float] = Field(
        default=None,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
        examples=[20.0],
    )
    pvforecast1_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
        examples=[[5.0, 15.0, 25.0]],
    )
    pvforecast1_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW.", examples=[3.5]
    )
    pvforecast1_pvtechchoice: Optional[str] = Field(
        default="crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast1_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast1_loss: Optional[float] = Field(
        default=14.0, description="Sum of PV system losses in percent"
    )
    pvforecast1_trackingtype: Optional[int] = Field(
        default=None,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
        examples=[None],
    )
    pvforecast1_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
        examples=[False],
    )
    pvforecast1_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
        examples=[False],
    )
    pvforecast1_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
        examples=[None],
    )
    pvforecast1_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane.", examples=[None]
    )
    pvforecast1_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane.", examples=[None]
    )
    pvforecast1_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]", examples=[4000]
    )
    pvforecast1_modules_per_string: Optional[int] = Field(
        default=None,
        description="Number of the PV modules of the strings of this plane.",
        examples=[20],
    )
    pvforecast1_strings_per_inverter: Optional[int] = Field(
        default=None,
        description="Number of the strings of the inverter of this plane.",
        examples=[2],
    )
    # Plane 2
    pvforecast2_surface_tilt: Optional[float] = Field(
        default=None,
        description="Tilt angle from horizontal plane. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast2_surface_azimuth: Optional[float] = Field(
        default=None,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
        examples=[None],
    )
    pvforecast2_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
        examples=[None],
    )
    pvforecast2_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW.", examples=[None]
    )
    pvforecast2_pvtechchoice: Optional[str] = Field(
        default="crystSi",
        description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.",
        examples=[None],
    )
    pvforecast2_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
        examples=[None],
    )
    pvforecast2_loss: Optional[float] = Field(
        default=14.0, description="Sum of PV system losses in percent", examples=[None]
    )
    pvforecast2_trackingtype: Optional[int] = Field(
        default=None,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
        examples=[None],
    )
    pvforecast2_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast2_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast2_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
        examples=[None],
    )
    pvforecast2_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane.", examples=[None]
    )
    pvforecast2_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane.", examples=[None]
    )
    pvforecast2_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]", examples=[None]
    )
    pvforecast2_modules_per_string: Optional[int] = Field(
        default=None,
        description="Number of the PV modules of the strings of this plane.",
        examples=[None],
    )
    pvforecast2_strings_per_inverter: Optional[int] = Field(
        default=None,
        description="Number of the strings of the inverter of this plane.",
        examples=[None],
    )
    # Plane 3
    pvforecast3_surface_tilt: Optional[float] = Field(
        default=None,
        description="Tilt angle from horizontal plane. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast3_surface_azimuth: Optional[float] = Field(
        default=None,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
        examples=[None],
    )
    pvforecast3_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
        examples=[None],
    )
    pvforecast3_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW.", examples=[None]
    )
    pvforecast3_pvtechchoice: Optional[str] = Field(
        default="crystSi",
        description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.",
        examples=[None],
    )
    pvforecast3_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
        examples=[None],
    )
    pvforecast3_loss: Optional[float] = Field(
        default=14.0, description="Sum of PV system losses in percent", examples=[None]
    )
    pvforecast3_trackingtype: Optional[int] = Field(
        default=None,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
        examples=[None],
    )
    pvforecast3_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast3_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast3_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
        examples=[None],
    )
    pvforecast3_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane.", examples=[None]
    )
    pvforecast3_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane.", examples=[None]
    )
    pvforecast3_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]", examples=[None]
    )
    pvforecast3_modules_per_string: Optional[int] = Field(
        default=None,
        description="Number of the PV modules of the strings of this plane.",
        examples=[None],
    )
    pvforecast3_strings_per_inverter: Optional[int] = Field(
        default=None,
        description="Number of the strings of the inverter of this plane.",
        examples=[None],
    )
    # Plane 4
    pvforecast4_surface_tilt: Optional[float] = Field(
        default=None,
        description="Tilt angle from horizontal plane. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast4_surface_azimuth: Optional[float] = Field(
        default=None,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
        examples=[None],
    )
    pvforecast4_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
        examples=[None],
    )
    pvforecast4_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW.", examples=[None]
    )
    pvforecast4_pvtechchoice: Optional[str] = Field(
        default="crystSi",
        description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.",
        examples=[None],
    )
    pvforecast4_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
        examples=[None],
    )
    pvforecast4_loss: Optional[float] = Field(
        default=14.0, description="Sum of PV system losses in percent", examples=[None]
    )
    pvforecast4_trackingtype: Optional[int] = Field(
        default=None,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
        examples=[None],
    )
    pvforecast4_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast4_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast4_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
        examples=[None],
    )
    pvforecast4_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane.", examples=[None]
    )
    pvforecast4_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane.", examples=[None]
    )
    pvforecast4_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]", examples=[None]
    )
    pvforecast4_modules_per_string: Optional[int] = Field(
        default=None,
        description="Number of the PV modules of the strings of this plane.",
        examples=[None],
    )
    pvforecast4_strings_per_inverter: Optional[int] = Field(
        default=None,
        description="Number of the strings of the inverter of this plane.",
        examples=[None],
    )
    # Plane 5
    pvforecast5_surface_tilt: Optional[float] = Field(
        default=None,
        description="Tilt angle from horizontal plane. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast5_surface_azimuth: Optional[float] = Field(
        default=None,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
        examples=[None],
    )
    pvforecast5_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
        examples=[None],
    )
    pvforecast5_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW.", examples=[None]
    )
    pvforecast5_pvtechchoice: Optional[str] = Field(
        default="crystSi",
        description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.",
        examples=[None],
    )
    pvforecast5_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
        examples=[None],
    )
    pvforecast5_loss: Optional[float] = Field(
        default=14.0, description="Sum of PV system losses in percent", examples=[None]
    )
    pvforecast5_trackingtype: Optional[int] = Field(
        default=None,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
        examples=[None],
    )
    pvforecast5_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast5_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
        examples=[None],
    )
    pvforecast5_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
        examples=[None],
    )
    pvforecast5_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane.", examples=[None]
    )
    pvforecast5_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane.", examples=[None]
    )
    pvforecast5_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]", examples=[None]
    )
    pvforecast5_modules_per_string: Optional[int] = Field(
        default=None,
        description="Number of the PV modules of the strings of this plane.",
        examples=[None],
    )
    pvforecast5_strings_per_inverter: Optional[int] = Field(
        default=None,
        description="Number of the strings of the inverter of this plane.",
        examples=[None],
    )

    pvforecast_max_planes: ClassVar[int] = 6  # Maximum number of planes that can be set

    provider_settings: Optional[PVForecastImportCommonSettings] = Field(
        default=None, description="Provider settings", examples=[None]
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes(self) -> List[str]:
        """Compute a list of active planes."""
        active_planes = []

        # Loop through pvforecast0 to pvforecast4
        for i in range(self.pvforecast_max_planes):
            plane = f"pvforecast{i}"
            tackingtype_attr = f"{plane}_trackingtype"
            tilt_attr = f"{plane}_surface_tilt"
            azimuth_attr = f"{plane}_surface_azimuth"

            # Check if either attribute is set and add to active planes
            if getattr(self, tackingtype_attr, None) == 2:
                # Tilt angle from horizontal plane is gnored for two-axis tracking.
                if getattr(self, azimuth_attr, None) is not None:
                    active_planes.append(f"pvforecast{i}")
            elif getattr(self, tilt_attr, None) and getattr(self, azimuth_attr, None):
                active_planes.append(f"pvforecast{i}")

        return active_planes

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_peakpower(self) -> List[float]:
        """Compute a list of the peak power per active planes."""
        planes_peakpower = []

        for plane in self.pvforecast_planes:
            peakpower_attr = f"{plane}_peakpower"
            peakpower = getattr(self, peakpower_attr, None)
            if peakpower is None:
                # TODO calculate peak power from modules/strings
                planes_peakpower.append(float(5000))
            else:
                planes_peakpower.append(float(peakpower))

        return planes_peakpower

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_azimuth(self) -> List[float]:
        """Compute a list of the azimuths per active planes."""
        planes_azimuth = []

        for plane in self.pvforecast_planes:
            azimuth_attr = f"{plane}_surface_azimuth"
            azimuth = getattr(self, azimuth_attr, None)
            if azimuth is None:
                # TODO Use default
                planes_azimuth.append(float(180))
            else:
                planes_azimuth.append(float(azimuth))

        return planes_azimuth

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_tilt(self) -> List[float]:
        """Compute a list of the tilts per active planes."""
        planes_tilt = []

        for plane in self.pvforecast_planes:
            tilt_attr = f"{plane}_surface_tilt"
            tilt = getattr(self, tilt_attr, None)
            if tilt is None:
                # TODO Use default
                planes_tilt.append(float(30))
            else:
                planes_tilt.append(float(tilt))

        return planes_tilt

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_userhorizon(self) -> Any:
        """Compute a list of the user horizon per active planes."""
        planes_userhorizon = []

        for plane in self.pvforecast_planes:
            userhorizon_attr = f"{plane}_userhorizon"
            userhorizon = getattr(self, userhorizon_attr, None)
            if userhorizon is None:
                # TODO Use default
                planes_userhorizon.append([float(0), float(0)])
            else:
                planes_userhorizon.append(userhorizon)

        return planes_userhorizon

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_inverter_paco(self) -> Any:
        """Compute a list of the maximum power rating of the inverter per active planes."""
        planes_inverter_paco = []

        for plane in self.pvforecast_planes:
            inverter_paco_attr = f"{plane}_inverter_paco"
            inverter_paco = getattr(self, inverter_paco_attr, None)
            if inverter_paco is None:
                # TODO Use default - no clipping
                planes_inverter_paco.append(25000.0)
            else:
                planes_inverter_paco.append(float(inverter_paco))

        return planes_inverter_paco
