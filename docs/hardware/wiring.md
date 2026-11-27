# Project Stingray — Wiring and Pinouts

This document explains how the hardware is connected to the Raspberry Pi and
how to reproduce that wiring from the same parts. It is written for someone who
has bought the equipment in [`BUILD_GUIDE.md`](../BUILD_GUIDE.md) and has the Pi
booted with Debian 13.

> **Sources of truth.** Two kinds of facts appear below:
>
> - **Verified from the running system** — bus numbers, device addresses,
>   device nodes and the display control pins used by the software. These were
>   read from `/boot/firmware/config.txt`, `/dev/*` and the driver source.
> - **Standard Pi header mapping** — the BCM/physical-pin correspondence for
>   I²C1 and SPI0. This is fixed by the 40-pin header and is safe to use.
>
> The exact position of each wire inside the DFR0918 terminal blocks was **not**
> photographed or labelled on the original harness. Treat the per-device tables
> as the logical connection map; confirm the terminal-block labels on your own
> board before applying power.

---

## 1. 40-pin header reference (BCM numbering)

The drivers use **BCM** pin numbers (`GPIO.setmode(GPIO.BCM)` in
`src/stingray/display/driver.py`). Physical pin numbers are counted with the
header at the top of the board, SD card slot facing you.

| Physical pin | BCM | Function used here | Connected to |
|---|---|---|---|
| 1 | — | 3.3 V power | all sensor VCC, display VCC |
| 3 | GPIO2 | **I²C1 SDA** | all four Sensirion sensors |
| 5 | GPIO3 | **I²C1 SCL** | all four Sensirion sensors |
| 6 | — | Ground | common ground |
| 9 | — | Ground | common ground |
| 17 | — | 3.3 V power | alternative VCC point |
| 18 | GPIO24 | **Display RESET** | ILI9341 RESET |
| 19 | GPIO10 | **SPI0 MOSI** | ILI9341 SDI / MOSI |
| 21 | GPIO9 | **SPI0 MISO** | ILI9341 SDO / MISO (if your board exposes it) |
| 22 | GPIO25 | **Display DC** | ILI9341 D/C |
| 23 | GPIO11 | **SPI0 SCLK** | ILI9341 SCK |
| 24 | GPIO8 | **SPI0 CE0** | ILI9341 CS |
| 2 / 4 | — | 5 V power | only for a module that *requires* 5 V; see §5 |

The active display driver is `src/stingray/display/driver.py :: ILI9341Driver`:

```python
ILI9341Driver(spi_bus=0, spi_cs=0, dc_pin=25, reset_pin=24,
              max_speed_hz=10_000_000)
```

`spi_cs=0` means SPI0 chip-select 0, i.e. physical pin 24 (BCM8). There is no
separate software-driven CS pin in that class. A second, older class in
`src/stingray/display/ili9341.py` defaults to `cs_pin=8`, `max_speed_hz=40 MHz`;
it is **not** imported by `display_service.py` and should not be used as the
wiring reference.

---

## 2. Bus map

### I²C — `/dev/i2c-1`

Three physical modules expose four I²C devices on one shared bus. The SHT45 and
SGP41 live on the **same 2-in-1 board** but have separate hard-wired addresses;
SCD4X and SPS30 are separate modules. Addresses are confirmed by the committed
config `config/application.combined.yaml`:

| Physical module | Logical driver | 7-bit address | Decimal | Cadence | Config key |
|---|---|---|---|---|---|
| SHT45 + SGP41 2-in-1 | SHT45 | `0x44` | 68 | 1 s | `sensors.sht45` |
| SHT45 + SGP41 2-in-1 | SGP41 | `0x59` | 89 | 1 s | `sensors.sgp41` |
| SCD4X (SCD41 verified) | SCD4X | `0x62` | 98 | 5 s | `sensors.scd4x` |
| SPS30 | SPS30 | `0x69` | 105 | 10 s | `sensors.sps30` |

There are no address conflicts, so every device can sit on the same SDA/SCL
pair. `/dev/i2c-20` and `/dev/i2c-21` also exist on this host but are HDMI DDC
buses — **do not use them**.

Enable and verify:

```ini
# /boot/firmware/config.txt
dtparam=i2c_arm=on
dtparam=spi=on
```

```sh
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
sudo reboot
ls -l /dev/i2c-1 /dev/spidev0.0
sudo i2cdetect -y 1
```

`i2cdetect` should show `44`, `59`, `62` and `69`. Note the historical baseline
in [`inventory.md`](inventory.md) recorded I²C and SPI as disabled; that was
corrected during the wiring phase and is now enabled on the running Pi.

### SPI — `/dev/spidev0.0`

SPI0, CE0, mode 0, 10 MHz. Only the ILI9341 display uses SPI. The driver opens
`/dev/spidev0.0` through `spidev` and toggles DC/RESET through `RPi.GPIO`.

---

## 3. Logical connections

### All I²C devices (SHT45 + SGP41 2-in-1 module, SCD4X, SPS30)

| Sensor pin | Goes to | Pi pin |
|---|---|---|
| SDA | shared SDA | physical 3 / BCM2 |
| SCL | shared SCL | physical 5 / BCM3 |
| VCC | 3.3 V rail | physical 1 or 17 |
| GND | common ground | physical 6 or 9 |

Every board must share the same ground. Connect ground **first** and remove
power before changing any wire.

### ILI9341 TFT

| Display pin | Goes to | Pi pin |
|---|---|---|
| VCC | 3.3 V | physical 1 or 17 |
| GND | ground | physical 6 or 9 |
| SCK | SPI0 SCLK | physical 23 / BCM11 |
| SDI / MOSI | SPI0 MOSI | physical 19 / BCM10 |
| SDO / MISO | SPI0 MISO | physical 21 / BCM9 (leave unconnected if not used) |
| CS | SPI0 CE0 | physical 24 / BCM8 |
| D/C | GPIO output | physical 22 / BCM25 |
| RESET | GPIO output | physical 18 / BCM24 |
| LED / backlight | see your board | do **not** assume it can be driven from GPIO; many breakouts tie it to 3.3 V through a resistor |

If your display board has a built-in regulator and expects 5 V on VCC, check its
logic pins: the Pi GPIO is **3.3 V only**. Either use a 3.3 V-compatible board or
add a level shifter on SCK/MOSI/CS/DC/RESET. The original build used a 3.3 V
logic display.

### DFRobot DFR0918 terminal HAT

The DFR0918 is a **terminal-block breakout**, not a power supply or regulator.
It provides screw terminals for the Pi header signals. Use it to make the
shared SDA/SCL/GND/3.3 V rails tidy; do not expect it to convert voltage or
supply extra current. Its exact terminal labelling was not captured for the
original unit, so map each terminal with a continuity tester before use.

---

## 4. Pull-ups, length and signal quality

- The Pi enables internal pull-ups on BCM2/BCM3 when I²C1 is active. The sensor
  breakouts used here do not need extra external pull-ups. If your modules have
  their own, do not remove them, but avoid stacking many low-value pull-ups.
- Keep the SDA/SCL run short (tens of centimetres). Four devices on one bus are
  well within the I²C fan-out limit; long cables cause CRC errors and NACKs.
- If `i2cdetect` sees a device but the driver reports repeated CRC mismatches,
  suspect wiring, inadequate power, or a too-fast bus before suspecting code.
- The SPS30 serial number is longer than a 32-byte SMBus block; the driver reads
  it in chunks. This is a software detail, not a wiring difference.

---

## 5. Power and safety rules

1. **Common ground everywhere.** Every sensor and the display must reference
   the Pi's ground.
2. **3.3 V logic only.** Never feed 5 V into any GPIO pin (SDA, SCL, DC, RESET,
   CS, SPI). Pi GPIO is not 5 V tolerant.
3. **Do not power peripherals from a GPIO pin.** GPIO outputs are for signals;
   draw sensor power from the 3.3 V rail or a properly rated supply.
4. **SPS30 supply check.** Some SPS30 breakouts require 5 V while their I²C
   lines remain 3.3 V logic. If your board states 5 V supply: power it from the
   Pi's 5 V rail (physical pin 2 or 4), add a level shifter on SDA/SCL if the
   module pulls up to 5 V, and keep ground shared. The original harness used
   the 3.3 V rail; verify against *your* module's datasheet before powering.
5. **No unidentified fan.** The original host was documented as booting only
   with its fan disconnected, and no fan is currently attached. Do not wire a
   fan straight to GPIO; use a proper driver circuit and a known supply. The
   system runs without a fan, but monitor `vcgencmd measure_temp` and
   `vcgencmd get_throttled`.
6. **Storage on SSD.** Mount the SSD at `/mnt/stingray`; keep the microSD for
   the OS only.

---

## 6. Service account permissions

`stingray.service` runs as `monusr`, which must be a member of `i2c`, `spi` and
`gpio` so it can open `/dev/i2c-1`, `/dev/spidev0.0` and drive DC/RESET without
root:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin monusr
sudo usermod -aG i2c,spi,gpio monusr
```

---

## 7. Verification checklist

After wiring and booting:

```sh
ls -l /dev/i2c-1 /dev/spidev0.0
sudo i2cdetect -y 1                       # expect 44 59 62 69
vcgencmd get_throttled                    # expect 0x0
vcgencmd measure_temp
bash scripts/preflight.sh
uv run python scripts/probe_i2c.py        # serials, firmware, live samples, CRCs
```

Then start the service and check the journal for all four logical sensor streams
`READY` (SHT45 and SGP41 are the two streams of the one 2-in-1 module):

```sh
sudo systemctl restart stingray.service
sudo journalctl -u stingray.service -n 60 --no-pager
curl -s http://127.0.0.1:8081/api/health | jq
```

**Do not run `scripts/validate_gas_sensors.py` while `stingray.service` is
running** — two processes driving the same I²C bus produce corrupt reads. Stop
the service, validate, then restart.

---

## 8. Open items for a new build

- [ ] Confirm the DFR0918 terminal labels on your board and record them here.
- [ ] Confirm your SPS30 breakout's supply voltage (3.3 V vs 5 V) and add a
      level shifter only if its I²C pull-ups go to 5 V.
- [ ] Confirm the ILI9341 board revision and whether its backlight is tied to
      3.3 V or needs a separate drive.
- [ ] Decide whether to add a fan with a proper driver circuit; the original
      build runs without one.
