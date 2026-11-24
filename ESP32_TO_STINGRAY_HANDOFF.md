# ESP32-S3 Air Quality Monitor → project_stingray

**Handoff baseline:** ESP32-S3 Air Quality Monitor V1.8 (September 2026 prototype closeout)  
**Target:** Raspberry Pi 4 Model B Rev 1.1, host `stingray`, Debian 13 ARM64, SD-card-only, SSH-managed.  
**Purpose:** Preserve confirmed physical/electrical decisions and application behavior while reimplementing on Raspberry Pi. This is an evidence-backed migration handoff, **not** a claim that the full V1.8 firmware source has been recovered.

## 1. Provenance and artifact inventory

| Artifact | Status | Use |
|---|---|---|
| `air_quality_monitor_v1_8_project_close.md` | Recovered in ChatGPT Library; original bytes not exportable in this session | Source of frozen hardware, software and PCB priorities |
| `firmware_v1_8_notes.md` | Recovered in ChatGPT Library; original bytes not exportable in this session | Display/layout specification and build command |
| `upgrade_v1_7_2_to_v1_8.py` | Recovered in ChatGPT Library; original bytes not exportable in this session | **Actual V1.8 display transformation source** |
| `air_quality_monitor_v1_7_2_gas_persistence.ino` | **Not recovered** | Required input for V1.8 upgrade script; needed to reconstruct exact firmware |
| `air_quality_monitor_v1_8_static_dashboard.ino` | **Not recovered** | Expected generated V1.8 firmware output; do not claim it exists |
| `air_quality_monitor_v1_8_wiring_reference.md` | Referenced by closeout but **not recovered** | Verify connector/pin-level details against original |
| `sketch_aug29_v1.4.2.ino` | Older firmware available in Library | Historical reference only; not replacement for V1.8 |

**Original generation command (requires the missing V1.7.2 file):**

```bash
python3 upgrade_v1_7_2_to_v1_8.py \
  air_quality_monitor_v1_7_2_gas_persistence.ino
# Creates air_quality_monitor_v1_8_static_dashboard.ino
```

Do not use the old V1.4.x OLED firmware as the final specification. Do not assume the V1.8 transformation preserves behavior until the generated result can be compared with source.

## 2. Frozen functional architecture

| Subsystem | ESP32 final source of truth | Measurements/role | Stingray preservation |
|---|---|---|---|
| Particles | Sensirion **SPS30** | PM1, PM2.5, PM4, PM10 mass concentration; typical particle size (TPS); particle-number outputs existed in earlier API implementations | Retain sensor and full raw readings; preserve PM values and TPS on UI/API |
| CO₂ | Sensirion **SCD4X** | CO₂ ppm primary; SCD4X temperature/RH diagnostic only | Never substitute SCD4X temperature/RH for room T/RH |
| Room environment | **SHT45** (7Semi SHT45+SGP41 board) | **Authoritative** temperature and humidity | Use for UI, API and SGP41 compensation |
| Gas indices | **SGP41** | VOC Index and NOx Index using SHT45 compensation | Preserve algorithm state/warmup and persistent trend semantics |
| Display | **ILI9341 2.8-inch SPI TFT, 320×240** | One static dashboard | No auto-rotating carousel |
| System | ESP32-S3 + Wi-Fi | Embedded HTTP, mDNS, NTP, Telegram | Replace ESP32-specific APIs and system diagnostics with Pi equivalents |

The **SSD1306 0.96-inch OLED** was used in earlier firmware versions; it is **not** the final V1.8 display and should not displace the ILI9341.

## 3. Known-good ESP32 wiring (reference only — do not copy GPIO numbers to Pi)

### Shared I²C

- ESP32-S3 SDA: **GPIO8**; SCL: **GPIO9**.
- SHT45: **0x44**; 3.3 V.
- SGP41: **0x59**; 3.3 V.
- SCD4X: **0x62**; 3.3 V in the frozen prototype.
- SPS30: **0x69**; 5 V power; **SEL to GND** to enable I²C.
- Shared electrical ground; I²C pull-ups to **3.3 V**. Verify each board's existing pull-ups and voltage tolerance before repowering.

### ILI9341 SPI TFT

| TFT signal | ESP32-S3 GPIO |
|---|---:|
| CS | 10 |
| MOSI | 11 |
| SCK | 12 |
| MISO | 13 |
| DC | 14 |
| RST/RESET | 15 |

Prototype wiring reports indicate TFT VCC supplied from 5 V and backlight/LED from 3.3 V, **subject to verifying the exact TFT module markings and regulator**. Do not assume that the TFT's data or backlight are 5-V tolerant.

**Pi migration note:** Pi I²C `/dev/i2c-1` and SPI `/dev/spidev0.0`, `/dev/spidev0.1` were already enabled, with no throttling observed in the earlier validation. Assign Raspberry Pi GPIO/CE/DC/RST based on the Pi header and the installed DFRobot DFR0918 terminal-block HAT, not ESP32 GPIO indices. Validate the 3.3-V logic domain, grounds and each powered board before connecting.

## 4. V1.8 display acceptance contract

- Physical screen: ILI9341, landscape **320×240**.
- One **fixed screen**, no carousel. Periodic measurement tiles update in place; top clock changes on minute boundary.
- Header: weekday/date at left, **HH:MM** at right, no seconds or firmware branding.
- Full-width room-status bar: **GOOD / ELEVATED / POOR**, plus explicit starting/sensor-check states.
- Two prominent primary tiles: **PM2.5** (µg/m³) and **CO₂** (ppm).
- Compact secondary tiles: **PM1, PM4, PM10, VOC Index, NOx Index**.
- Bottom environmental tiles: **SHT45 temperature**, **SHT45 relative humidity**, **SPS30 TPS** (µm). Thermometer and droplet were drawn as native graphics.
- Footer in ESP32 version: `airmonitor.local`, Wi-Fi RSSI in dBm, seconds since update. Pi should keep equivalent service/reading health diagnostics, substituting host/network metrics as appropriate.
- Good/warning/poor status is encoded in green/yellow/red. The V1.8 patch classifies PM against its configured alert thresholds (warning at ≥75% of alert threshold, poor at ≥threshold) and CO₂ as GOOD <800 ppm, ELEVATED 800–1499 ppm, POOR ≥1500 ppm. These **display thresholds are not necessarily alert thresholds**; do not conflate the two.
- Display patch used `DISPLAY_REFRESH_INTERVAL 2000UL` and history sample interval `HISTORY_INTERVAL 30000UL`.
- Sensor availability and warmup must never be shown as fabricated numeric readings; show `OFF`, `WARM`, `--`, or corresponding clear states.

## 5. API, storage, time, alert and operation contract

| Capability | Frozen behavior / migration requirement |
|---|---|
| HTTP | Local web dashboard and JSON APIs `/api/status` and `/api/history` retained. Historical versions also exposed `/api/live`, `/api/system`, and `/api/history?after=<epoch>`; **verify exact V1.8 API schema/route compatibility from final firmware**. |
| History | 30-second sampling, **120 slots ≈60 minutes** of RAM measurement history. Avoid claiming a full schema until firmware is recovered. |
| Gas persistence | **7-day persistent VOC/NOx history/state**, stored with LittleFS in ESP32; map to appropriate SD-card storage and define write frequency for Pi. |
| Alerts | Telegram integration and particulate alerts are frozen features. Recover precise command list, thresholds, cooldown and anti-spam logic from final firmware; do not invent them. |
| Time | NTP-derived local date/time. Ensure timezone is explicitly set on Pi. |
| Discovery | Original `airmonitor.local` mDNS. Avoid colliding with Pi hostname `stingray.local`; decide and document compatibility alias. |
| Diagnostics | Wi-Fi/network state, reading age and per-sensor online/warmup indications; implement service health and restart behavior on Pi. |
| Secrets | Telegram bot token/chat ID, Wi-Fi credentials and any credentials belong in non-committed environment/secrets configuration. No credentials are included in this handoff. |

## 6. Mechanical design and power: confirmed vs proposed

### Confirmed at closeout

- Completed prototype used **breadboard wiring**, not a completed enclosure/PCB.
- Sensirion SPS30 has an **internal sampling fan**; its **air inlet and outlet must remain unobstructed**. Do not mistake it for a separate case fan.
- Keep the **SHT45 physically distant from MCU, regulators, display and other heat sources** to reduce temperature bias.
- Shared ground, 5 V and 3.3 V power domains, sensor connectors, service/test points, mounting holes and airflow were marked as required PCB/integration design priorities.
- The proposed Rev-1 carrier/baseboard approach was to **socket the ESP32-S3**, use connectors for SCD4X, SHT45/SGP41 and TFT, and cable the external SPS30. The objective was to replace breadboard jumpers, **not** redesign sensor electronics.

### Previously discussed, but not proven as built

- Mechanical proposal: **M3 screws, 10–12 mm nylon standoffs** on a reusable/perforated plate; SPS30 held by an appropriate bracket/clamp, without deforming its housing.
- Later GPIO-extender proposal: use **only the GPIO extender** for wiring and defer a custom PCB.
- Power proposal: regulated **7.5 V / 2 A**, center-positive 5.5×2.1 mm DC barrel into the *ESP32-specific extender*; **9 V / 2 A** considered acceptable if regulator heat is checked; **12 V rejected** for that extender. Downstream 5 V for SPS30 and TFT VCC, 3.3 V for SHT45/SGP41, and suitable voltage for SCD4X.
- These recommendations **must not be transferred as a power-input specification for the DFRobot DFR0918 Pi HAT**. Confirm HAT documentation and its power-distribution design independently.
- No final assembled enclosure, external case fan design, connector strain relief design or measured consumption verification was recovered for the ESP32 prototype.

### Separate decisions already made for project_stingray

- Raspberry Pi 4B with aluminum case and **two existing fans**; HAT mounted above using purchased spacers/elevations.
- **DFRobot DFR0918 terminal-block HAT** already purchased, and user wants independently powered external peripherals.
- **No SSD**; persistent files on SD card; SSH-only management.
- As of 24 Nov 2026, Raspberry Pi reportedly boots with case fan disconnected but fails to boot with fan connected. **Power/fan boot issue is unresolved** and must block powering additional peripherals until validated.
- Do not connect a Pi header's 3.3 V rail to externally powered 5 V I²C pull-ups; verify logic levels and use appropriate isolation/level-shifting where needed.

## 7. Implementation inheritance: what to port and what to replace

**Port the semantics:** sensor assignment and compensation, measurement units, warmup/errors, VOC/NOx algorithm state, status classification, API and history responses, alerts, static dashboard layout and alert semantics.

**Replace the implementation:** ESP32 Arduino `Wire`/`SPI`, `WebServer`, TFT rendering stack, Wi-Fi startup, `LittleFS`, timers, embedded TLS client and mDNS host startup with Pi-friendly libraries, system services and storage. Preserve observable behavior where practical.

**Do not silently port:** ESP32 pin numbers, ESP32 7.5/9-V extender input, unverified TFT LED voltage, old OLED carousel, undocumented API payload shapes, unverified alert numbers or unbuilt mechanical plans.

## 8. Recovery blockers (before final parity sign-off)

1. Recover `air_quality_monitor_v1_7_2_gas_persistence.ino` **or** the final generated `air_quality_monitor_v1_8_static_dashboard.ino` from the old workstation, archived conversation attachment, source repository or flashed build directory.
2. Recover `air_quality_monitor_v1_8_wiring_reference.md` to confirm connector-level wiring and polarity.
3. Inspect the real V1.8 source for exact JSON keys and schemas, Telegram commands, PM alert thresholds/cooldowns, sensor measurement periods, SGP41 conditioning and persistence format.
4. Validate exact ILI9341 breakout version, backlight electrical requirements, board supply limits and actual cable lengths.
5. Resolve Pi-with-fan boot fault and verify supply voltage, current headroom, boot logs and `vcgencmd get_throttled` before installing the HAT/sensors.
6. Validate physical air path, SHT45 thermal isolation, strain relief and mounting after assembly.

## 9. Suggested Stingray parity acceptance criteria

- AC-01: All four sensor ICs appear at expected I²C addresses without bus instability, with correct independent power, ground and safe 3.3-V I²C signaling.
- AC-02: PM1/2.5/4/10 and TPS sourced from SPS30; CO₂ sourced from SCD4X; room T/RH sourced only from SHT45; SGP41 compensation uses SHT45.
- AC-03: Missing, warming or failed sensors are represented as such in services, API and display; no stale reading is reported as fresh.
- AC-04: ILI9341 shows all frozen V1.8 fields on a static 320×240 layout; status colors and refresh semantics are preserved.
- AC-05: 120 history samples at 30-second interval are retrievable; 7-day gas persistence survives service and system restart under documented retention semantics.
- AC-06: Original API payloads and Telegram command/alert behaviors pass parity tests **once V1.8 source is recovered**; no unsupported schema assumptions are locked in beforehand.
- AC-07: Services recover on reboot and sensor disconnect/reconnect; power faults and bus errors produce actionable logs.
- AC-08: Enclosure/assembly permits SPS30 airflow; SHT45 is thermally isolated; cabling and HAT mounting are electrically and mechanically safe.
- AC-09: Pi boots reliably with case fans and proposed peripheral loading, with no undervoltage/throttling indication.

## 10. Current evidence grade

- **Verified in recovered closure materials:** V1.8 feature baseline, frozen sensors, display layout, selected GPIO contract, 60-minute and 7-day history claims.
- **Recovered historical decisions:** I²C addresses, sensor voltage assignments, TFT wiring, breadboard vs Rev-1 carrier, proposed standoffs and ESP32-extender power.
- **Unverified:** Complete V1.8 `.ino`, definitive API payloads, final thresholds/Telegram commands, exact physical build of extender/power proposal, completed enclosure/fan design, Pi-compatible power wiring.

**Rule for the Stingray repo:** mark each implementation requirement as `verified`, `historical decision`, `proposed`, or `requires source verification` until the corresponding hardware or firmware evidence is available.
