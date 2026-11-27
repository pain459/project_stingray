# Project Stingray — Sensor Details

This document describes the three physical sensor modules — the combined
SHT45 + SGP41 2-in-1 board plus SCD4X and SPS30 — the four logical drivers they
expose, how those drivers talk to the devices, what units they publish, and
which warm-up/validation rules apply. All
facts here are taken from the driver sources in `src/stingray/sensors/` and from
live probes on the Pi. See [`wiring.md`](wiring.md) for the electrical
connections and [`BUILD_GUIDE.md`](../BUILD_GUIDE.md) for the full build.

---

## 1. Overview

| Sensor | Measures | Address | Cadence | Interface | Driver |
|---|---|---|---|---|---|
| SHT45 | temperature (°C), RH (%) | `0x44` | 1 s | I²C1 | `src/stingray/sensors/sht45.py` |
| SCD4X (SCD41 verified) | CO₂ (ppm), temp (°C), RH (%) | `0x62` | 5 s | I²C1 | `src/stingray/sensors/scd4x.py` |
| SGP41 | VOC index, NOx index | `0x59` | 1 s | I²C1 | `src/stingray/sensors/sgp41.py` |
| SPS30 | PM1/PM2.5/PM4/PM10 mass, number concentrations, typical size | `0x69` | 10 s | I²C1 | `src/stingray/sensors/sps30.py` |

The SHT45 and SGP41 are **one physical 2-in-1 module** on a single board, with
two hard-wired I²C addresses (`0x44` and `0x59`). It is wired and powered as one
device but appears as two logical sensors in the scheduler, storage and health
report. SCD4X and SPS30 are separate modules: three physical modules, four
logical sensor streams in total.

The SHT45 is the **authoritative** temperature/humidity source. Its readings
are also used internally to compensate the SGP41 gas measurements on the same
module.

Each driver implements the same `SensorDriver` contract: `initialize()`,
`read()`, `health()`, `close()`. A single `read()` may emit several
`Measurement` objects (for example SCD4X emits CO₂, temperature and humidity at
once). Failures are isolated per sensor: one broken device never stops the
others, and an absent device is retried as `DISCONNECTED`.

---

## 2. Shared I²C protocol rules

All four devices are Sensirion parts using a **command-then-read** protocol:

1. Write a 16-bit command (or a single 8-bit command for SHT45), big-endian.
2. Wait the datasheet-specified conversion time.
3. Read the response with a **bare I²C read** — no register byte.

`src/stingray/sensors/i2c.py :: Smbus2Transport.read()` uses
`smbus2.i2c_msg.read()` followed by `bus.i2c_rdwr()`. This matters: SMBus
`read_i2c_block_data(address, register, length)` injects a register byte that
these chips do not expect, causing a NACK. Earlier attempts with that API and
with raw ioctl calls failed; the `i2c_msg` path is the verified one.

**CRC-8:** every 2-byte data word is followed by a CRC byte (polynomial `0x31`,
init `0xFF`, no reflection, no final XOR; reference vector `0xBE 0xEF` →
`0x92`). Commands with arguments also carry a CRC byte. Drivers validate every
word and raise `SensorReadError` on mismatch rather than publishing corrupt
data.

**Error classification:** `errno` values `EREMOTEIO`, `EIO`, `ENXIO`, `ENODEV`,
`EBADF` and `ENOENT` are treated as a disconnected sensor (`is_disconnect_error`)
and retried. Missing `smbus2` or a missing `/dev/i2c-1` is a deployment error
and stops startup with a clear message.

---

## 3. SHT45 — temperature and relative humidity

- **Address:** `0x44`. **Cadence:** 1 s.
- **Commands:** high-precision measurement `0xFD` (clock stretching disabled),
  soft reset `0x94`, read serial `0x89`.
- **Timing:** measurement delay 12 ms (datasheet max 8.3 ms + margin), reset
  2 ms, serial 2 ms.
- **Conversion:** raw temperature and humidity ticks → °C and %RH using the
  datasheet formulas, clamped to the physical range.
- **Serial number:** 32-bit, read at startup and cached; CRC checked per word.
- **Role:** `read_sample()` returns `(temperature_c, rh_percent)` without
  emitting `Measurement` objects; this is the compensation source for SGP41.
  `read()` emits temperature and humidity measurements for storage.
- **Failure policy:** an absent device is reported `DISCONNECTED` and retried;
  malformed responses surface as read errors.

---

## 4. SCD4X — CO₂, temperature, humidity

- **Address:** `0x62`. **Cadence:** 5 s. Verified variant: SCD41.
- **Mode:** periodic measurement. `initialize()` sends Start Periodic
  Measurement `0x21B1`; `Read Measurement` `0xEC05` returns 9 bytes (3 words +
  3 CRCs). Stop is `0x3F86`. Variant query `0x202F`, data-ready query `0x2029`.
- **Conversions:**
  - CO₂ = raw value, in **ppm**
  - Temperature = `-45 + 175 * raw / 65535`, in **°C**
  - RH = `100 * raw / 65535`, in **%**
- **Warm-up:** the data-ready status can NACK during warm-up; this is normal and
  the driver yields no measurement for that tick rather than logging an error.
- **Variant:** the driver can query and log the human-readable variant string.
- **Units published:** `ppm`, `°C`, `%`.

Note: the SCD4X's own temperature/humidity are useful but SHT45 remains the
authoritative source for temperature and humidity elsewhere in the system.

---

## 5. SGP41 — VOC and NOx indices

- **Address:** `0x59`. **Cadence:** 1 s.
- **Commands:** condition `0x2612`, measure raw signals `0x2619`, self-test
  `0x280E`, heater off `0x3615`, serial `0x3682`. Delays around 50 ms for
  conditioning/measurement.
- **Compensation:** both conditioning and measurement take RH and temperature
  ticks as arguments. The driver takes them from the SHT45
  (`compensation_source()` reports the source). Defaults of 50 %RH / 25 °C are
  used only if the SHT45 is unavailable.
- **Conditioning:** the datasheet recommends one conditioning cycle per second
  for the first 10 seconds after power-on. `CONDITIONING_CYCLES = 10`. During
  this period the driver emits raw signals but **no index values**; after the
  tenth cycle it logs "conditioning complete" and starts publishing indices.
- **Index algorithm:** the SGP41 datasheet does not give a direct raw → index
  formula. `src/stingray/sensors/gas_index.py` is a pure-Python port of
  Sensirion's official Gas Index Algorithm v3.2.0 (BSD-3-Clause), which applies
  a running mean/variance MOX model, sigmoid and adaptive low-pass filter.
  Publishing raw signals as "indices" would be wrong.
- **Index warm-up:** the algorithm has an initial blackout of 45 s and a longer
  initial learning phase; `in_warmup()` / `indices_warming_up()` expose this.
  Algorithm state is **not persisted**, so a restart begins a fresh learning
  phase.
- **Self test:** success code `0xD400`.
- **Units published:** dimensionless VOC index and NOx index.

---

## 6. SPS30 — particulate matter

- **Address:** `0x69`. **Cadence:** 10 s. Configured in **I²C mode**.
- **Commands:** start measurement `0x0010`, read measured values `0x0300`,
  firmware version `0xD100`, serial `0xD033`. Output format `0x05` = 16-bit
  unsigned integers.
- **Response:** 10 words (30 bytes, each word followed by a CRC): PM1.0, PM2.5,
  PM4.0, PM10 mass concentrations; PM0.5, PM1.0, PM2.5, PM4.0, PM10 number
  concentrations; typical particle size.
- **Units:** mass concentrations are **µg/m³**; number concentrations are
  particles/cm³; typical particle size is published in **µm**.
- **Important conversion:** the SPS30 reports typical particle size in **nm**
  on the wire. `_tps_to_um()` divides by 1000 before publishing (`TPS_UM`).
  Getting this wrong makes the value look 1000× too large.
- **Serial number:** up to 48 bytes of hex pairs + CRCs; read in chunks ≤30
  bytes because SMBus block reads are limited to 32 bytes.
- **Failure policy:** absent device → `DISCONNECTED` and retry; CRC mismatch →
  read error.

---

## 7. Measurements and units in the database

`Measurement` is a frozen pydantic model in `src/stingray/core/models.py`.
Every sample carries `sensor_id`, `metric_id`, `value`, `unit` and `received_at`
(UTC). `METRIC_UNITS` maps metric IDs to canonical units so the API and
dashboard do not guess.

| Metric ID | Unit |
|---|---|
| `TEMPERATURE_C` | °C |
| `RELATIVE_HUMIDITY_PERCENT` | % |
| `CO2_PPM` | ppm |
| `VOC_INDEX` | index |
| `NOX_INDEX` | index |
| `PM1_UG_M3`, `PM2_5_UG_M3`, `PM4_UG_M3`, `PM10_UG_M3` | µg/m³ |
| `PM1_N_CM3`, `PM2_5_N_CM3`, `PM4_N_CM3`, `PM10_N_CM3` | #/cm³ |
| `TPS_UM` | µm |

Rows are written to ZSTD-compressed, day-partitioned Parquet on the SSD. The
dashboard and the hourly summary both read that same store — there is no
second, divergent data path.

---

## 8. Validation and safe probing

```sh
# Read serials, firmware and one live sample from each sensor; checks CRCs.
uv run python scripts/probe_i2c.py

# System-level checks (buses, thermals, throttling, devices).
bash scripts/preflight.sh
```

> **Never run `scripts/validate_gas_sensors.py` while `stingray.service` is
> running.** It drives the same I²C bus from a second process and will corrupt
> reads. Stop the service, run the validator, then restart it.

Expected healthy journal lines: each sensor `READY` at startup, no CRC mismatch
messages, SGP41 "conditioning complete" after ~10 s, and values changing at the
cadences in §1.
