"""Read SMA Energy Meter EMETER-20 measurements from Speedwire UDP.

Protocol reference:
    SMA, "SMA Energy Meter Meter Protocol", EMETER-Protocol-TI-en-10.
    https://developer.sma.de/fileadmin/content/www.developer.sma.de/docs/EMETER-Protokoll-TI-en-10.pdf

This implementation is independently written against the published protocol
description and uses only the Python standard library.

You mayp print decoded EMETER readings as JSON-like Python dictionaries for testing.

Usage::

    uv run python src/akkudoktoreos/adapter/controllers/sma/smaspeedwire.py

"""

import socket
import struct
from dataclasses import dataclass
from typing import Iterator

from loguru import logger

from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

MULTICAST_ADDRESS = "239.12.255.254"
SPEEDWIRE_PORT = 9522
# Listen on all interfaces: the SMA Energy Meter multicasts on the LAN, and EOS
# has to search for it without knowing the interface in advance.
ANY_INTERFACE_IP = "0.0.0.0"  # noqa: S104
SMA_SIGNATURE = b"SMA\x00"
ENERGY_METER_PROTOCOL_ID = 0x6069
TAG_END = 0x0000
TAG0 = 0x02A0
TAG_SMA_NET2 = 0x0010

# OBIS measurement index C -> (name, instantaneous unit, energy unit)
# The meter's type-4 values are instantaneous values; type-8 values are
# energy counters. Counter values are transmitted in watt-seconds (or the
# corresponding var-seconds / VA-seconds for reactive/apparent energy).
_OBIS: dict[int, tuple[str, str, str]] = {
    1: ("active_power_import", "W", "kWh"),
    2: ("active_power_export", "W", "kWh"),
    3: ("reactive_power_import", "var", "kvarh"),
    4: ("reactive_power_export", "var", "kvarh"),
    9: ("apparent_power_import", "VA", "kVAh"),
    10: ("apparent_power_export", "VA", "kVAh"),
    13: ("power_factor", "", ""),
    21: ("l1_active_power_import", "W", "kWh"),
    22: ("l1_active_power_export", "W", "kWh"),
    23: ("l1_reactive_power_import", "var", "kvarh"),
    24: ("l1_reactive_power_export", "var", "kvarh"),
    29: ("l1_apparent_power_import", "VA", "kVAh"),
    30: ("l1_apparent_power_export", "VA", "kVAh"),
    31: ("l1_current", "A", ""),
    32: ("l1_voltage", "V", ""),
    41: ("l2_active_power_import", "W", "kWh"),
    42: ("l2_active_power_export", "W", "kWh"),
    43: ("l2_reactive_power_import", "var", "kvarh"),
    44: ("l2_reactive_power_export", "var", "kvarh"),
    49: ("l2_apparent_power_import", "VA", "kVAh"),
    50: ("l2_apparent_power_export", "VA", "kVAh"),
    51: ("l2_current", "A", ""),
    52: ("l2_voltage", "V", ""),
    61: ("l3_active_power_import", "W", "kWh"),
    62: ("l3_active_power_export", "W", "kWh"),
    63: ("l3_reactive_power_import", "var", "kvarh"),
    64: ("l3_reactive_power_export", "var", "kvarh"),
    69: ("l3_apparent_power_import", "VA", "kVAh"),
    70: ("l3_apparent_power_export", "VA", "kVAh"),
    71: ("l3_current", "A", ""),
    72: ("l3_voltage", "V", ""),
}


class SpeedwireDecodeError(ValueError):
    """Raised when a datagram is malformed or is not an EMETER telegram."""


@dataclass(frozen=True, slots=True)
class ObisCode:
    """The four fields carried by an SMA OBIS identifier."""

    channel: int
    index: int
    measurement_type: int
    tariff: int

    @property
    def text(self) -> str:
        """Return the common ``B:C.D.E`` representation (class A is 1)."""
        return f"1:{self.index}.{self.measurement_type}.{self.tariff}"


@dataclass(frozen=True, slots=True)
class Measurement:
    """A decoded measurement, retaining its original OBIS fields and value."""

    obis: ObisCode
    raw_value: int
    name: str
    value: float
    unit: str


@dataclass(frozen=True, slots=True)
class MeterTelegram:
    """Decoded contents of one SMA Energy Meter protocol telegram."""

    susy_id: int
    serial_number: int
    ticker_ms: int
    measurements: tuple[Measurement, ...]
    source_ip: str | None = None
    received_at: DateTime | None = None

    @property
    def device_address(self) -> str:
        """Return the six-byte SMA device address as hexadecimal."""
        return f"{self.susy_id:04x}{self.serial_number:08x}"

    def by_name(self) -> dict[str, Measurement]:
        """Return recognized measurements keyed by name.

        If a telegram contains repeated recognized names, the last record wins.
        For retaining all records (including duplicate tariffs/channels), use
        ``measurements`` directly.
        """
        return {item.name: item for item in self.measurements}

    def values(self) -> dict[str, float]:
        """Return recognized numeric values keyed by name."""
        return {item.name: item.value for item in self.measurements}


def _read_tags(packet: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (tag_id, payload) from the outer SMA tag envelope."""
    if len(packet) < 8 or packet[:4] != SMA_SIGNATURE:
        raise SpeedwireDecodeError("Missing SMA signature or truncated header")

    pos = 4
    while pos < len(packet):
        if len(packet) - pos < 4:
            raise SpeedwireDecodeError("Truncated Speedwire tag header")

        payload_length, tag_id = struct.unpack_from(">HH", packet, pos)
        pos += 4

        if payload_length == 0 and tag_id == TAG_END:
            if pos != len(packet):
                # Padding/trailing bytes are not part of the documented
                # envelope. Reject rather than silently parse ambiguous data.
                raise SpeedwireDecodeError("Unexpected bytes after end tag")
            return

        end = pos + payload_length
        if end > len(packet):
            raise SpeedwireDecodeError("Speedwire tag exceeds datagram length")

        yield tag_id, packet[pos:end]
        pos = end

    raise SpeedwireDecodeError("Missing Speedwire end tag")


def _decode_record(payload: bytes, pos: int) -> tuple[Measurement | None, int]:
    """Decode one OBIS record at pos and return (measurement, next_pos)."""
    if len(payload) - pos < 4:
        raise SpeedwireDecodeError("Truncated OBIS identifier")

    obis = ObisCode(*payload[pos : pos + 4])
    pos += 4

    # The specification defines the manufacturer-specific software version
    # (B=144) as a four-byte value, irrespective of the standard D meanings.
    if obis.channel == 144:
        value_length = 4
    elif obis.measurement_type == 4:
        value_length = 4
    elif obis.measurement_type == 8:
        value_length = 8
    else:
        raise SpeedwireDecodeError(
            f"Unsupported OBIS measurement type {obis.measurement_type} for identifier {obis.text}"
        )

    if len(payload) - pos < value_length:
        raise SpeedwireDecodeError(f"Truncated value for OBIS {obis.text}")

    raw = int.from_bytes(payload[pos : pos + value_length], "big", signed=False)
    pos += value_length

    if obis.channel == 144:
        # Firmware-version payload is Major, Minor, Build, Revision byte.
        # Keep it available as a raw measurement; revision character decoding
        # is manufacturer-specific and not a numeric physical quantity.
        measurement = Measurement(
            obis=obis,
            raw_value=raw,
            name="manufacturer_144_value",
            value=float(raw),
            unit="",
        )
        return measurement, pos

    definition = _OBIS.get(obis.index)
    if definition is None:
        # Unknown standard/vendor record is skipped after safely consuming it.
        return None, pos

    base_name, instant_unit, energy_unit = definition
    if obis.measurement_type == 8:
        name = base_name + "_energy"
        unit = energy_unit or "Ws"
        value = raw / 3_600_000.0
    else:
        name = base_name
        unit = instant_unit
        # Power values (W/var/VA) are transmitted with 0.1 resolution
        # (raw 11451 = 1145.1 W); current and voltage are milliamps and
        # millivolts, and power factor has 0.001 resolution.
        if instant_unit in ("W", "var", "VA"):
            value = raw / 10.0
        else:
            value = raw / 1000.0

    return Measurement(obis, raw, name, value, unit), pos


def decode_datagram(
    packet: bytes,
    *,
    source_ip: str | None = None,
    received_at: DateTime | None = None,
) -> MeterTelegram:
    """Decode one full Speedwire UDP datagram containing an EMETER telegram.

    Unknown outer tags are ignored. A matching SMA Net 2 tag must contain
    protocol ID 0x6069 followed by the 6-byte meter address, a 32-bit
    millisecond ticker, and zero or more OBIS/value records.
    """
    data_payloads: list[bytes] = []
    saw_tag0 = False

    for tag_id, payload in _read_tags(packet):
        if tag_id == TAG0:
            if len(payload) != 4:
                raise SpeedwireDecodeError("Unexpected Tag0 payload length")
            saw_tag0 = True
        elif tag_id == TAG_SMA_NET2:
            data_payloads.append(payload)

    if not saw_tag0:
        raise SpeedwireDecodeError("Missing Speedwire Tag0")
    if not data_payloads:
        raise SpeedwireDecodeError("Missing SMA Net 2 data tag")

    matching: list[bytes] = []
    for payload in data_payloads:
        if len(payload) >= 2 and int.from_bytes(payload[:2], "big") == ENERGY_METER_PROTOCOL_ID:
            matching.append(payload)

    if len(matching) != 1:
        raise SpeedwireDecodeError(
            "Expected exactly one SMA Energy Meter protocol payload (0x6069)"
        )

    payload = matching[0]
    if len(payload) < 12:
        raise SpeedwireDecodeError("Truncated EMETER protocol header")

    # The two-byte protocol ID is followed by a 6-byte SMA address and
    # a four-byte ticker in milliseconds (which wraps around).
    susy_id = int.from_bytes(payload[2:4], "big")
    serial_number = int.from_bytes(payload[4:8], "big")
    ticker_ms = int.from_bytes(payload[8:12], "big")

    measurements: list[Measurement] = []
    pos = 12
    while pos < len(payload):
        measurement, pos = _decode_record(payload, pos)
        if measurement is not None:
            measurements.append(measurement)

    return MeterTelegram(
        susy_id=susy_id,
        serial_number=serial_number,
        ticker_ms=ticker_ms,
        measurements=tuple(measurements),
        source_ip=source_ip,
        received_at=received_at,
    )


class SpeedwireListener:
    """Receive and decode EMETER Speedwire multicast telegrams."""

    def __init__(
        self,
        interface_ip: str = ANY_INTERFACE_IP,
        *,
        multicast_address: str = MULTICAST_ADDRESS,
        port: int = SPEEDWIRE_PORT,
        timeout: float = 3.0,
        expected_serial: int | None = None,
    ) -> None:
        """Configure the listener.

        Args:
            interface_ip: Local IPv4 address to join multicast on. Use
                ``0.0.0.0`` for the OS-selected default interface.
            multicast_address: Speedwire multicast group.
            port: UDP port (normally 9522).
            timeout: Socket timeout in seconds.
            expected_serial: Optional meter serial number filter.
        """
        self.interface_ip = interface_ip
        self.multicast_address = multicast_address
        self.port = port
        self.timeout = timeout
        self.expected_serial = expected_serial
        self._socket: socket.socket | None = None
        self._last_decode_error: str | None = None

    def open(self) -> None:
        """Open the UDP socket and join the multicast group."""
        if self._socket is not None:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            membership = struct.pack(
                "=4s4s",
                socket.inet_aton(self.multicast_address),
                socket.inet_aton(self.interface_ip),
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.settimeout(self.timeout)
        except Exception:
            sock.close()
            raise
        self._socket = sock

    def close(self) -> None:
        """Leave multicast and close the socket."""
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> "SpeedwireListener":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def receive(self) -> MeterTelegram | None:
        """Receive one telegram; return None on timeout or malformed packets."""
        if self._socket is None:
            self.open()
        sock = self._socket
        if sock is None:
            raise RuntimeError("Listener socket failed to initialize")
        try:
            packet, address = sock.recvfrom(65535)
        except TimeoutError:
            return None

        try:
            telegram = decode_datagram(
                packet,
                source_ip=address[0],
                received_at=to_datetime(in_timezone="UTC"),
            )
        except SpeedwireDecodeError as exc:
            if str(exc) != self._last_decode_error:
                self._last_decode_error = str(exc)
                logger.warning(f"Speedwire decode error: {exc}")
            return None

        if self.expected_serial is not None and telegram.serial_number != self.expected_serial:
            return None
        return telegram

    def listen(self) -> Iterator[MeterTelegram]:
        """Yield valid telegrams indefinitely, ignoring timeouts and bad packets."""
        while True:
            telegram = self.receive()
            if telegram is not None:
                yield telegram


def main() -> None:
    """Print decoded EMETER readings as JSON-like Python dictionaries."""
    import json

    interface_ip = ANY_INTERFACE_IP  # Set to the local LAN interface IP if needed.
    with SpeedwireListener(interface_ip=interface_ip, timeout=3.0) as listener:
        print(f"Listening on UDP {SPEEDWIRE_PORT}, multicast {MULTICAST_ADDRESS}; Ctrl+C to stop.")
        try:
            for telegram in listener.listen():
                output = {
                    "received_at": telegram.received_at.isoformat()
                    if telegram.received_at
                    else None,
                    "source_ip": telegram.source_ip,
                    "susy_id": telegram.susy_id,
                    "serial_number": telegram.serial_number,
                    "ticker_ms": telegram.ticker_ms,
                    "values": {
                        name: {"value": item.value, "unit": item.unit}
                        for name, item in telegram.by_name().items()
                    },
                }
                print(json.dumps(output, indent=2))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
