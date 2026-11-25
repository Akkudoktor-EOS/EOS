# SMA EMETER-20 Speedwire reader

Independent Python implementation of the documented SMA Energy Meter
Speedwire protocol, intended as a starting point for Apache-licensed projects.

## Files

- `smaspeedwire.py`: packet/tag parser, OBIS decoder and UDP multicast listener.
- `test_adapterinvertersmaspeedwire.py`: synthetic unit tests; no physical meter required.

## Run

Requires Python 3.10+ and no third-party packages.

```bash
python3 -m unittest -v
python3 sma_speedwire.py
```

Set `interface_ip` in `main()` to the local IPv4 address of the network
interface connected to the meter if multicast interface selection is needed.

Inspect the network if no readings arrive:

```bash
sudo tcpdump -ni any 'udp port 9522'
```

## Notes and limitations

- The decoder follows the published SMA Energy Meter protocol structure:
  outer SMA tag envelope, Tag0, SMA Net 2 tag, protocol ID `0x6069`, meter
  address, wrapping millisecond ticker, and OBIS/value records.
- The ticker is a device-relative millisecond counter, not a Unix timestamp.
  `received_at` is the local UTC receive time.
- Unknown standard measurement indexes are consumed safely and omitted from
  the named measurement list. Unsupported standard measurement types are
  rejected rather than guessed.
- Energy counters are converted from watt-seconds to kWh (or corresponding
  kvarh/kVAh labels). Instantaneous power is kept in watts; current and voltage
  are converted from mA/mV; power factor is divided by 1000.
- Test packets are synthetic and test the parser mechanics, not a capture from
  a physical EMETER-20. Validate field values against a real packet capture
  before using them for control decisions.
- This implementation is independently written from the published protocol
  description; it does not include code copied from GPL implementations.
  Review the applicable SMA documentation terms and your project's legal
  requirements before distribution.

Protocol reference:
https://developer.sma.de/fileadmin/content/www.developer.sma.de/docs/EMETER-Protokoll-TI-en-10.pdf
