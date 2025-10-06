"""Energy management plan.

The energy management plan is leaned on to the S2 standard.

This module provides data models and enums for energy resource management
following the S2 standard, supporting various control types including Power Envelope Based Control,
Power Profile Based Control, Operation Mode Based Control, Fill Rate Based Control, and
Demand Driven Based Control.
"""

import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import Field, computed_field, model_validator

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import DateTime, Duration, to_datetime

# S2 Basic Data Types
# - Array -> list
# - Boolean -> bool
# - DateTimeStamp -> DateTime
# - Duration -> Duration
# - ID -> alias on str
# - Number -> float
# - String -> str

ID = str


# S2 Enumerations


class RoleType(str, Enum):
    """Enumeration of energy resource roles in the system."""

    ENERGY_PRODUCER = "ENERGY_PRODUCER"
    ENERGY_CONSUMER = "ENERGY_CONSUMER"
    ENERGY_STORAGE = "ENERGY_STORAGE"


class Commodity(str, Enum):
    """Enumeration of energy commodities supported in the system."""

    GAS = "GAS"
    HEAT = "HEAT"
    ELECTRICITY = "ELECTRICITY"
    OIL = "OIL"


class CommodityQuantity(str, Enum):
    """Enumeration of specific commodity quantities and measurement types."""

    ELECTRIC_POWER_L1 = "ELECTRIC.POWER.L1"
    """Electric power in Watt on phase 1. If a device utilizes only one phase, it should always use L1."""

    ELECTRIC_POWER_L2 = "ELECTRIC.POWER.L2"
    """Electric power in Watt on phase 2. Only applicable for 3-phase devices."""

    ELECTRIC_POWER_L3 = "ELECTRIC.POWER.L3"
    """Electric power in Watt on phase 3. Only applicable for 3-phase devices."""

    ELECTRIC_POWER_3_PHASE_SYM = "ELECTRIC.POWER.3_PHASE_SYM"
    """Electric power in Watt when power is equally shared among the three phases. Only applicable for 3-phase devices."""

    NATURAL_GAS_FLOW_RATE = "NATURAL_GAS.FLOW_RATE"
    """Gas flow rate described in liters per second."""

    HYDROGEN_FLOW_RATE = "HYDROGEN.FLOW_RATE"
    """Hydrogen flow rate described in grams per second."""

    HEAT_TEMPERATURE = "HEAT.TEMPERATURE"
    """Heat temperature described in degrees Celsius."""

    HEAT_FLOW_RATE = "HEAT.FLOW_RATE"
    """Flow rate of heat-carrying gas or liquid in liters per second."""

    HEAT_THERMAL_POWER = "HEAT.THERMAL_POWER"
    """Thermal power in Watt."""

    OIL_FLOW_RATE = "OIL.FLOW_RATE"
    """Oil flow rate described in liters per hour."""

    CURRENCY = "CURRENCY"
    """Currency-related quantity."""


class Currency(str, Enum):
    """Enumeration of currency codes following ISO 4217 standard."""

    AED = "AED"
    AFN = "AFN"
    ALL = "ALL"
    AMD = "AMD"
    ANG = "ANG"
    AOA = "AOA"
    ARS = "ARS"
    AUD = "AUD"
    AWG = "AWG"
    AZN = "AZN"
    BAM = "BAM"
    BBD = "BBD"
    BDT = "BDT"
    BGN = "BGN"
    BHD = "BHD"
    BIF = "BIF"
    BMD = "BMD"
    BND = "BND"
    BOB = "BOB"
    BRL = "BRL"
    BSD = "BSD"
    BTN = "BTN"
    BWP = "BWP"
    BYN = "BYN"
    BZD = "BZD"
    CAD = "CAD"
    CDF = "CDF"
    CHF = "CHF"
    CLP = "CLP"
    CNY = "CNY"
    COP = "COP"
    CRC = "CRC"
    CUP = "CUP"
    CVE = "CVE"
    CZK = "CZK"
    DJF = "DJF"
    DKK = "DKK"
    DOP = "DOP"
    DZD = "DZD"
    EGP = "EGP"
    ERN = "ERN"
    ETB = "ETB"
    EUR = "EUR"
    FJD = "FJD"
    FKP = "FKP"
    FOK = "FOK"
    GBP = "GBP"
    GEL = "GEL"
    GGP = "GGP"
    GHS = "GHS"
    GIP = "GIP"
    GMD = "GMD"
    GNF = "GNF"
    GTQ = "GTQ"
    GYD = "GYD"
    HKD = "HKD"
    HNL = "HNL"
    HRK = "HRK"
    HTG = "HTG"
    HUF = "HUF"
    IDR = "IDR"
    ILS = "ILS"
    IMP = "IMP"
    INR = "INR"
    IQD = "IQD"
    IRR = "IRR"
    ISK = "ISK"
    JEP = "JEP"
    JMD = "JMD"
    JOD = "JOD"
    JPY = "JPY"
    KES = "KES"
    KGS = "KGS"
    KHR = "KHR"
    KID = "KID"
    KMF = "KMF"
    KRW = "KRW"
    KWD = "KWD"
    KYD = "KYD"
    KZT = "KZT"
    LAK = "LAK"
    LBP = "LBP"
    LKR = "LKR"
    LRD = "LRD"
    LSL = "LSL"
    LYD = "LYD"
    MAD = "MAD"
    MDL = "MDL"
    MGA = "MGA"
    MKD = "MKD"
    MMK = "MMK"
    MNT = "MNT"
    MOP = "MOP"
    MRU = "MRU"
    MUR = "MUR"
    MVR = "MVR"
    MWK = "MWK"
    MXN = "MXN"
    MYR = "MYR"
    MZN = "MZN"
    NAD = "NAD"
    NGN = "NGN"
    NIO = "NIO"
    NOK = "NOK"
    NPR = "NPR"
    NZD = "NZD"
    OMR = "OMR"
    PAB = "PAB"
    PEN = "PEN"
    PGK = "PGK"
    PHP = "PHP"
    PKR = "PKR"
    PLN = "PLN"
    PYG = "PYG"
    QAR = "QAR"
    RON = "RON"
    RSD = "RSD"
    RUB = "RUB"
    RWF = "RWF"
    SAR = "SAR"
    SBD = "SBD"
    SCR = "SCR"
    SDG = "SDG"
    SEK = "SEK"
    SGD = "SGD"
    SHP = "SHP"
    SLE = "SLE"
    SLL = "SLL"
    SOS = "SOS"
    SRD = "SRD"
    SSP = "SSP"
    STN = "STN"
    SYP = "SYP"
    SZL = "SZL"
    THB = "THB"
    TJS = "TJS"
    TMT = "TMT"
    TND = "TND"
    TOP = "TOP"
    TRY = "TRY"
    TTD = "TTD"
    TVD = "TVD"
    TWD = "TWD"
    TZS = "TZS"
    UAH = "UAH"
    UGX = "UGX"
    USD = "USD"
    UYU = "UYU"
    UZS = "UZS"
    VES = "VES"
    VND = "VND"
    VUV = "VUV"
    WST = "WST"
    XAF = "XAF"
    XCD = "XCD"
    XOF = "XOF"
    XPF = "XPF"
    YER = "YER"
    ZAR = "ZAR"
    ZMW = "ZMW"
    ZWL = "ZWL"


class InstructionStatus(str, Enum):
    """Enumeration of possible instruction status values."""

    NEW = "NEW"  # Instruction was newly created
    ACCEPTED = "ACCEPTED"  # Instruction has been accepted
    REJECTED = "REJECTED"  # Instruction was rejected
    REVOKED = "REVOKED"  # Instruction was revoked
    STARTED = "STARTED"  # Instruction was executed
    SUCCEEDED = "SUCCEEDED"  # Instruction finished successfully
    ABORTED = "ABORTED"  # Instruction was aborted


class ControlType(str, Enum):
    """Enumeration of different control types supported by the system."""

    POWER_ENVELOPE_BASED_CONTROL = (
        "POWER_ENVELOPE_BASED_CONTROL"  # Identifier for the Power Envelope Based Control type
    )
    POWER_PROFILE_BASED_CONTROL = (
        "POWER_PROFILE_BASED_CONTROL"  # Identifier for the Power Profile Based Control type
    )
    OPERATION_MODE_BASED_CONTROL = (
        "OPERATION_MODE_BASED_CONTROL"  # Identifier for the Operation Mode Based Control type
    )
    FILL_RATE_BASED_CONTROL = (
        "FILL_RATE_BASED_CONTROL"  # Identifier for the Fill Rate Based Control type
    )
    DEMAND_DRIVEN_BASED_CONTROL = (
        "DEMAND_DRIVEN_BASED_CONTROL"  # Identifier for the Demand Driven Based Control type
    )
    NOT_CONTROLABLE = "NOT_CONTROLABLE"  # Used if no control is possible; resource can still provide forecasts and measurements
    NO_SELECTION = "NO_SELECTION"  # Used if no control type is/has been selected


class PEBCPowerEnvelopeLimitType(str, Enum):
    """Enumeration of power envelope limit types for Power Envelope Based Control."""

    UPPER_LIMIT = "UPPER_LIMIT"  # Indicates the upper limit of a Power Envelope
    LOWER_LIMIT = "LOWER_LIMIT"  # Indicates the lower limit of a Power Envelope


class PEBCPowerEnvelopeConsequenceType(str, Enum):
    """Enumeration of consequences when power is limited for Power Envelope Based Control."""

    VANISH = "VANISH"  # Limited load or generation will be lost and not reappear
    DEFER = "DEFER"  # Limited load or generation will be postponed to a later moment


class ReceptionStatusValues(str, Enum):
    """Enumeration of status values for data reception."""

    SUCCEEDED = "SUCCEEDED"  # Data received, complete, and consistent
    REJECTED = "REJECTED"  # Data could not be parsed or was incomplete/inconsistent


class PPBCPowerSequenceStatus(str, Enum):
    """Enumeration of status values for Power Profile Based Control sequences."""

    NOT_SCHEDULED = "NOT_SCHEDULED"  # No PowerSequence is scheduled
    SCHEDULED = "SCHEDULED"  # PowerSequence is scheduled for future execution
    EXECUTING = "EXECUTING"  # PowerSequence is currently being executed
    INTERRUPTED = "INTERRUPTED"  # Execution is currently interrupted and will continue later
    FINISHED = "FINISHED"  # PowerSequence finished successfully
    ABORTED = "ABORTED"  # PowerSequence was aborted and will not continue


# S2 Basic Values


class PowerValue(PydanticBaseModel):
    """Represents a specific power value measurement with its associated commodity quantity.

    This class links a numerical power value to a specific type of power quantity (such as
    active power, reactive power, etc.) and its unit of measurement.
    """

    commodity_quantity: CommodityQuantity = Field(
        ..., description="The power quantity the value refers to."
    )
    value: float = Field(
        ..., description="Power value expressed in the unit associated with the CommodityQuantity."
    )


class PowerForecastValue(PydanticBaseModel):
    """Represents a forecasted power value with statistical confidence intervals.

    This model provides a complete statistical representation of a power forecast,
    including the expected value and multiple confidence intervals (68%, 95%, and absolute limits).
    Each forecast is associated with a specific commodity quantity.
    """

    value_upper_limit: Optional[float] = Field(
        None,
        description="The upper boundary of the range with 100% certainty the power value is in it.",
    )
    value_upper_95PPR: Optional[float] = Field(
        None,
        description="The upper boundary of the range with 95% certainty the power value is in it.",
    )
    value_upper_68PPR: Optional[float] = Field(
        None,
        description="The upper boundary of the range with 68% certainty the power value is in it.",
    )
    value_expected: float = Field(..., description="The expected power value.")
    value_lower_68PPR: Optional[float] = Field(
        None,
        description="The lower boundary of the range with 68% certainty the power value is in it.",
    )
    value_lower_95PPR: Optional[float] = Field(
        None,
        description="The lower boundary of the range with 95% certainty the power value is in it.",
    )
    value_lower_limit: Optional[float] = Field(
        None,
        description="The lower boundary of the range with 100% certainty the power value is in it.",
    )
    commodity_quantity: CommodityQuantity = Field(
        ..., description="The power quantity the value refers to."
    )


class PowerRange(PydanticBaseModel):
    """Defines a range of acceptable power values for a specific commodity quantity.

    This model specifies the minimum and maximum values for a power parameter,
    creating operational boundaries for energy systems. This range is used for
    defining permissible operating conditions or constraints.
    """

    start_of_range: float = Field(
        ..., description="Power value that defines the start of the range."
    )
    end_of_range: float = Field(..., description="Power value that defines the end of the range.")
    commodity_quantity: CommodityQuantity = Field(
        ..., description="The power quantity the values refer to."
    )


class NumberRange(PydanticBaseModel):
    """Defines a generic numeric range with start and end values.

    Unlike PowerRange, this model is not tied to a specific commodity quantity
    and can be used for any numeric range definition throughout the system.
    Used for representing ranges of prices, percentages, or other numeric values.
    """

    start_of_range: float = Field(..., description="Number that defines the start of the range.")
    end_of_range: float = Field(..., description="Number that defines the end of the range.")


class PowerMeasurement(PydanticBaseModel):
    """Captures a set of power measurements taken at a specific point in time.

    This model records multiple power values (for different commodity quantities)
    along with the timestamp when the measurements were taken, enabling time-series
    analysis and monitoring of power consumption or production.
    """

    type: Literal["PowerMeasurement"] = Field(default="PowerMeasurement")

    measurement_timestamp: DateTime = Field(
        ..., description="Timestamp when PowerValues were measured."
    )
    values: list[PowerValue] = Field(
        ...,
        description="Array of measured PowerValues. Shall contain at least one item and at most one item per 'commodity_quantity' (defined inside the PowerValue).",
    )


class EnergyMeasurement(PydanticBaseModel):
    """Captures a set of energy meter readouts taken at a specific point in time.

    Energy is defined as the cummulative power per hour as provided by an energy meter.

    This model records multiple energy values (for different commodity quantities)
    along with the timestamp when the meter readouts were taken, enabling time-series
    analysis and monitoring of energy consumption or production.

    Note: This is an extension to the S2 standard.
    """

    type: Literal["EnergyMeasurement"] = Field(default="EnergyMeasurement")

    measurement_timestamp: DateTime = Field(
        ..., description="Timestamp when energy values were measured."
    )
    last_reset: Optional[DateTime] = Field(
        default=None,
        description="Timestamp when the energy meter's cumulative counter was last reset.",
    )
    values: list[PowerValue] = Field(
        ...,
        description="Array of measured energy values. Shall contain at least one item and at most one item per 'commodity_quantity' (defined inside the PowerValue).",
    )


class Role(PydanticBaseModel):
    """Defines an energy system role related to a specific commodity.

    This model links a role type (such as consumer, producer, or storage) with
    a specific energy commodity (electricity, gas, heat, etc.), defining how
    an entity interacts with the energy system for that commodity.
    """

    role: RoleType = Field(..., description="Role type for the given commodity.")
    commodity: Commodity = Field(..., description="Commodity the role refers to.")


class ReceptionStatus(PydanticBaseModel):
    """Represents the status of a data reception operation with optional diagnostic information.

    This model tracks whether data was successfully received, with additional
    diagnostic information for debugging purposes. It serves as a feedback mechanism
    for communication operations within the system.
    """

    status: ReceptionStatusValues = Field(
        ..., description="Enumeration of status values indicating reception outcome."
    )
    diagnostic_label: Optional[str] = Field(
        None,
        description=(
            "Optional diagnostic label providing additional information for debugging. "
            "Not intended for Human-Machine Interface (HMI) use."
        ),
    )


class Transition(PydanticBaseModel):
    """Defines a permitted transition between operation modes with associated constraints and costs.

    This model represents the rules and constraints governing how a system can move
    between different operation modes. It includes information about timing constraints,
    costs associated with the transition, expected duration, and whether the transition
    is only allowed during abnormal conditions.
    """

    id: ID = Field(
        ...,
        description=(
            "ID of the Transition. Shall be unique in the scope of the OMBC.SystemDescription, "
            "FRBC.ActuatorDescription, or DDBC.ActuatorDescription in which it is used."
        ),
    )
    from_: ID = Field(
        ...,
        alias="from",
        description=(
            "ID of the OperationMode that should be switched from. "
            "Exact type depends on the ControlType."
        ),
    )
    to: ID = Field(
        ...,
        description=(
            "ID of the OperationMode that will be switched to. "
            "Exact type depends on the ControlType."
        ),
    )
    start_timers: list[ID] = Field(
        ...,
        description=(
            "List of IDs of Timers that will be (re)started when this Transition is initiated."
        ),
    )
    blocking_timers: list[ID] = Field(
        ...,
        description=(
            "List of IDs of Timers that block this Transition from initiating "
            "while at least one of them is not yet finished."
        ),
    )
    transition_costs: Optional[float] = Field(
        None,
        description=(
            "Absolute costs for going through this Transition, in the currency defined in ResourceManagerDetails."
        ),
    )
    transition_duration: Optional[Duration] = Field(
        None,
        description=(
            "Time between initiation of this Transition and when the device behaves according to the target Operation Mode. "
            "Assumed negligible if not provided."
        ),
    )
    abnormal_condition_only: bool = Field(
        ...,
        description=(
            "Indicates whether this Transition may only be used during an abnormal condition."
        ),
    )

    model_config = {
        "populate_by_name": True,  # Enables using 'from_' as 'from' during model population
        "extra": "forbid",
    }


class Timer(PydanticBaseModel):
    """Defines a timing constraint for transitions between operation modes.

    This model implements time-based constraints for state transitions in the system,
    tracking both the duration of the timer and when it will complete. Timers are used
    to enforce minimum dwell times in states, cooldown periods, or other timing-related
    operational constraints.
    """

    id: ID = Field(
        ...,
        description=(
            "ID of the Timer. Shall be unique in the scope of the OMBC.SystemDescription, "
            "FRBC.ActuatorDescription, or DDBC.ActuatorDescription in which it is used."
        ),
    )
    diagnostic_label: Optional[str] = Field(
        None,
        description=(
            "Human readable name/description of the Timer. "
            "This element is only intended for diagnostic purposes and not for HMI applications."
        ),
    )
    duration: Duration = Field(
        ..., description=("The time it takes for the Timer to finish after it has been started.")
    )
    finished_at: DateTime = Field(
        ...,
        description=(
            "Timestamp indicating when the Timer will be finished. "
            "If in the future, the timer is not yet finished. "
            "If in the past, the timer is finished. "
            "If the timer was never started, this can be an arbitrary timestamp in the past."
        ),
    )


class InstructionStatusUpdate(PydanticBaseModel):
    """Represents an update to the status of a control instruction.

    This model tracks the progress and current state of a control instruction,
    including when its status last changed. It enables monitoring of instruction
    execution and provides feedback about the system's response to control commands.
    """

    instruction_id: ID = Field(..., description=("ID of this instruction, as provided by the CEM."))
    status_type: InstructionStatus = Field(..., description=("Present status of this instruction."))
    timestamp: DateTime = Field(..., description=("Timestamp when the status_type last changed."))


# ResourceManager


class ResourceManagerDetails(PydanticBaseModel):
    """Provides comprehensive details about a ResourceManager's capabilities and identity.

    This model defines the core characteristics of a ResourceManager, including its
    identification, supported energy roles, control capabilities, and technical specifications.
    It serves as the primary descriptor for a device or system that can be controlled
    by a Customer Energy Manager (CEM).
    """

    resource_id: ID = Field(
        ...,
        description="Identifier of the ResourceManager. Shall be unique within the scope of the CEM.",
    )
    name: Optional[str] = Field(None, description="Human readable name given by user.")
    roles: list[Role] = Field(
        ..., description="Each ResourceManager provides one or more energy Roles."
    )
    manufacturer: Optional[str] = Field(None, description="Name of Manufacturer.")
    model: Optional[str] = Field(None, description="Name of the model of the device.")
    serial_number: Optional[str] = Field(None, description="Serial number of the device.")
    firmware_version: Optional[str] = Field(
        None, description="Version identifier of the firmware used in the device."
    )
    instruction_processing_delay: Duration = Field(
        ...,
        description="The average time the system and device needs to process and execute an instruction.",
    )
    available_control_types: list[ControlType] = Field(
        ..., description="The control types supported by this ResourceManager."
    )
    currency: Optional[Currency] = Field(
        None,
        description="Currency to be used for all information regarding costs. "
        "Mandatory if cost information is published.",
    )
    provides_forecast: bool = Field(
        ..., description="Indicates whether the ResourceManager is able to provide PowerForecasts."
    )
    provides_power_measurement_types: list[CommodityQuantity] = Field(
        ...,
        description="Array of all CommodityQuantities that this ResourceManager can provide measurements for.",
    )


# PowerForecast


class PowerForecastElement(PydanticBaseModel):
    """Represents a segment of a power forecast covering a specific time duration.

    This model defines power forecast values for a specific time period, with multiple
    power values potentially covering different commodity quantities. It is used to
    construct time-series forecasts of future power production or consumption.
    """

    duration: Duration = Field(
        ...,
        description=(
            "Duration of the PowerForecastElement. "
            "Defines the time window the power values apply to."
        ),
    )
    power_values: list[PowerForecastValue] = Field(
        ...,
        min_length=1,
        description=(
            "The values of power that are expected for the given period. "
            "There shall be at least one PowerForecastValue, and at most one per CommodityQuantity."
        ),
    )


class PowerForecast(PydanticBaseModel):
    """Represents a power forecast profile consisting of one or more forecast elements.

    This model defines a time-series forecast of power production or consumption
    starting from a specified point in time. It consists of sequential forecast elements,
    each covering a specific duration with associated power values for different
    commodity quantities.

    Attributes:
        start_time (DateTime): Start time of the period covered by the forecast.
        elements (list[PowerForecastElement]): Chronologically ordered forecast segments.
    """

    start_time: DateTime = Field(
        ..., description="Start time of time period that is covered by the profile."
    )
    elements: list[PowerForecastElement] = Field(
        ...,
        min_length=1,
        description=(
            "Elements of which this forecast consists. Contains at least one element. "
            "Elements shall be placed in chronological order."
        ),
    )


# Base classes for control types


class BaseInstruction(PydanticBaseModel, ABC):
    """Base class for S2 control instructions.

    This class defines the common structure for S2 standard control instructions.
    An instruction must have a unique identifier (`id`), an `execution_time`,
    and a flag indicating abnormal operation (`abnormal_condition`). If a `resource_id`
    is provided at instantiation and `id` is not explicitly supplied, a new unique `id`
    will be auto-generated as `{resource_id}-{UUID}`.

    Attributes:
        id (Optional[ID]): Unique identifier of the instruction in the ResourceManager scope.
        execution_time (DateTime): Start time of the instruction execution.
        abnormal_condition (bool): Indicates if this is an instruction for abnormal conditions.
    """

    id: Optional[ID] = Field(
        default=None,
        description=(
            "Unique identifier of the instruction in the ResourceManager scope. "
            "If not provided and a `resource_id` is passed at instantiation, this will "
            "be auto-generated as `{resource_id}@{UUID}`."
        ),
    )
    execution_time: DateTime = Field(..., description="Start time of the instruction execution.")
    abnormal_condition: bool = Field(
        default=False,
        description="Indicates if this is an instruction for abnormal conditions. Defaults to False.",
    )

    @model_validator(mode="before")
    def accept_resource_id(cls, values: dict) -> dict:
        """Pre-process the initialization values.

        Accepts an optional `resource_id` and generates an unique instruction `id` if one is not
        provided.

        Args:
            values (dict): Raw keyword arguments passed to the model constructor.

        Returns:
            dict: Updated keyword arguments with `id` set if `resource_id` was present
                  and `id` was not supplied.
        """
        resource_id = values.pop("resource_id", None)
        if resource_id and not values.get("id"):
            values["id"] = f"{resource_id}@{uuid.uuid4()}"
        return values

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def resource_id(self) -> str:
        """Get the resource identifier component from the instruction's `id`.

        Assumes the `id` follows the format `{resource_id}@{UUID}`. Extracts the resource_id part
        of the id by splitting at the last @.

        Returns:
            str: The resource identifier prefix of `id`, or an empty string if `id` is None.
        """
        return self.id.rsplit("@", 1)[0] if self.id else ""

    @abstractmethod
    def duration(self) -> Optional[Duration]:
        """Returns the active duration of this instruction.

        Returns:
            Optional[Duration]:
                - A finite Duration if the instruction is only active for that period.
                - None if the instruction is active indefinitely.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement the `duration()` method."
        )


# Control Types - Power Envelope Based Control (PEBC)


class PEBCAllowedLimitRange(PydanticBaseModel):
    """Defines the permissible range for power envelope limits in PEBC.

    This model specifies the range of values that a Customer Energy Manager (CEM)
    can select for upper or lower power envelope limits. It establishes the operational
    boundaries for controlling a device using Power Envelope Based Control,
    with optional flags for use during abnormal conditions.
    """

    commodity_quantity: CommodityQuantity = Field(
        ..., description="Type of power quantity this range applies to."
    )
    limit_type: PEBCPowerEnvelopeLimitType = Field(
        ..., description="Whether this range applies to the upper or lower power envelope limit."
    )
    range_boundary: NumberRange = Field(
        ..., description="Range of values the CEM can choose for the power envelope."
    )
    abnormal_condition_only: Optional[bool] = Field(
        False, description="Indicates if this range can only be used during an abnormal condition."
    )


class PEBCPowerConstraints(PydanticBaseModel):
    """Defines the constraints for power envelope control during a specific time period.

    This model specifies the allowed ranges for power envelope limits and the
    consequences of limiting power within those ranges. It provides the CEM with
    information about what power limits can be set and how those limits will affect
    the controlled device's behavior.
    """

    id: ID = Field(..., description="Unique identifier of this PowerConstraints set.")
    valid_from: DateTime = Field(..., description="Timestamp when these constraints become valid.")
    valid_until: Optional[DateTime] = Field(
        None, description="Optional end time of validity for these constraints."
    )
    consequence_type: PEBCPowerEnvelopeConsequenceType = Field(
        ..., description="The type of consequence when limiting power."
    )
    allowed_limit_ranges: list[PEBCAllowedLimitRange] = Field(
        ...,
        description="List of allowed power envelope limit ranges. Must contain at least one UPPER_LIMIT and one LOWER_LIMIT.",
    )


class PEBCEnergyConstraints(PydanticBaseModel):
    """Defines energy constraints over a time period for Power Envelope Based Control.

    This model specifies the minimum and maximum average power over a defined time period,
    which translates to energy constraints. It enables the implementation of energy-based
    limitations in addition to power-based limitations, supporting more sophisticated
    energy management strategies.
    """

    id: ID = Field(..., description="Unique identifier of this EnergyConstraints object.")
    valid_from: DateTime = Field(..., description="Start time for which this constraint is valid.")
    valid_until: DateTime = Field(..., description="End time for which this constraint is valid.")
    upper_average_power: float = Field(
        ...,
        description=(
            "Maximum average power over the given time period. "
            "Used to derive maximum energy content."
        ),
    )
    lower_average_power: float = Field(
        ...,
        description=(
            "Minimum average power over the given time period. "
            "Used to derive minimum energy content."
        ),
    )
    commodity_quantity: CommodityQuantity = Field(
        ..., description="The commodity or type of power to which this applies."
    )


class PEBCPowerEnvelopeElement(PydanticBaseModel):
    """Defines a segment of a power envelope for a specific duration.

    This model specifies the upper and lower power limits for a specific time duration,
    forming part of a complete power envelope. A sequence of these elements creates
    a time-varying power envelope that constrains device power consumption or production.
    """

    duration: Duration = Field(..., description="Duration of this power envelope element.")
    upper_limit: float = Field(
        ...,
        description=(
            "Upper power limit for the given commodity_quantity. "
            "Shall match PEBC.AllowedLimitRange with limit_type UPPER_LIMIT."
        ),
    )
    lower_limit: float = Field(
        ...,
        description=(
            "Lower power limit for the given commodity_quantity. "
            "Shall match PEBC.AllowedLimitRange with limit_type LOWER_LIMIT."
        ),
    )


class PEBCPowerEnvelope(PydanticBaseModel):
    """Defines a complete power envelope constraint for a specific commodity quantity.

    This model specifies a time-series of power limits (upper and lower bounds) that
    a device must operate within. The power envelope consists of sequential elements,
    each defining constraints for a specific duration, creating a complete time-varying
    operational boundary for the device.
    """

    id: ID = Field(
        ...,
        description=(
            "Unique identifier of this PEBC.PowerEnvelope, scoped to the ResourceManager."
        ),
    )
    commodity_quantity: CommodityQuantity = Field(
        ..., description="Type of power quantity the envelope applies to."
    )
    power_envelope_elements: list[PEBCPowerEnvelopeElement] = Field(
        ...,
        min_length=1,
        description=(
            "Chronologically ordered list of PowerEnvelopeElements. "
            "Defines how power should be constrained over time."
        ),
    )


class PEBCInstruction(BaseInstruction):
    """Represents a control instruction for Power Envelope Based Control.

    This model defines a complete instruction for controlling a device using power
    envelopes. It specifies when the instruction should be executed, which power
    constraints apply, and the specific power envelopes to follow. It supports
    multiple power envelopes for different commodity quantities.
    """

    type: Literal["PEBCInstruction"] = Field(default="PEBCInstruction")
    power_constraints_id: ID = Field(..., description="ID of the associated PEBC.PowerConstraints.")
    power_envelopes: list[PEBCPowerEnvelope] = Field(
        ...,
        min_length=1,
        description=(
            "List of PowerEnvelopes to follow. One per CommodityQuantity, max one per type."
        ),
    )

    def duration(self) -> Optional[Duration]:
        envelope_durations: list[Duration] = []
        for power_envelope in self.power_envelopes:
            total_duration = Duration(seconds=0)
            for power_envelope_element in power_envelope.power_envelope_elements:
                total_duration += power_envelope_element.duration
            envelope_durations.append(total_duration)

        return max(envelope_durations) if envelope_durations else None


# Control Types - Power Profile Based Control (PPBC)


class PPBCPowerSequenceElement(PydanticBaseModel):
    """Defines a segment of a power sequence with specific duration and power values.

    This model represents a time segment within a power sequence, specifying the
    forecasted power values for the duration. Multiple elements arranged sequentially
    form a complete power sequence, defining how power will vary over time during
    the execution of the sequence.
    """

    duration: Duration = Field(..., description="Duration of the sequence element.")
    power_values: list[PowerForecastValue] = Field(
        ..., description="Forecasted power values for the duration, one per CommodityQuantity."
    )


class PPBCPowerSequence(PydanticBaseModel):
    """Defines a specific power sequence pattern with timing and interruptibility properties.

    This model specifies a detailed sequence of power behaviors over time, represented
    as a series of power sequence elements. It includes properties that define whether
    the sequence can be interrupted and timing constraints related to its execution,
    supporting flexible power management strategies.
    """

    id: ID = Field(..., description="Unique identifier of the PowerSequence within its container.")
    elements: list[PPBCPowerSequenceElement] = Field(
        ..., description="Ordered list of sequence elements representing power behavior."
    )
    is_interruptible: bool = Field(
        ..., description="Indicates whether this sequence can be interrupted."
    )
    max_pause_before: Optional[Duration] = Field(
        None,
        description="Maximum allowed pause before this sequence starts after the previous one.",
    )
    abnormal_condition_only: bool = Field(
        ..., description="True if sequence is only applicable in abnormal conditions."
    )


class PPBCPowerSequenceContainer(PydanticBaseModel):
    """Groups alternative power sequences for a specific phase of operation.

    This model organizes multiple alternative power sequences for a specific operational
    phase, allowing the CEM to select one based on system requirements. Containers are
    arranged chronologically within a power profile definition to represent sequential
    phases of a complete operation.
    """

    id: ID = Field(
        ...,
        description="Unique identifier of the PowerSequenceContainer within its parent PowerProfileDefinition.",
    )
    power_sequences: list[PPBCPowerSequence] = Field(
        ..., description="List of alternative PowerSequences. One will be selected by the CEM."
    )


class PPBCPowerProfileDefinition(PydanticBaseModel):
    """Defines a complete power profile for Power Profile Based Control.

    This model specifies a structured power profile consisting of multiple sequence
    containers arranged chronologically. Each container holds alternative power sequences,
    allowing the CEM to select the most appropriate sequence based on system needs.
    The profile includes timing constraints for when the sequences can be executed.
    """

    id: ID = Field(
        ...,
        description="Unique identifier of the PowerProfileDefinition within the ResourceManager session.",
    )
    start_time: DateTime = Field(
        ..., description="Earliest possible start time of the first PowerSequence."
    )
    end_time: DateTime = Field(
        ..., description="Latest time the last PowerSequence must be completed."
    )
    power_sequences_containers: list[PPBCPowerSequenceContainer] = Field(
        ...,
        description="List of containers for alternative power sequences, in chronological order.",
    )


class PPBCPowerSequenceContainerStatus(PydanticBaseModel):
    """Reports the status of a specific power sequence container execution.

    This model provides detailed status information for a single sequence container,
    including which sequence was selected, the current execution progress, and the
    operational status. It enables fine-grained monitoring of sequence execution
    within the broader power profile.
    """

    power_profile_id: ID = Field(..., description="ID of the related PowerProfileDefinition.")
    sequence_container_id: ID = Field(
        ..., description="ID of the PowerSequenceContainer being reported on."
    )
    selected_sequence_id: Optional[str] = Field(
        None, description="ID of the selected PowerSequence, if any."
    )
    progress: Optional[Duration] = Field(
        None, description="Elapsed time since the selected sequence started, if applicable."
    )
    status: PPBCPowerSequenceStatus = Field(
        ..., description="Status of the selected PowerSequence."
    )


class PPBCPowerProfileStatus(PydanticBaseModel):
    """Reports the current status of a power profile execution.

    This model provides comprehensive status information for all sequence containers
    in a power profile definition, enabling monitoring of profile execution progress.
    It tracks which sequences have been selected and their current execution status.
    """

    type: Literal["PPBCPowerProfileStatus"] = Field(default="PPBCPowerProfileStatus")

    sequence_container_status: list[PPBCPowerSequenceContainerStatus] = Field(
        ..., description="Status list for all sequence containers in the PowerProfileDefinition."
    )


class PPBCScheduleInstruction(BaseInstruction):
    """Represents an instruction to schedule execution of a specific power sequence.

    This model defines a control instruction that schedules the execution of a
    selected power sequence from a power profile. It specifies which sequence
    has been selected and when it should begin execution, enabling precise control
    of device power behavior according to the predefined sequence.
    """

    type: Literal["PPBCScheduleInstruction"] = Field(default="PPBCScheduleInstruction")

    power_profile_id: ID = Field(
        ..., description="ID of the PowerProfileDefinition being scheduled."
    )
    sequence_container_id: ID = Field(
        ..., description="ID of the container with the selected sequence."
    )
    power_sequence_id: ID = Field(..., description="ID of the selected PowerSequence.")

    def duration(self) -> Optional[Duration]:
        # @TODO: PPBCPowerProfileDefinition needed
        return None


class PPBCStartInterruptionInstruction(BaseInstruction):
    """Represents an instruction to interrupt execution of a running power sequence.

    This model defines a control instruction that interrupts the execution of an
    active power sequence. It enables dynamic control over sequence execution,
    allowing temporary suspension of a sequence in response to changing system conditions
    or requirements, particularly for sequences marked as interruptible.
    """

    type: Literal["PPBCStartInterruptionInstruction"] = Field(
        default="PPBCStartInterruptionInstruction"
    )
    power_profile_id: ID = Field(
        ..., description="ID of the PowerProfileDefinition whose sequence is being interrupted."
    )
    sequence_container_id: ID = Field(
        ..., description="ID of the container containing the sequence."
    )
    power_sequence_id: ID = Field(..., description="ID of the PowerSequence to be interrupted.")

    def duration(self) -> Optional[Duration]:
        # @TODO: PPBCPowerProfileDefinition needed
        return None


class PPBCEndInterruptionInstruction(BaseInstruction):
    """Represents an instruction to resume execution of a previously interrupted power sequence.

    This model defines a control instruction that ends an interruption and resumes
    execution of a previously interrupted power sequence. It complements the start
    interruption instruction, enabling the complete interruption-resumption cycle
    for flexible sequence execution control.
    """

    type: Literal["PPBCEndInterruptionInstruction"] = Field(
        default="PPBCEndInterruptionInstruction"
    )
    power_profile_id: ID = Field(
        ..., description="ID of the PowerProfileDefinition related to the ended interruption."
    )
    sequence_container_id: ID = Field(
        ..., description="ID of the container containing the sequence."
    )
    power_sequence_id: ID = Field(
        ..., description="ID of the PowerSequence for which the interruption ends."
    )

    def duration(self) -> Optional[Duration]:
        # @TODO: PPBCPowerProfileDefinition needed
        return None


# Control Types - Operation Mode Based Control (OMBC)


class OMBCOperationMode(PydanticBaseModel):
    """Operation Mode for Operation Mode Based Control (OMBC).

    Defines a specific operation mode with its power consumption/production characteristics and costs.
    Each operation mode represents a distinct way the resource can operate, with an associated power profile.
    """

    id: ID = Field(
        ..., description="Unique ID of the OperationMode within the ResourceManager session."
    )
    diagnostic_label: Optional[str] = Field(
        None, description="Human-readable label for diagnostics (not for HMI)."
    )
    power_ranges: list[PowerRange] = Field(
        ...,
        description="List of power consumption or production ranges mapped to operation_mode_factor 0 to 1.",
    )
    running_costs: Optional[NumberRange] = Field(
        None,
        description="Estimated additional costs per second, excluding commodity cost. Represents uncertainty.",
    )
    abnormal_condition_only: bool = Field(
        ..., description="True if this mode can only be used during an abnormal condition."
    )


class OMBCStatus(PydanticBaseModel):
    """Reports the current operational status of an Operation Mode Based Control system.

    This model provides real-time status information about an OMBC-controlled device,
    including which operation mode is currently active, how it is configured,
    and information about recent mode transitions. It enables monitoring of the
    device's operational state and tracking mode transition history.
    """

    type: Literal["OMBCStatus"] = Field(default="OMBCStatus")

    active_operation_mode_id: ID = Field(
        ..., description="ID of the currently active operation mode."
    )
    operation_mode_factor: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Factor with which the operation mode is configured (between 0 and 1).",
    )
    previous_operation_mode_id: Optional[str] = Field(
        None, description="ID of the previously active operation mode, if known."
    )
    transition_timestamp: Optional[DateTime] = Field(
        None, description="Timestamp of transition to the active operation mode, if applicable."
    )


class OMBCTimerStatus(PydanticBaseModel):
    """Current status of an OMBC Timer.

    Indicates when the Timer will be finished.
    """

    type: Literal["OMBCTimerStatus"] = Field(default="OMBCTimerStatus")

    timer_id: ID = Field(..., description="ID of the timer this status refers to.")

    finished_at: DateTime = Field(
        ...,
        description="Indicates when the Timer will be finished. If the DateTime is in the future, the timer is not yet finished. If the DateTime is in the past, the timer is finished. If the timer was never started, the value can be an arbitrary DateTimeStamp in the past.",
    )


class OMBCSystemDefinition(PydanticBaseModel):
    """Provides a comprehensive definition of an Operation Mode Based Control system.

    This model defines the complete operational framework for a device controlled using
    Operation Mode Based Control. It specifies all available operation modes, permitted
    transitions between modes, and the timing constraints via timers.
    """

    valid_from: DateTime = Field(
        ...,
        description="Start time from which this system description is valid. Must be in the past or present if immediately applicable.",
    )
    operation_modes: list[OMBCOperationMode] = Field(
        ...,
        description="List of operation modes available for the CEM to coordinate device behavior.",
    )
    transitions: list[Transition] = Field(
        ..., description="Possible transitions between operation modes."
    )
    timers: list[Timer] = Field(
        ..., description="Timers specifying constraints for when transitions can occur."
    )


class OMBCSystemDescription(OMBCSystemDefinition):
    """Provides a comprehensive description of an Operation Mode Based Control system.

    This model defines the complete operational framework for a device controlled using
    Operation Mode Based Control. It specifies all available operation modes, permitted
    transitions between modes, timing constraints via timers, and the current operational
    status.

    It serves as the foundation for understanding and controlling the device's behavior.
    """

    status: OMBCStatus = Field(
        ...,
        description="Current status information, including the active operation mode and transition details.",
    )


class OMBCInstruction(BaseInstruction):
    """Instruction for Operation Mode Based Control (OMBC).

    Contains information about when and how to activate a specific operation mode.
    Used to command resources to change their operation at a specified time.
    """

    type: Literal["OMBCInstruction"] = Field(default="OMBCInstruction")
    operation_mode_id: ID = Field(..., description="ID of the OMBC.OperationMode to activate.")
    operation_mode_factor: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Factor with which the operation mode is configured (0 to 1).",
    )

    def duration(self) -> Optional[Duration]:
        # Infinite, until next instruction
        return None


# Control Types - Fill Rate Based Control (FRBC)


class FRBCOperationModeElement(PydanticBaseModel):
    """Element of an FRBC Operation Mode with properties dependent on fill level.

    Defines how a resource operates within a specific fill level range, including
    its effect on fill rate and associated power consumption/production.
    """

    fill_level_range: NumberRange = Field(..., description="Fill level range for this element.")
    fill_rate: NumberRange = Field(
        ..., description="Change in fill level per second for this mode."
    )
    power_ranges: list[PowerRange] = Field(
        ..., description="Power produced/consumed per commodity."
    )
    running_costs: Optional[NumberRange] = Field(
        None, description="Additional costs per second (excluding commodity cost)."
    )


class FRBCOperationMode(PydanticBaseModel):
    """Operation Mode for Fill Rate Based Control (FRBC).

    Defines a complete operation mode with properties that may vary based on
    the current fill level of the associated storage. Each mode represents a
    distinct way to operate the resource affecting the storage fill level.
    """

    id: ID = Field(..., description="Unique ID of the operation mode within the actuator.")
    diagnostic_label: Optional[str] = Field(
        None, description="Human-readable label for diagnostics."
    )
    elements: list[FRBCOperationModeElement] = Field(
        ..., description="Properties of the mode depending on fill level."
    )
    abnormal_condition_only: bool = Field(
        ..., description="True if mode is for abnormal conditions only."
    )


class FRBCActuatorStatus(PydanticBaseModel):
    """Current status of an FRBC Actuator.

    Provides information about the currently active operation mode and transition history.
    Used to track the current state of the actuator.
    """

    type: Literal["FRBCActuatorStatus"] = Field(default="FRBCActuatorStatus")

    active_operation_mode_id: ID = Field(..., description="Currently active operation mode ID.")
    operation_mode_factor: float = Field(
        ..., ge=0, le=1, description="Factor with which the mode is configured (0 to 1)."
    )
    previous_operation_mode_id: Optional[str] = Field(
        None, description="Previously active operation mode ID."
    )
    transition_timestamp: Optional[DateTime] = Field(
        None, description="Timestamp of the last transition between modes."
    )


class FRBCActuatorDefinition(PydanticBaseModel):
    """Definition of an Actuator for Fill Rate Based Control (FRBC).

    Provides a complete definition of an actuator including its capabilities,
    available operation modes, and constraints on transitions between modes.
    """

    id: ID = Field(..., description="Unique actuator ID within the ResourceManager session.")
    diagnostic_label: Optional[str] = Field(
        None, description="Human-readable actuator description for diagnostics."
    )
    supported_commodities: list[str] = Field(..., description="List of supported commodity IDs.")
    operation_modes: list[FRBCOperationMode] = Field(
        ..., description="Operation modes provided by this actuator."
    )
    transitions: list[Transition] = Field(
        ..., description="Allowed transitions between operation modes."
    )
    timers: list[Timer] = Field(..., description="Timers associated with this actuator.")


class FRBCActuatorDescription(FRBCActuatorDefinition):
    """Description of an Actuator for Fill Rate Based Control (FRBC).

    Provides a complete definition of an actuator including its capabilities,
    available operation modes, constraints on transitions between modes, and the current status
    of the actuator.
    """

    status: FRBCActuatorStatus = Field(..., description="Current status of the actuator.")


class FRBCEnergyStatus(PydanticBaseModel):
    """Energy status of an FRBC storage.

    Note: This is an extension to the S2 standard.
    """

    type: Literal["FRBCEnergyStatus"] = Field(default="FRBCEnergyStatus")

    import_total: Optional[EnergyMeasurement] = Field(
        default=None, description="Total cumulative imported energy from the energy meter start."
    )
    export_total: Optional[EnergyMeasurement] = Field(
        default=None, description="Total cumulative exported energy from the energy meter start."
    )


class FRBCStorageStatus(PydanticBaseModel):
    """Current status of an FRBC Storage.

    Indicates the current fill level of the storage, which is essential
    for determining applicable operation modes and control decisions.
    """

    type: Literal["FRBCStorageStatus"] = Field(default="FRBCStorageStatus")

    present_fill_level: float = Field(..., description="Current fill level of the storage.")


class FRBCTimerStatus(PydanticBaseModel):
    """Current status of an FRBC Timer.

    Indicates when the Timer will be finished.
    """

    type: Literal["FRBCTimerStatus"] = Field(default="FRBCTimerStatus")

    actuator_id: ID = Field(..., description="ID of the actuator the timer belongs to.")

    timer_id: ID = Field(..., description="ID of the timer this status refers to.")

    finished_at: DateTime = Field(
        ...,
        description="Indicates when the Timer will be finished. If the DateTime is in the future, the timer is not yet finished. If the DateTime is in the past, the timer is finished. If the timer was never started, the value can be an arbitrary DateTimeStamp in the past.",
    )


class FRBCLeakageBehaviourElement(PydanticBaseModel):
    """Element of the leakage behavior for an FRBC Storage.

    Describes how leakage varies with fill level, used to model natural
    losses in the storage over time.
    """

    fill_level_range: NumberRange = Field(
        ..., description="Applicable fill level range for this element."
    )
    leakage_rate: float = Field(
        ..., description="Rate of fill level decrease per second due to leakage."
    )


class FRBCLeakageBehaviour(PydanticBaseModel):
    """Complete leakage behavior model for an FRBC Storage.

    Describes how the storage naturally loses its content over time,
    with leakage rates that may vary based on fill level.
    """

    valid_from: DateTime = Field(..., description="Start of validity for this leakage behaviour.")
    elements: list[FRBCLeakageBehaviourElement] = Field(
        ..., description="Contiguous elements modeling leakage."
    )


class FRBCUsageForecastElement(PydanticBaseModel):
    """Element of a usage forecast for an FRBC Storage.

    Describes expected usage rates for a specific duration, including
    probability ranges to represent uncertainty.
    """

    duration: Duration = Field(..., description="How long the given usage rate is valid.")
    usage_rate_upper_limit: Optional[float] = Field(
        None, description="100% probability upper limit."
    )
    usage_rate_upper_95PPR: Optional[float] = Field(
        None, description="95% probability upper limit."
    )
    usage_rate_upper_68PPR: Optional[float] = Field(
        None, description="68% probability upper limit."
    )
    usage_rate_expected: float = Field(..., description="Most likely usage rate.")
    usage_rate_lower_68PPR: Optional[float] = Field(
        None, description="68% probability lower limit."
    )
    usage_rate_lower_95PPR: Optional[float] = Field(
        None, description="95% probability lower limit."
    )
    usage_rate_lower_limit: Optional[float] = Field(
        None, description="100% probability lower limit."
    )


class FRBCUsageForecast(PydanticBaseModel):
    """Complete usage forecast for an FRBC Storage.

    Provides a time-series forecast of expected usage rates,
    allowing for planning of optimal resource operation.
    """

    start_time: DateTime = Field(..., description="Start time of the forecast.")
    elements: list[FRBCUsageForecastElement] = Field(
        ..., description="Chronological forecast profile elements."
    )


class FRBCFillLevelTargetProfileElement(PydanticBaseModel):
    """Element of a fill level target profile for an FRBC Storage.

    Specifies the desired fill level range for a specific duration,
    used to guide resource operation planning.
    """

    duration: Duration = Field(..., description="Duration this target applies for.")
    fill_level_range: NumberRange = Field(
        ..., description="Target fill level range for the duration."
    )


class FRBCFillLevelTargetProfile(PydanticBaseModel):
    """Complete fill level target profile for an FRBC Storage.

    Defines a time-series of target fill levels, providing goals
    for the control system to achieve through resource operation.
    """

    start_time: DateTime = Field(..., description="Start time of the fill level target profile.")
    elements: list[FRBCFillLevelTargetProfileElement] = Field(
        ..., description="Chronological list of target ranges."
    )


class FRBCStorageDefinition(PydanticBaseModel):
    """Definition of a Storage for Fill Rate Based Control (FRBC).

    Provides a complete definition of a storage including its capabilities,
    constraints, and behavior characteristics.
    """

    diagnostic_label: Optional[str] = Field(
        None, description="Diagnostic description of the storage."
    )
    fill_level_label: Optional[str] = Field(
        None, description="Description of fill level units (e.g. °C, %)."
    )
    fill_level_range: NumberRange = Field(
        ..., description="Range in which fill level should remain."
    )
    leakage_behaviour: Optional[FRBCLeakageBehaviour] = Field(
        None, description="Details of buffer leakage behaviour."
    )


class FRBCStorageDescription(FRBCStorageDefinition):
    """Description of a Storage for Fill Rate Based Control (FRBC).

    Provides a complete definition of a storage including its capabilities,
    constraints, current status, and behavior characteristics.
    """

    status: FRBCStorageStatus = Field(..., description="Current storage status.")
    provides_leakage_behaviour: bool = Field(
        ..., description="True if leakage behaviour can be provided."
    )
    provides_fill_level_target_profile: bool = Field(
        ..., description="True if fill level target profile can be provided."
    )
    provides_usage_forecast: bool = Field(
        ..., description="True if usage forecast can be provided."
    )


class FRBCInstruction(BaseInstruction):
    """Instruction for Fill Rate Based Control (FRBC).

    Contains information about when and how to activate a specific operation mode
    for an actuator. Used to command resources to change their operation at a specified time.
    """

    type: Literal["FRBCInstruction"] = Field(default="FRBCInstruction")
    actuator_id: ID = Field(..., description="ID of the actuator this instruction belongs to.")
    operation_mode_id: str = Field(..., description="ID of the operation mode to activate.")
    operation_mode_factor: float = Field(
        ..., ge=0, le=1, description="Factor for the operation mode configuration (0 to 1)."
    )

    def duration(self) -> Optional[Duration]:
        # Infinite, until next instruction
        return None


class FRBCSystemDescription(PydanticBaseModel):
    """Complete system description for Fill Rate Based Control (FRBC).

    Provides a comprehensive description of all components in an FRBC system,
    including actuators and storage. This is the top-level model for FRBC.
    """

    valid_from: DateTime = Field(..., description="Time this system description becomes valid.")
    actuators: list[FRBCActuatorDescription] = Field(..., description="List of all actuators.")
    storage: FRBCStorageDescription = Field(..., description="Details of the storage.")


# Control Types - Demand Driven Based Control (DDBC)


class DDBCOperationMode(PydanticBaseModel):
    """Operation Mode for Demand Driven Based Control (DDBC).

    Defines a specific operation mode with its power consumption/production characteristics,
    supply capabilities, and costs. Each mode represents a distinct way to operate a resource
    to meet demand.
    """

    id: ID = Field(
        ..., description="ID of the operation mode. Must be unique within the actuator description."
    )
    diagnostic_label: Optional[str] = Field(
        None, description="Human-readable name/description for diagnostics (not for HMI)."
    )
    power_ranges: list[PowerRange] = Field(
        ...,
        description="Power ranges associated with this operation mode. At least one per CommodityQuantity.",
    )
    supply_range: NumberRange = Field(
        ..., description="Supply rate that can match the demand rate, mapped from factor 0 to 1."
    )
    running_costs: NumberRange = Field(
        ...,
        description="Additional cost per second (excluding commodity cost). Represents uncertainty, not linked to factor.",
    )
    abnormal_condition_only: Optional[bool] = Field(
        False,
        description="Whether this operation mode may only be used during abnormal conditions.",
    )


class DDBCActuatorStatus(PydanticBaseModel):
    """Current status of a DDBC Actuator.

    Provides information about the currently active operation mode and transition history.
    Used to track the current state of the actuator.
    """

    type: Literal["DDBCActuatorStatus"] = Field(default="DDBCActuatorStatus")

    active_operation_mode_id: ID = Field(..., description="Currently active operation mode ID.")
    operation_mode_factor: float = Field(
        ..., ge=0, le=1, description="Factor with which the operation mode is configured (0 to 1)."
    )
    previous_operation_mode_id: Optional[str] = Field(
        None,
        description="Previously active operation mode ID. Required unless this is the first mode.",
    )
    transition_timestamp: Optional[DateTime] = Field(
        None, description="Timestamp of transition to the active operation mode."
    )


class DDBCActuatorDefinition(PydanticBaseModel):
    """Definition of an Actuator for Demand Driven Based Control (DDBC).

    Provides a complete definition of an actuator including its capabilities,
    available operation modes, and constraints on transitions between modes.
    """

    id: ID = Field(
        ...,
        description="ID of this actuator. Must be unique in the ResourceManager scope during the session.",
    )
    diagnostic_label: Optional[str] = Field(
        None, description="Human-readable name/description for diagnostics (not for HMI)."
    )
    supported_commodities: list[str] = Field(
        ..., description="Commodities supported by this actuator. Must include at least one."
    )
    operation_modes: list[DDBCOperationMode] = Field(
        ...,
        description="List of available operation modes for this actuator. Must include at least one.",
    )
    transitions: list[Transition] = Field(
        ..., description="List of transitions between operation modes. Must include at least one."
    )
    timers: list[Timer] = Field(
        ..., description="List of timers associated with transitions. Can be empty."
    )


class DDBCActuatorDescription(DDBCActuatorDefinition):
    """Description of an Actuator for Demand Driven Based Control (DDBC).

    Provides a complete description of an actuator including its capabilities,
    available operation modes, constraints on transitions between modes, and
    its present status.
    """

    status: DDBCActuatorStatus = Field(..., description="Present status of this actuator.")


class DDBCSystemDescription(PydanticBaseModel):
    """Complete system description for Demand Driven Based Control (DDBC).

    Provides a comprehensive description of all components in a DDBC system,
    including actuators and demand characteristics. This is the top-level model for DDBC.
    """

    valid_from: DateTime = Field(
        ...,
        description="Moment this DDBC.SystemDescription starts to be valid. If immediately valid, it should be now or in the past.",
    )
    actuators: list[DDBCActuatorDescription] = Field(
        ...,
        description="List of all available actuators in the system. Shall contain at least one DDBC.ActuatorAggregated.",
    )
    present_demand_rate: NumberRange = Field(
        ..., description="Present demand rate that needs to be satisfied by the system."
    )
    provides_average_demand_rate_forecast: bool = Field(
        ...,
        description="Indicates whether a demand rate forecast is provided through DDBC.AverageDemandRateForecast.",
    )


class DDBCInstruction(BaseInstruction):
    """Instruction for Demand Driven Based Control (DDBC).

    Contains information about when and how to activate a specific operation mode
    for an actuator. Used to command resources to change their operation at a specified time.
    """

    type: Literal["DDBCInstruction"] = Field(default="DDBCInstruction")
    actuator_id: ID = Field(..., description="ID of the actuator this instruction belongs to.")
    operation_mode_id: ID = Field(..., description="ID of the DDBC.OperationMode to apply.")
    operation_mode_factor: float = Field(
        ...,
        ge=0,
        le=1,
        description="Factor with which the operation mode should be applied (0 to 1).",
    )

    def duration(self) -> Optional[Duration]:
        # Infinite, until next instruction
        return None


class DDBCAverageDemandRateForecastElement(PydanticBaseModel):
    """Element of a demand rate forecast for DDBC.

    Describes expected demand rates for a specific duration, including
    probability ranges to represent uncertainty.
    """

    duration: Duration = Field(..., description="Duration of this forecast element.")
    demand_rate_upper_limit: Optional[float] = Field(
        None, description="100% upper limit of demand rate range."
    )
    demand_rate_upper_95PPR: Optional[float] = Field(
        None, description="95% upper limit of demand rate range."
    )
    demand_rate_upper_68PPR: Optional[float] = Field(
        None, description="68% upper limit of demand rate range."
    )
    demand_rate_expected: float = Field(
        ..., description="Expected demand rate (fill level increase/decrease per second)."
    )
    demand_rate_lower_68PPR: Optional[float] = Field(
        None, description="68% lower limit of demand rate range."
    )
    demand_rate_lower_95PPR: Optional[float] = Field(
        None, description="95% lower limit of demand rate range."
    )
    demand_rate_lower_limit: Optional[float] = Field(
        None, description="100% lower limit of demand rate range."
    )


class DDBCAverageDemandRateForecast(PydanticBaseModel):
    """Complete demand rate forecast for DDBC.

    Provides a time-series forecast of expected demand rates,
    allowing for planning of optimal resource operation to meet future demands.
    """

    start_time: DateTime = Field(
        ..., description="Start time of the average demand rate forecast profile."
    )
    elements: list[DDBCAverageDemandRateForecastElement] = Field(
        ..., description="List of forecast elements in chronological order."
    )


# Resource Status

# ResourceStatus, discriminated by its type field
ResourceStatus = Annotated[
    Union[
        PowerMeasurement,
        EnergyMeasurement,
        PPBCPowerProfileStatus,
        OMBCStatus,
        FRBCActuatorStatus,
        FRBCEnergyStatus,
        FRBCStorageStatus,
        FRBCTimerStatus,
        DDBCActuatorStatus,
    ],
    Field(discriminator="type"),
]


# Plan

# Instruction, discriminated by its type field
EnergyManagementInstruction = Annotated[
    Union[
        PEBCInstruction,
        PPBCScheduleInstruction,
        PPBCStartInterruptionInstruction,
        PPBCEndInterruptionInstruction,
        OMBCInstruction,
        FRBCInstruction,
        DDBCInstruction,
    ],
    Field(discriminator="type"),
]


class EnergyManagementPlan(PydanticBaseModel):
    """A coordinated energy management plan composed of device control instructions.

    Attributes:
        plan_id (ID): Unique identifier for this energy management plan.
        generated_at (DateTime): Timestamp when the plan was generated.
        valid_from (Optional[DateTime]): Earliest start time of any instruction.
        valid_until (Optional[DateTime]): Latest end time across all instructions
            with finite duration; None if all instructions have infinite duration.
        instructions (list[BaseInstruction]): List of control instructions for the plan.
        comment (Optional[str]): Optional comment or annotation for the plan.
    """

    id: ID = Field(..., description="Unique ID for the energy management plan.")
    generated_at: DateTime = Field(..., description="Timestamp when the plan was generated.")
    valid_from: Optional[DateTime] = Field(
        default=None, description="Earliest start time of any instruction."
    )
    valid_until: Optional[DateTime] = Field(
        default=None,
        description=(
            "Latest end time across all instructions with finite duration; "
            "None if all instructions have infinite duration."
        ),
    )
    instructions: list[EnergyManagementInstruction] = Field(
        ..., description="List of control instructions for the plan."
    )
    comment: Optional[str] = Field(
        default=None, description="Optional comment or annotation for the plan."
    )

    def _update_time_range(self) -> None:
        """Updates valid_from and valid_until based on the instructions.

        Sets valid_from as the earliest execution_time of the instructions.
        Sets valid_until as the latest end time, or None if any instruction is infinite.
        """
        if not self.instructions:
            self.valid_from = to_datetime()
            self.valid_until = None
            return

        self.valid_from = min(i.execution_time for i in self.instructions)

        end_times = []
        for instr in self.instructions:
            instr_duration = instr.duration()  # Returns Optional[Duration]
            if instr_duration is None:
                # Infinite instruction means valid_until must be None
                self.valid_until = None
                return
            end_times.append(instr.execution_time + instr_duration)

        self.valid_until = max(end_times) if end_times else None

    def add_instruction(self, instruction: EnergyManagementInstruction) -> None:
        """Adds a new control instruction and updates time range."""
        self.instructions.append(instruction)
        self.instructions.sort(key=lambda i: i.execution_time)
        self._update_time_range()

    def clear(self) -> None:
        """Removes all control instructions and resets time range."""
        self.instructions.clear()
        self.valid_from = to_datetime()
        self.valid_until = None

    def get_active_instructions(
        self, now: Optional[DateTime] = None
    ) -> list[EnergyManagementInstruction]:
        """Retrieves all currently active instructions at the specified time."""
        now = now or to_datetime()
        active = []
        for instr in self.instructions:
            instr_duration = instr.duration()
            if instr_duration is None:
                if instr.execution_time <= now:
                    active.append(instr)
            else:
                if instr.execution_time <= now < instr.execution_time + instr_duration:
                    active.append(instr)
        return active

    def get_next_instruction(
        self, now: Optional[DateTime] = None
    ) -> Optional[EnergyManagementInstruction]:
        """Finds the next instruction scheduled after the specified time."""
        now = now or to_datetime()
        future_instructions = [i for i in self.instructions if i.execution_time > now]
        return (
            min(future_instructions, key=lambda i: i.execution_time)
            if future_instructions
            else None
        )

    def get_instructions_for_resource(self, resource_id: ID) -> list[EnergyManagementInstruction]:
        """Filters the plan's instructions for a specific resource."""
        return [i for i in self.instructions if i.resource_id == resource_id]
