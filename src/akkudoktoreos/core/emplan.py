"""Energy management plan.

The energy management plan is leaned on to the S2 standard.

This module provides data models and enums for energy resource management
following the S2 standard, supporting various control types including Power Envelope Based Control,
Power Profile Based Control, Operation Mode Based Control, Fill Rate Based Control, and
Demand Driven Based Control.
"""

from enum import Enum
from typing import Optional

from pendulum import DateTime, Duration
from pydantic import Field

from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

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


from akkudoktoreos.core.pydantic import ParametersBaseModel

# S2 Basic Values


class PowerValue(ParametersBaseModel):
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


class PowerForecastValue(ParametersBaseModel):
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


class PowerRange(ParametersBaseModel):
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


class NumberRange(ParametersBaseModel):
    """Defines a generic numeric range with start and end values.

    Unlike PowerRange, this model is not tied to a specific commodity quantity
    and can be used for any numeric range definition throughout the system.
    Used for representing ranges of prices, percentages, or other numeric values.
    """

    start_of_range: float = Field(..., description="Number that defines the start of the range.")
    end_of_range: float = Field(..., description="Number that defines the end of the range.")


class PowerMeasurement(ParametersBaseModel):
    """Captures a set of power measurements taken at a specific point in time.

    This model records multiple power values (for different commodity quantities)
    along with the timestamp when the measurements were taken, enabling time-series
    analysis and monitoring of power consumption or production.
    """

    measurement_timestamp: DateTime = Field(
        ..., description="Timestamp when PowerValues were measured."
    )
    values: list[PowerValue] = Field(
        ...,
        description="Array of measured PowerValues. Shall contain at least one item and at most one item per 'commodity_quantity' (defined inside the PowerValue).",
    )


class Role(ParametersBaseModel):
    """Defines an energy system role related to a specific commodity.

    This model links a role type (such as consumer, producer, or storage) with
    a specific energy commodity (electricity, gas, heat, etc.), defining how
    an entity interacts with the energy system for that commodity.
    """

    role: RoleType = Field(..., description="Role type for the given commodity.")
    commodity: Commodity = Field(..., description="Commodity the role refers to.")


class ReceptionStatus(ParametersBaseModel):
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


class Transition(ParametersBaseModel):
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


class Timer(ParametersBaseModel):
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


class InstructionStatusUpdate(ParametersBaseModel):
    """Represents an update to the status of a control instruction.

    This model tracks the progress and current state of a control instruction,
    including when its status last changed. It enables monitoring of instruction
    execution and provides feedback about the system's response to control commands.
    """

    instruction_id: ID = Field(..., description=("ID of this instruction, as provided by the CEM."))
    status_type: InstructionStatus = Field(..., description=("Present status of this instruction."))
    timestamp: DateTime = Field(..., description=("Timestamp when the status_type last changed."))


# ResourceManager


class ResourceManagerDetails(ParametersBaseModel):
    """Provides comprehensive details about a ResourceManager's capabilities and identity.

    This model defines the core characteristics of a ResourceManager, including its
    identification, supported energy roles, control capabilities, and technical specifications.
    It serves as the primary descriptor for a device or system that can be controlled
    by a Customer Energy Manager (CEM).
    """

    simulation_id: ID = Field(
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


class PowerForecastElement(ParametersBaseModel):
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


class PowerForecast(ParametersBaseModel):
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


# Control Types - Power Envelope Based Control (PEBC)


class PEBCAllowedLimitRange(ParametersBaseModel):
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


class PEBCPowerConstraints(ParametersBaseModel):
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


class PEBCEnergyConstraints(ParametersBaseModel):
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


class PEBCPowerEnvelopeElement(ParametersBaseModel):
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


class PEBCPowerEnvelope(ParametersBaseModel):
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


class PEBCInstruction(ParametersBaseModel):
    """Represents a control instruction for Power Envelope Based Control.

    This model defines a complete instruction for controlling a device using power
    envelopes. It specifies when the instruction should be executed, which power
    constraints apply, and the specific power envelopes to follow. It supports
    multiple power envelopes for different commodity quantities.
    """

    id: ID = Field(
        ..., description="Unique identifier of the instruction within the ResourceManager context."
    )
    execution_time: DateTime = Field(
        ..., description="Timestamp when execution of the instruction should begin."
    )
    abnormal_condition: bool = Field(
        ..., description="True if the instruction is triggered under an abnormal condition."
    )
    power_constraints_id: ID = Field(..., description="ID of the associated PEBC.PowerConstraints.")
    power_envelopes: list[PEBCPowerEnvelope] = Field(
        ...,
        min_length=1,
        description=(
            "List of PowerEnvelopes to follow. One per CommodityQuantity, max one per type."
        ),
    )


# Control Types - Power Profile Based Control (PPBC)


class PPBCPowerSequenceElement(ParametersBaseModel):
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


class PPBCPowerSequence(ParametersBaseModel):
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


class PPBCPowerSequenceContainer(ParametersBaseModel):
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


class PPBCPowerProfileDefinition(ParametersBaseModel):
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


class PPBCPowerSequenceContainerStatus(ParametersBaseModel):
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


class PPBCPowerProfileStatus(ParametersBaseModel):
    """Reports the current status of a power profile execution.

    This model provides comprehensive status information for all sequence containers
    in a power profile definition, enabling monitoring of profile execution progress.
    It tracks which sequences have been selected and their current execution status.
    """

    sequence_container_status: list[PPBCPowerSequenceContainerStatus] = Field(
        ..., description="Status list for all sequence containers in the PowerProfileDefinition."
    )


class PPBCScheduleInstruction(ParametersBaseModel):
    """Represents an instruction to schedule execution of a specific power sequence.

    This model defines a control instruction that schedules the execution of a
    selected power sequence from a power profile. It specifies which sequence
    has been selected and when it should begin execution, enabling precise control
    of device power behavior according to the predefined sequence.
    """

    id: ID = Field(..., description="Unique ID of the instruction.")
    power_profile_id: ID = Field(
        ..., description="ID of the PowerProfileDefinition being scheduled."
    )
    sequence_container_id: ID = Field(
        ..., description="ID of the container with the selected sequence."
    )
    power_sequence_id: ID = Field(..., description="ID of the selected PowerSequence.")
    execution_time: DateTime = Field(..., description="Start time of the sequence execution.")
    abnormal_condition: bool = Field(
        ..., description="True if the instruction is issued during an abnormal condition."
    )


class PPBCStartInterruptionInstruction(ParametersBaseModel):
    """Represents an instruction to interrupt execution of a running power sequence.

    This model defines a control instruction that interrupts the execution of an
    active power sequence. It enables dynamic control over sequence execution,
    allowing temporary suspension of a sequence in response to changing system conditions
    or requirements, particularly for sequences marked as interruptible.
    """

    id: ID = Field(..., description="Unique ID of the interruption instruction.")
    power_profile_id: ID = Field(
        ..., description="ID of the PowerProfileDefinition whose sequence is being interrupted."
    )
    sequence_container_id: ID = Field(
        ..., description="ID of the container containing the sequence."
    )
    power_sequence_id: ID = Field(..., description="ID of the PowerSequence to be interrupted.")
    execution_time: DateTime = Field(..., description="Time when the interruption starts.")
    abnormal_condition: bool = Field(
        ..., description="True if the instruction is during an abnormal condition."
    )


class PPBCEndInterruptionInstruction(ParametersBaseModel):
    """Represents an instruction to resume execution of a previously interrupted power sequence.

    This model defines a control instruction that ends an interruption and resumes
    execution of a previously interrupted power sequence. It complements the start
    interruption instruction, enabling the complete interruption-resumption cycle
    for flexible sequence execution control.
    """

    id: ID = Field(..., description="Unique ID of the end-of-interruption instruction.")
    power_profile_id: ID = Field(
        ..., description="ID of the PowerProfileDefinition related to the ended interruption."
    )
    sequence_container_id: ID = Field(
        ..., description="ID of the container containing the sequence."
    )
    power_sequence_id: ID = Field(
        ..., description="ID of the PowerSequence for which the interruption ends."
    )
    execution_time: DateTime = Field(..., description="Time the interruption ends.")
    abnormal_condition: bool = Field(
        ..., description="True if the instruction is issued during an abnormal condition."
    )


# Control Types - Operation Mode Based Control (OMBC)


class OMBCOperationMode(ParametersBaseModel):
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


class OMBCStatus(ParametersBaseModel):
    """Reports the current operational status of an Operation Mode Based Control system.

    This model provides real-time status information about an OMBC-controlled device,
    including which operation mode is currently active, how it is configured,
    and information about recent mode transitions. It enables monitoring of the
    device's operational state and tracking mode transition history.
    """

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


class OMBCSystemDescription(ParametersBaseModel):
    """Provides a comprehensive description of an Operation Mode Based Control system.

    This model defines the complete operational framework for a device controlled using
    Operation Mode Based Control. It specifies all available operation modes, permitted
    transitions between modes, timing constraints via timers, and the current operational
    status. It serves as the foundation for understanding and controlling the device's behavior.
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
    status: OMBCStatus = Field(
        ...,
        description="Current status information, including the active operation mode and transition details.",
    )


class OMBCInstruction(ParametersBaseModel):
    """Instruction for Operation Mode Based Control (OMBC).

    Contains information about when and how to activate a specific operation mode.
    Used to command resources to change their operation at a specified time.
    """

    id: ID = Field(
        ..., description="Unique ID of the instruction within the ResourceManager session."
    )
    execution_time: DateTime = Field(
        ..., description="Time at which the instruction should be executed."
    )
    operation_mode_id: ID = Field(..., description="ID of the OMBC.OperationMode to activate.")
    operation_mode_factor: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Factor with which the operation mode is configured (0 to 1).",
    )
    abnormal_condition: bool = Field(
        ..., description="True if the instruction is for abnormal condition handling."
    )


# Control Types - Fill Rate Based Control (FRBC)


class FRBCOperationModeElement(ParametersBaseModel):
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


class FRBCOperationMode(ParametersBaseModel):
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


class FRBCActuatorStatus(ParametersBaseModel):
    """Current status of an FRBC Actuator.

    Provides information about the currently active operation mode and transition history.
    Used to track the current state of the actuator.
    """

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


class FRBCActuatorDescription(ParametersBaseModel):
    """Description of an Actuator for Fill Rate Based Control (FRBC).

    Provides a complete definition of an actuator including its capabilities,
    available operation modes, and constraints on transitions between modes.
    """

    id: ID = Field(..., description="Unique actuator ID within the ResourceManager session.")
    diagnostic_label: Optional[str] = Field(
        None, description="Human-readable actuator description for diagnostics."
    )
    supported_commodities: list[str] = Field(..., description="List of supported commodity IDs.")
    status: FRBCActuatorStatus = Field(..., description="Current status of the actuator.")
    operation_modes: list[FRBCOperationMode] = Field(
        ..., description="Operation modes provided by this actuator."
    )
    transitions: list[Transition] = Field(
        ..., description="Allowed transitions between operation modes."
    )
    timers: list[Timer] = Field(..., description="Timers associated with this actuator.")


class FRBCStorageStatus(ParametersBaseModel):
    """Current status of an FRBC Storage.

    Indicates the current fill level of the storage, which is essential
    for determining applicable operation modes and control decisions.
    """

    present_fill_level: float = Field(..., description="Current fill level of the storage.")


class FRBCLeakageBehaviourElement(ParametersBaseModel):
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


class FRBCLeakageBehaviour(ParametersBaseModel):
    """Complete leakage behavior model for an FRBC Storage.

    Describes how the storage naturally loses its content over time,
    with leakage rates that may vary based on fill level.
    """

    valid_from: DateTime = Field(..., description="Start of validity for this leakage behaviour.")
    elements: list[FRBCLeakageBehaviourElement] = Field(
        ..., description="Contiguous elements modeling leakage."
    )


class FRBCUsageForecastElement(ParametersBaseModel):
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


class FRBCUsageForecast(ParametersBaseModel):
    """Complete usage forecast for an FRBC Storage.

    Provides a time-series forecast of expected usage rates,
    allowing for planning of optimal resource operation.
    """

    start_time: DateTime = Field(..., description="Start time of the forecast.")
    elements: list[FRBCUsageForecastElement] = Field(
        ..., description="Chronological forecast profile elements."
    )


class FRBCFillLevelTargetProfileElement(ParametersBaseModel):
    """Element of a fill level target profile for an FRBC Storage.

    Specifies the desired fill level range for a specific duration,
    used to guide resource operation planning.
    """

    duration: Duration = Field(..., description="Duration this target applies for.")
    fill_level_range: NumberRange = Field(
        ..., description="Target fill level range for the duration."
    )


class FRBCFillLevelTargetProfile(ParametersBaseModel):
    """Complete fill level target profile for an FRBC Storage.

    Defines a time-series of target fill levels, providing goals
    for the control system to achieve through resource operation.
    """

    start_time: DateTime = Field(..., description="Start time of the fill level target profile.")
    elements: list[FRBCFillLevelTargetProfileElement] = Field(
        ..., description="Chronological list of target ranges."
    )


class FRBCStorageDescription(ParametersBaseModel):
    """Description of a Storage for Fill Rate Based Control (FRBC).

    Provides a complete definition of a storage including its capabilities,
    constraints, current status, and behavior characteristics.
    """

    diagnostic_label: Optional[str] = Field(
        None, description="Diagnostic description of the storage."
    )
    fill_level_label: Optional[str] = Field(
        None, description="Description of fill level units (e.g. °C, %)."
    )
    provides_leakage_behaviour: bool = Field(
        ..., description="True if leakage behaviour can be provided."
    )
    provides_fill_level_target_profile: bool = Field(
        ..., description="True if fill level target profile can be provided."
    )
    provides_usage_forecast: bool = Field(
        ..., description="True if usage forecast can be provided."
    )
    fill_level_range: NumberRange = Field(
        ..., description="Range in which fill level should remain."
    )
    status: FRBCStorageStatus = Field(..., description="Current storage status.")
    leakage_behaviour: Optional[FRBCLeakageBehaviour] = Field(
        None, description="Details of buffer leakage behaviour."
    )


class FRBCInstruction(ParametersBaseModel):
    """Instruction for Fill Rate Based Control (FRBC).

    Contains information about when and how to activate a specific operation mode
    for an actuator. Used to command resources to change their operation at a specified time.
    """

    id: ID = Field(..., description="Unique ID of the instruction.")
    actuator_id: ID = Field(..., description="ID of the actuator the instruction applies to.")
    operation_mode: str = Field(..., description="ID of the operation mode to activate.")
    operation_mode_factor: float = Field(
        ..., ge=0, le=1, description="Factor for the operation mode configuration (0 to 1)."
    )
    execution_time: DateTime = Field(..., description="Time the instruction should be executed.")
    abnormal_condition: bool = Field(
        ..., description="True if this is an abnormal condition instruction."
    )


class FRBCSystemDescription(ParametersBaseModel):
    """Complete system description for Fill Rate Based Control (FRBC).

    Provides a comprehensive description of all components in an FRBC system,
    including actuators and storage. This is the top-level model for FRBC.
    """

    valid_from: DateTime = Field(..., description="Time this system description becomes valid.")
    actuators: list[FRBCActuatorDescription] = Field(..., description="List of all actuators.")
    storage: FRBCStorageDescription = Field(..., description="Details of the storage.")


# Control Types - Demand Driven Based Control (DDBC)


class DDBCOperationMode(ParametersBaseModel):
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


class DDBCActuatorStatus(ParametersBaseModel):
    """Current status of a DDBC Actuator.

    Provides information about the currently active operation mode and transition history.
    Used to track the current state of the actuator.
    """

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


class DDBCActuatorDescription(ParametersBaseModel):
    """Description of an Actuator for Demand Driven Based Control (DDBC).

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
    status: DDBCActuatorStatus = Field(..., description="Present status of this actuator.")
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


class DDBCSystemDescription(ParametersBaseModel):
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


class DDBCInstruction(ParametersBaseModel):
    """Instruction for Demand Driven Based Control (DDBC).

    Contains information about when and how to activate a specific operation mode
    for an actuator. Used to command resources to change their operation at a specified time.
    """

    id: ID = Field(
        ..., description="Unique identifier of the instruction in the ResourceManager scope."
    )
    execution_time: DateTime = Field(..., description="Start time of the instruction execution.")
    abnormal_condition: bool = Field(
        ..., description="Indicates if this is an instruction for abnormal conditions."
    )
    actuator_id: ID = Field(
        ..., description="ID of the actuator to which this instruction applies."
    )
    operation_mode_id: ID = Field(..., description="ID of the DDBC.OperationMode to apply.")
    operation_mode_factor: float = Field(
        ...,
        ge=0,
        le=1,
        description="Factor with which the operation mode should be applied (0 to 1).",
    )


class DDBCAverageDemandRateForecastElement(ParametersBaseModel):
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


class DDBCAverageDemandRateForecast(ParametersBaseModel):
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


# Device Control


class DeviceControlInstruction(ParametersBaseModel):
    """Represents a control instruction for an energy device.

    This model abstracts commands such as setting power, enabling/disabling, or targeting
    a fill level. It is designed to be mappable to concrete device APIs.

    Attributes:
        control_id: Unique identifier for the control instruction.
        target_device: ID of the simulated or real device receiving the instruction.
        commodity_quantity: Type of energy being controlled (e.g., electric_power).
        start_time: Timestamp when the instruction becomes active.
        duration: Time span during which the instruction is valid.
        power_value: Optional target power in watts (positive for consumption, negative for production).
        fill_level_target: Optional target fill level between 0 and 1 (used for storage devices).
        enable: Optional flag to enable or disable the device.
    """

    control_id: ID = Field(..., description="Unique ID of the control instruction.")
    target_device: ID = Field(..., description="ID of the target simulated or real device.")
    commodity_quantity: CommodityQuantity = Field(
        ..., description="Type of energy being controlled (e.g. electric_power)."
    )
    start_time: DateTime = Field(..., description="Time when this control should become active.")
    duration: Duration = Field(..., description="How long this control should be active.")

    power: Optional[float] = Field(
        None, description="Target power (positive for consumption, negative for production)."
    )

    fill_level: Optional[float] = Field(
        None, description="Target fill level (0.0...1.0) for devices with storage capabilities."
    )

    enable: Optional[bool] = Field(
        None, description="Flag to enable (true) or disable (false) the device."
    )

    def is_power_control(self) -> bool:
        """Check whether this instruction contains a power command."""
        return self.power_value is not None

    def is_fill_level_control(self) -> bool:
        """Check whether this instruction contains a fill level target."""
        return self.fill_level_target is not None

    def is_enable_disable(self) -> bool:
        """Check whether this instruction contains an enable/disable flag."""
        return self.enable is not None


class EnergyRequestInstruction(ParametersBaseModel):
    """Represents an energy request instruction for the optimization.

    The request holds a structured power profile consisting of multiple sequence
    containers arranged chronologically. Each container holds alternative power sequences,
    allowing the optimization to select the most appropriate sequence based on system needs.
    The profile includes timing constraints for when the sequences can be executed.
    """

    control_id: ID = Field(..., description="Unique ID of the request instruction.")
    source_device: ID = Field(..., description="ID of the source simulated or real device.")

    power_profile: PPBCPowerProfileDefinition = Field(..., description="Requested power profile.")


class EnergyRequestResponse(ParametersBaseModel):
    request_id: str
    accepted: bool
    message: Optional[str] = None
    scheduled_sequence: Optional[PPBCPowerSequence] = None
    response_time: DateTime


class EnergyManagementPlan(ParametersBaseModel):
    """A coordinated energy management plan composed of device control instructions.

    This plan defines how energy devices (such as batteries, generators, or loads)
    should behave over time to follow energy goals. Instructions are scheduled and can
    be queried based on time or device.

    Attributes:
        plan_id (ID): Unique identifier for this energy management plan.
        generated_at (DateTime): Timestamp when the plan was created.
        valid_from (Optional[DateTime]): Optional timestamp from which the plan is valid.
        valid_until (Optional[DateTime]): Optional timestamp after which the plan is no longer valid.
        instructions (list[DeviceControlInstruction]): List of control instructions.
        comment (Optional[str]): Optional comment or annotation for the plan.
    """

    plan_id: ID = Field(..., description="Unique ID for the energy management plan.")
    generated_at: DateTime = Field(..., description="Timestamp when the plan was generated.")
    valid_from: Optional[DateTime] = Field(None, description="Start of plan validity (optional).")
    valid_until: Optional[DateTime] = Field(None, description="End of plan validity (optional).")
    instructions: list[DeviceControlInstruction] = Field(
        ..., description="List of control instructions for the plan."
    )
    comment: Optional[str] = Field(None, description="Optional comment or annotation for the plan.")

    def _update_time_range(self) -> None:
        """Updates the from_time and to_time based on the current instructions."""
        if self.instructions:
            self.valid_from = min(i.start_time for i in self.instructions)
            self.valid_until = max(
                i.start_time + to_duration(i.duration) for i in self.instructions
            )
        else:
            self.valid_from = self.valid_until = to_datetime()

    def add_instruction(self, instruction: DeviceControlInstruction) -> None:
        """Adds a new control instruction to the plan and keeps the list sorted.

        Args:
            instruction (DeviceControlInstruction): The control instruction to add.
        """
        self.instructions.append(instruction)
        # Sort the instructions by the start_time DateTime
        self.instructions.sort(key=lambda i: i.start_time)

        # Update from_time and to_time
        self._update_time_range()

    def clear(self) -> None:
        """Removes all control instructions from the plan."""
        self.instructions.clear()
        self.valid_from = self.valid_until = to_datetime()

    def get_active_instructions(
        self, now: Optional[DateTime] = None
    ) -> Optional[list[DeviceControlInstruction]]:
        """Retrieves all control instructions that are currently active.

        Args:
            now (Optional[DateTime]): The current time to evaluate. Defaults to `to_datetime()` if not provided.

        Returns:
            Optional[list[DeviceControlInstruction]]: List of active instructions at the given time.
        """
        now = now or to_datetime()
        active_instructions = [
            i for i in self.instructions if i.start_time <= now < (i.start_time + i.duration)
        ]

        # If there are no active instructions, return None
        if not active_instructions:
            return None

        return active_instructions

    def get_next_instruction(
        self, now: Optional[DateTime] = None
    ) -> Optional[DeviceControlInstruction]:
        """Finds the next upcoming instruction after the given or current time.

        Args:
            now (Optional[DateTime]): The current time to evaluate. Defaults to `DateTime.now()` if not provided.

        Returns:
            Optional[DeviceControlInstruction]: The next instruction to be executed, or None if none are scheduled.
        """
        now = now or to_datetime()
        future_instructions = [
            i for i in self.instructions if compare_datetimes(i.start_time, now).gt
        ]

        # If there are no future instructions, return None
        if not future_instructions:
            return None

        # Safely get the next instruction based on start_time
        return min(future_instructions, key=lambda i: i.start_time)

    def get_instructions_for_device(self, device_id: ID) -> list[DeviceControlInstruction]:
        """Filters the plan's instructions for a specific target device.

        Args:
            device_id (ID): The ID of the device to filter for.

        Returns:
            list[DeviceControlInstruction]: Instructions targeting the specified device.
        """
        return [i for i in self.instructions if i.target_device == device_id]
