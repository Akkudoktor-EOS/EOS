"""PV forecast module for PV power predictions."""

from typing import Any, ClassVar, List, Optional

from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class PVForecastCommonSettings(SettingsBaseModel):
    # General plane parameters
    #     https://pvlib-python.readthedocs.io/en/stable/_modules/pvlib/iotools/pvgis.html
    # Inverter Parameters
    #     https://pvlib-python.readthedocs.io/en/stable/_modules/pvlib/inverter.html

    pvforecast_provider: Optional[str] = Field(
        default=None, description="PVForecast provider id of provider to be used."
    )
    # pvforecast0_latitude: Optional[float] = Field(default=None, description="Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)")
    # Plane 0
    pvforecast0_surface_tilt: Optional[float] = Field(
        default=0, description="Tilt angle from horizontal plane. Ignored for two-axis tracking."
    )
    pvforecast0_surface_azimuth: Optional[float] = Field(
        default=180,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
    )
    pvforecast0_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
    )
    pvforecast0_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW."
    )
    pvforecast0_pvtechchoice: Optional[str] = Field(
        default="crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast0_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast0_loss: Optional[float] = Field(
        default=None, description="Sum of PV system losses in percent"
    )
    pvforecast0_trackingtype: Optional[int] = Field(
        default=0,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
    )
    pvforecast0_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
    )
    pvforecast0_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
    )
    pvforecast0_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
    )
    pvforecast0_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane."
    )
    pvforecast0_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane."
    )
    pvforecast0_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]"
    )
    pvforecast0_modules_per_string: Optional[str] = Field(
        default=None, description="Number of the PV modules of the strings of this plane."
    )
    pvforecast0_strings_per_inverter: Optional[str] = Field(
        default=None, description="Number of the strings of the inverter of this plane."
    )
    # Plane 1
    pvforecast1_surface_tilt: Optional[float] = Field(
        default=0, description="Tilt angle from horizontal plane. Ignored for two-axis tracking."
    )
    pvforecast1_surface_azimuth: Optional[float] = Field(
        default=180,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
    )
    pvforecast1_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
    )
    pvforecast1_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW."
    )
    pvforecast1_pvtechchoice: Optional[str] = Field(
        default="crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast1_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast1_loss: Optional[float] = Field(0, description="Sum of PV system losses in percent")
    pvforecast1_trackingtype: Optional[int] = Field(
        default=0,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
    )
    pvforecast1_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
    )
    pvforecast1_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
    )
    pvforecast1_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
    )
    pvforecast1_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane."
    )
    pvforecast1_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane."
    )
    pvforecast1_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]"
    )
    pvforecast1_modules_per_string: Optional[str] = Field(
        default=None, description="Number of the PV modules of the strings of this plane."
    )
    pvforecast1_strings_per_inverter: Optional[str] = Field(
        default=None, description="Number of the strings of the inverter of this plane."
    )
    # Plane 2
    pvforecast2_surface_tilt: Optional[float] = Field(
        default=0, description="Tilt angle from horizontal plane. Ignored for two-axis tracking."
    )
    pvforecast2_surface_azimuth: Optional[float] = Field(
        default=180,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
    )
    pvforecast2_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
    )
    pvforecast2_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW."
    )
    pvforecast2_pvtechchoice: Optional[str] = Field(
        default="crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast2_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast2_loss: Optional[float] = Field(0, description="Sum of PV system losses in percent")
    pvforecast2_trackingtype: Optional[int] = Field(
        default=0,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
    )
    pvforecast2_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
    )
    pvforecast2_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
    )
    pvforecast2_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
    )
    pvforecast2_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane."
    )
    pvforecast2_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane."
    )
    pvforecast2_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]"
    )
    pvforecast2_modules_per_string: Optional[str] = Field(
        default=None, description="Number of the PV modules of the strings of this plane."
    )
    pvforecast2_strings_per_inverter: Optional[str] = Field(
        default=None, description="Number of the strings of the inverter of this plane."
    )
    # Plane 3
    pvforecast3_surface_tilt: Optional[float] = Field(
        default=0, description="Tilt angle from horizontal plane. Ignored for two-axis tracking."
    )
    pvforecast3_surface_azimuth: Optional[float] = Field(
        default=180,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
    )
    pvforecast3_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
    )
    pvforecast3_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW."
    )
    pvforecast3_pvtechchoice: Optional[str] = Field(
        default="crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast3_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast3_loss: Optional[float] = Field(0, description="Sum of PV system losses in percent")
    pvforecast3_trackingtype: Optional[int] = Field(
        default=0,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
    )
    pvforecast3_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
    )
    pvforecast3_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
    )
    pvforecast3_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
    )
    pvforecast3_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane."
    )
    pvforecast3_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane."
    )
    pvforecast3_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]"
    )
    pvforecast3_modules_per_string: Optional[str] = Field(
        default=None, description="Number of the PV modules of the strings of this plane."
    )
    pvforecast3_strings_per_inverter: Optional[str] = Field(
        default=None, description="Number of the strings of the inverter of this plane."
    )
    # Plane 4
    pvforecast4_surface_tilt: Optional[float] = Field(
        default=0, description="Tilt angle from horizontal plane. Ignored for two-axis tracking."
    )
    pvforecast4_surface_azimuth: Optional[float] = Field(
        default=180,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
    )
    pvforecast4_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
    )
    pvforecast4_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW."
    )
    pvforecast4_pvtechchoice: Optional[str] = Field(
        "crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast4_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast4_loss: Optional[float] = Field(0, description="Sum of PV system losses in percent")
    pvforecast4_trackingtype: Optional[int] = Field(
        default=0,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
    )
    pvforecast4_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
    )
    pvforecast4_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
    )
    pvforecast4_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
    )
    pvforecast4_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane."
    )
    pvforecast4_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane."
    )
    pvforecast4_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]"
    )
    pvforecast4_modules_per_string: Optional[str] = Field(
        default=None, description="Number of the PV modules of the strings of this plane."
    )
    pvforecast4_strings_per_inverter: Optional[str] = Field(
        default=None, description="Number of the strings of the inverter of this plane."
    )
    # Plane 5
    pvforecast5_surface_tilt: Optional[float] = Field(
        default=0, description="Tilt angle from horizontal plane. Ignored for two-axis tracking."
    )
    pvforecast5_surface_azimuth: Optional[float] = Field(
        default=180,
        description="Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).",
    )
    pvforecast5_userhorizon: Optional[List[float]] = Field(
        default=None,
        description="Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.",
    )
    pvforecast5_peakpower: Optional[float] = Field(
        default=None, description="Nominal power of PV system in kW."
    )
    pvforecast5_pvtechchoice: Optional[str] = Field(
        "crystSi", description="PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'."
    )
    pvforecast5_mountingplace: Optional[str] = Field(
        default="free",
        description="Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.",
    )
    pvforecast5_loss: Optional[float] = Field(0, description="Sum of PV system losses in percent")
    pvforecast5_trackingtype: Optional[int] = Field(
        default=0,
        description="Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.",
    )
    pvforecast5_optimal_surface_tilt: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt angle. Ignored for two-axis tracking.",
    )
    pvforecast5_optimalangles: Optional[bool] = Field(
        default=False,
        description="Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.",
    )
    pvforecast5_albedo: Optional[float] = Field(
        default=None,
        description="Proportion of the light hitting the ground that it reflects back.",
    )
    pvforecast5_module_model: Optional[str] = Field(
        default=None, description="Model of the PV modules of this plane."
    )
    pvforecast5_inverter_model: Optional[str] = Field(
        default=None, description="Model of the inverter of this plane."
    )
    pvforecast5_inverter_paco: Optional[int] = Field(
        default=None, description="AC power rating of the inverter. [W]"
    )
    pvforecast5_modules_per_string: Optional[str] = Field(
        default=None, description="Number of the PV modules of the strings of this plane."
    )
    pvforecast5_strings_per_inverter: Optional[str] = Field(
        default=None, description="Number of the strings of the inverter of this plane."
    )

    pvforecast_max_planes: ClassVar[int] = 6  # Maximum number of planes that can be set

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes(self) -> List[str]:
        """Compute a list of active planes."""
        active_planes = []

        # Loop through pvforecast0 to pvforecast4
        for i in range(self.pvforecast_max_planes):
            peakpower_attr = f"pvforecast{i}_peakpower"
            modules_attr = f"pvforecast{i}_modules_per_string"

            # Check if either attribute is set and add to active planes
            if getattr(self, peakpower_attr, None) or getattr(self, modules_attr, None):
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
            if peakpower:
                planes_peakpower.append(float(peakpower))
                continue
            # TODO calculate peak power from modules/strings
            planes_peakpower.append(float(5000))

        return planes_peakpower

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_azimuth(self) -> List[float]:
        """Compute a list of the azimuths per active planes."""
        planes_azimuth = []

        for plane in self.pvforecast_planes:
            azimuth_attr = f"{plane}_azimuth"
            azimuth = getattr(self, azimuth_attr, None)
            if azimuth:
                planes_azimuth.append(float(azimuth))
                continue
            # TODO Use default
            planes_azimuth.append(float(180))

        return planes_azimuth

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_tilt(self) -> List[float]:
        """Compute a list of the tilts per active planes."""
        planes_tilt = []

        for plane in self.pvforecast_planes:
            tilt_attr = f"{plane}_tilt"
            tilt = getattr(self, tilt_attr, None)
            if tilt:
                planes_tilt.append(float(tilt))
                continue
            # TODO Use default
            planes_tilt.append(float(0))

        return planes_tilt

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_userhorizon(self) -> Any:
        """Compute a list of the user horizon per active planes."""
        planes_userhorizon = []

        for plane in self.pvforecast_planes:
            userhorizon_attr = f"{plane}_userhorizon"
            userhorizon = getattr(self, userhorizon_attr, None)
            if userhorizon:
                planes_userhorizon.append(userhorizon)
                continue
            # TODO Use default
            planes_userhorizon.append([float(0), float(0)])

        return planes_userhorizon

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecast_planes_inverter_paco(self) -> Any:
        """Compute a list of the maximum power rating of the inverter per active planes."""
        planes_inverter_paco = []

        for plane in self.pvforecast_planes:
            inverter_paco_attr = f"{plane}_inverter_paco"
            inverter_paco = getattr(self, inverter_paco_attr, None)
            if inverter_paco:
                planes_inverter_paco.append(inverter_paco)
                continue
            # TODO Use default - no clipping
            planes_inverter_paco.append(25000)

        return planes_inverter_paco
