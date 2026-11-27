# Hardware Inventory — Project Stingray

**Device host:** `stingray`
**Captured:** 2026-11-24 (Asia/Kolkata, UTC+05:30)
**Commit at capture:** `6cfbf40`
**Method:** read-only commands executed over SSH on the Pi; no electrical work performed.

> Sensitive identifiers (board serial, MAC addresses, network details) are
> intentionally redacted from this committed document.

## 1. Compute platform

| Item | Value |
|---|---|
| Model | Raspberry Pi 4 Model B Rev 1.1 |
| Board revision code | `b03111` |
| SoC | BCM2711 (quad-core Cortex-A72) |
| Architecture | `aarch64` (64-bit) |
| CPU frequency (configured) | `arm_freq=1500` |
| GPU frequency | `gpu_freq=500` |
| RAM | 1.8 GiB total (~2 GiB part) |
| Serial | *(redacted)* |

## 2. Operating system

| Item | Value |
|---|---|
| Distribution | Debian GNU/Linux 13 (trixie) |
| Debian point release | 13.8 |
| Kernel | `6.18.60+rpt-rpi-v8` |
| Kernel build | `#1 SMP PREEMPT Debian 1:6.18.60-1+rpt1 (2026-11-03)` |
| Firmware base | Raspberry Pi Foundation `rpt` kernel |
| Time zone | `Asia/Kolkata` (IST, UTC+05:30) |
| NTP | `systemd-timesyncd` active, clock synchronized |

## 3. Storage

| Item | Value |
|---|---|
| Root device | `/dev/mmcblk0p2` (ext4) |
| Boot partition | `/dev/mmcblk0p1` (vfat, mounted `/boot/firmware`) |
| Card size | 238.3 GB |
| Root filesystem size | 235 GB, ~4.8 GB used (3%) |
| Boot filesystem | 505 MB, 68 MB used (14%) |
| Swap | `zram0` 1.8 GiB + `loop0` 1.8 GiB (both swap) |
| Mount options | `errors=remount-ro` on `/boot/firmware` |

## 4. Interfaces and device nodes

| Interface | State at capture |
|---|---|
| I2C | **Not enabled.** No `/dev/i2c-*` nodes. Only `i2c-20`/`i2c-21` (HDMI DDC buses, `brcm,bcm2711-hdmi-i2c`) exist. |
| SPI | **Not enabled.** No `/dev/spidev*` nodes. `spi@7e204000` (SPI0) status = `disabled`. |
| GPIO | Available: `/dev/gpiochip0`, `/dev/gpiochip1`, `/dev/gpiochip4`, `/dev/gpiomem` |
| UART | Not assessed (not required for v1.0) |

### `config.txt` state (relevant lines)

```text
#dtparam=i2c_arm=on      # commented out -> I2C1 disabled
#dtparam=i2s=on
#dtparam=spi=on          # commented out -> SPI0 disabled
dtparam=audio=on
dtoverlay=vc4-kms-v3d
max_framebuffers=2
disable_fw_kms_setup=1
arm_64bit=1
disable_overscan=1
arm_boost=1
[cm4]
otg_mode=1
[cm5]
dtoverlay=dwc2,dr_mode=host
[pi5]
dtoverlay=nospi10
[all]
```

**Conclusion:** I2C and SPI must be enabled in `/boot/firmware/config.txt`
before any sensor or display work (Phase 03 / STG-009). This contradicts the
plan's "previously verified" baseline note — the buses are currently off.

## 5. Power, thermal, throttling

| Check | Value |
|---|---|
| `vcgencmd get_throttled` | `throttled=0x0` (no undervoltage, no throttling, no historical flags) |
| `vcgencmd measure_temp` | ~49–50 °C at idle |
| `get_arm_voltage` | Not registered on this firmware (`Command not registered`) |
| Undervoltage in `dmesg` | None found |
| Fan | **Not present / disconnected.** Plan notes the Pi reportedly boots only with fan disconnected — open blocker (STG-004). |

## 6. User and permissions

| Item | Value |
|---|---|
| User | `ravik` (uid 1000) |
| Supplementary groups | `adm, dialout, cdrom, sudo, audio, video, plugdev, games, users, netdev, gpio(986), i2c(988), spi(989), render, input, lpadmin` |
| Device access | User already in `i2c`, `spi`, `gpio` groups — good; no root needed for bus access once nodes exist. |
| `sudo` | Password required (no passwordless sudo). |

## 7. Installed tooling (relevant)

| Tool | State |
|---|---|
| `python3` | 3.13.5 |
| `uv` | 0.13.9 (installed during Phase 02, `~/.local/bin/uv`) |
| `git` | present |
| `i2c-tools` | 4.4-2 (binaries in `/usr/sbin`, e.g. `/usr/sbin/i2cdetect`) |
| `spi-tools` | 0.8.4-1+b1 |
| `gpiod` | 2.2.1-2+deb13u1 |
| `python3-libgpiod` | 2.2.1 |
| `jq`, `tmux`, `sqlite3` | present |
| `vcgencmd` | present |

## 8. systemd

| Item | Value |
|---|---|
| Failed units | None |
| Uptime at capture | ~4 h 44 m |
| Application services | None installed yet |

## 9. As-built update (2026-11-27)

The snapshot above is the **baseline at capture time**, not the current state.
Items that have since changed on the running Pi:

| Area | Baseline (2026-11-24) | As built (2026-11-27) |
|---|---|---|
| I²C | disabled, no `/dev/i2c-*` | enabled: `dtparam=i2c_arm=on`, `i2c-dev` loaded, `/dev/i2c-1` present |
| SPI | disabled, no `/dev/spidev*` | enabled: `dtparam=spi=on`, `/dev/spidev0.0` and `/dev/spidev0.1` present |
| Sensors | not connected at capture | SHT45 `0x44`, SCD4X `0x62` (SCD41), SGP41 `0x59`, SPS30 `0x69`, all on bus 1 |
| Display | not assessed | ILI9341 on SPI0 CE0; DC BCM25 (pin 22), RESET BCM24 (pin 18) |
| Services | none installed | `stingray.service`, `stingray-hourly-summary.timer/.service`, Ollama |
| Storage | SD card only | SSD mounted at `/mnt/stingray` for Parquet and summaries |

The fan question is unchanged: no fan is attached, and the system is operated
without one. See [`wiring.md`](wiring.md) for the connection map and
[`BUILD_GUIDE.md`](../BUILD_GUIDE.md) for the current build procedure.

## 10. Open hardware questions (must be resolved before Phase 03/05)

- [ ] Fan power/boot issue (STG-004) — unresolved; no fan currently attached.
- [ ] DFR0918 breakout revision and terminal assignments — needs physical confirmation.
- [ ] Exact SPS30 interface mode (I2C vs UART) on the board in hand.
- [ ] ILI9341 breakout board revision and control-pin (DC/RESET/CS) mapping.
- [ ] Pull-up ownership on the I2C bus (Pi internal vs module).
