# RC-R02XT register / capability matrix

Source: JCH-TW Technical Bulletin SB-12024, version 6.0, printed pages 21–24 (PDF pages 20–23). The locally supplied official PDF was inspected as rendered pages as well as extracted text. The copyrighted PDF is not redistributed. No undocumented write values are used. Reference architecture: JK0526/coselig-modbus-hub tag v1.6.3, commit d8e23c2866cd7fd27862a7198ceb735910c11b32 (MIT).

## Serial / framing

9600 bps, 8 data bits, 1 start bit, 1 stop bit; none/odd/even parity must match the converter DIP settings. Factory parity is none. Slave IDs 1–247; never broadcast. The PDF describes up to 32 converters per physical bus. The full 1–247 address range does not imply 247 electrical loads are supported.

**Modbus RTU frame over TCP ≠ Modbus TCP / MBAP.** Configure a transparent TCP serial gateway. RTU CRC16 uses polynomial 0xA001, initial 0xFFFF, low CRC byte first. Register words are big endian. Stream reads use exact protocol lengths, never sleeps to guess frame boundaries. Default transaction timeout is 1 second. No automatic write retries.

| Function | Request | Response |
|---|---|---|
| FC03 | slave, 03, start u16, count u16, CRC | slave, 03, byte count, words, CRC |
| FC06 | slave, 06, address u16, value u16, CRC | exact echo |
| FC10 | slave, 10, start u16, count u16, byte count, words, CRC | slave, 10, start u16, count u16, CRC |

Protocol supports FC10 framing/validation; normal climate operations use FC06. Firmware DIP configuration must permit the 0x40–0x44 multi-register read (DIPSW1 PIN5 per bulletin). FC10 is confined to documented 0x20–0x23 when used by future profile code, never exposed as a raw UI action.

Exception responses are valid frames: 01 illegal function, 02 illegal address / SAA unsupported function, 03 illegal value or count, 04 communication failure/disconnection, 06 indoor-unit connection in progress. These are device exceptions, not proof of TCP gateway loss.

## Register matrix

All addresses below are hexadecimal wire addresses, not 40001-based labels. R = read-only use by this integration, W = climate write allowlist.

| Address | Meaning / conversion | Use |
|---|---|---|
| 0000 | indoor temperature / initial read probe, 1 °C | R discovery |
| 0004 | 0 cooling-only, 1 cooling/heating | R heat capability |
| 0005 | indoor type: 1 RAD, 2 RAS, 3 RA, 4 CRD, 5 CRS, 6 RPI | R identity |
| 0006 | indoor capacity, raw retained; no invented model | R identity |
| 0007–0008 | ASCII series words, e.g. 4B57 + 4520 = KWE | R identity |
| 0009 | SAAnet version, e.g. 0400 = 4.0 | R identity |
| 000A | outdoor temperature, unsigned 1 °C per bulletin | R capability / slow |
| 000F | outdoor operating current, 0.1 A | R capability / slow |
| 0020 | power: 0 off / 1 on | R/W |
| 0021 | mode: 0 cool, 1 dry, 2 fan, 3 auto, 4 heat; RPI has no auto | R/W |
| 0022 | residential fan: 5 auto, 4 high, 3 medium, 1 low, 7 quiet | R/W |
| 0023 | target 16–32 °C, integer step 1 | R/W, confirmed target readback |
| 002A–002B | vertical/horizontal swing | defined only; no UI/write path |
| 0038 | accumulated outdoor energy, 0.1 kWh; max 40000 then reset 0 | R capability / slow |
| 0039 | hours accumulated since periodic maintenance reset | R capability / slow |
| 003A | hours accumulated since filter-cleaning reset | R capability / slow |
| 0040–0044 | power, mode, fan, indoor °C, raw error | R fast, one FC03 count 5 |

Commercial units expose additional fan values; unknown readings remain raw/unknown, never make the climate unavailable. The shared safe fan subset is used and undocumented quiet writes are refused for RPI. Unsupported mode/fan combinations may return device exception 03; commands are not retried with guessed alternatives. Outdoor temperature is unsigned as documented; sub-zero encoding is not guessed.

## Identity / discovery

1. Exclusive transport lease, FC03 0000 count 1. Timeout or CRC error: no confirmed device. Valid Modbus exception still confirms presence, so continue identity probes.
2. Read 0005–0009 separately: RC-R01XT reports exception 02 for these. Unknown/partial identities stay visible as unsupported legacy/unknown. Types 7/8 identify dehumidifiers; 32 identifies heat exchanger. They never create climate entities.
3. Supported AC identity probes 0004 and each optional sensor register independently. For a new device, exception 02 or timeout leaves that capability absent. On rescan, successful probes add capabilities, exception 02 removes a previously supported capability, and timeout, CRC errors, or other exceptions retain the previous capability. None invalidates the climate. After explicit exception 02 and confirmation, an existing sensor is no longer loaded; its entity-registry record is retained as unavailable for history. Discovery never sends FC06/FC10.
4. Existing/missing/new classifications are shown in results. One failed scan never deletes a stored slave. The interface inference is based on the bulletin's RC-R02XT-only identity register support, not an invented hardware serial number.

## Write boundary

Application profile accepts only power, mode, fan and integral target temperature. Heat is allowed only by official 0004=1, observed mode 4 or explicit per-device override. No arbitrary write service, monitor write command, broadcast, energy reset, maintenance reset, filter reset, swing, dehumidifier or central-controller control exists. Protocol frame builders are internal library functions, not user-facing write endpoints.

After successful FC06 acknowledgement, FC03 0040 count 5 confirms actual climate state; 0023 is separately read for target temperature because the fast block contains no target. Slow polling also reads 0023 to observe changes from a physical remote. Failed readback preserves confirmed climate state and records communication trouble. The bulletin updates the cached fast block once per second, so real-device propagation/readback timing requires hardware acceptance.

## Energy semantics

137 → 13.7 kWh. The entity publishes the hardware counter directly with ENERGY / TOTAL_INCREASING / kWh. HA long-term statistics handles decreases as counter reset; there is no artificial offset accumulator. Values above documented maximum are unknown. Counters are read again after restart; stale energy values are never restored as confirmed live readings.
