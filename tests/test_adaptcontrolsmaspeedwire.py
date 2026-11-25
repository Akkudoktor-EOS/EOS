"""Tests for the independent SMA Speedwire / EMETER decoder."""

import struct
import unittest

from akkudoktoreos.adapter.controllers.sma.smaspeedwire import (
    ENERGY_METER_PROTOCOL_ID,
    SpeedwireDecodeError,
    decode_datagram,
)


def make_packet(records: bytes, *, susy_id: int = 270, serial: int = 258,
                ticker: int = 3_451_005) -> bytes:
    """Build a synthetic documented Speedwire tag envelope."""
    protocol_payload = (
        struct.pack(">H", ENERGY_METER_PROTOCOL_ID)
        + struct.pack(">HI", susy_id, serial)
        + struct.pack(">I", ticker)
        + records
    )
    tag0 = struct.pack(">HHI", 4, 0x02A0, 1)
    data_tag = struct.pack(">HH", len(protocol_payload), 0x0010) + protocol_payload
    end_tag = struct.pack(">HH", 0, 0)
    return b"SMA\x00" + tag0 + data_tag + end_tag


def record(channel: int, index: int, kind: int, tariff: int, value: int) -> bytes:
    length = 8 if kind == 8 else 4
    return bytes((channel, index, kind, tariff)) + value.to_bytes(length, "big")


class TestDecodeDatagram(unittest.TestCase):
    def test_power_import_and_export(self) -> None:
        packet = make_packet(
            record(0, 1, 4, 0, 2350)
            + record(0, 2, 4, 0, 0)
            + record(0, 21, 4, 0, 1600)
        )
        result = decode_datagram(packet, source_ip="192.0.2.10")
        values = result.by_name()
        self.assertEqual(result.susy_id, 270)
        self.assertEqual(result.serial_number, 258)
        self.assertEqual(result.ticker_ms, 3_451_005)
        self.assertEqual(result.source_ip, "192.0.2.10")
        self.assertEqual(values["active_power_import"].value, 235.0)
        self.assertEqual(values["active_power_export"].value, 0.0)
        self.assertEqual(values["l1_active_power_import"].value, 160.0)

    def test_energy_counter_is_watt_seconds_converted_to_kwh(self) -> None:
        # 3,600,000 Ws = 1 kWh.
        packet = make_packet(record(0, 1, 8, 0, 3_600_000))
        result = decode_datagram(packet)
        self.assertEqual(result.by_name()["active_power_import_energy"].value, 1.0)
        self.assertEqual(result.by_name()["active_power_import_energy"].unit, "kWh")

    def test_current_and_voltage_scaling(self) -> None:
        packet = make_packet(
            record(0, 31, 4, 0, 12_345)
            + record(0, 32, 4, 0, 230_100)
        )
        values = decode_datagram(packet).by_name()
        self.assertEqual(values["l1_current"].value, 12.345)
        self.assertEqual(values["l1_voltage"].value, 230.1)

    def test_empty_measurement_payload_is_valid(self) -> None:
        result = decode_datagram(make_packet(b""))
        self.assertEqual(result.measurements, ())

    def test_bad_signature_rejected(self) -> None:
        with self.assertRaises(SpeedwireDecodeError):
            decode_datagram(b"NOPE" + b"\x00" * 20)

    def test_truncated_record_rejected(self) -> None:
        packet = make_packet(bytes((0, 1, 4, 0, 0, 0)))
        with self.assertRaises(SpeedwireDecodeError):
            decode_datagram(packet)

    def test_unsupported_measurement_type_rejected(self) -> None:
        packet = make_packet(bytes((0, 1, 7, 0)) + b"\x00" * 4)
        with self.assertRaises(SpeedwireDecodeError):
            decode_datagram(packet)


if __name__ == "__main__":
    unittest.main()
