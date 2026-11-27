#!/usr/bin/env python3
"""Standalone 1 Hz validation for the combined SHT4x + SGP41 module.

Run on the Raspberry Pi with the deployed release virtualenv. This diagnostic
is not bundled in the release tarball; copy it from GitHub to a temporary path
on the Pi first, and stop stingray.service before running it::

    /opt/stingray/current/.venv/bin/python /tmp/validate_gas_sensors.py

Add ``--seconds 1800`` for a timed 30-minute run. Ctrl-C exits cleanly.

The script deliberately bypasses the service, scheduler, storage and dashboard
so the two sensors on the shared I2C bus can be validated in isolation. SHT4x
is the authoritative temperature/humidity source for SGP41 compensation.

Gas indices are intentionally shown as ``WARM`` during the Sensirion algorithm
initial-learning period instead of as the sentinel value ``0``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running as ``python scripts/validate_gas_sensors.py`` without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stingray.core.models import METRIC_UNITS, MetricId  # noqa: E402
from stingray.sensors.sgp41 import CONDITIONING_CYCLES, SELF_TEST_PASS, SGP41  # noqa: E402
from stingray.sensors.sht45 import SHT45  # noqa: E402


def _fmt(value: float | None, digits: int = 2) -> str:
    return "--" if value is None else f"{value:.{digits}f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="Stop after this many seconds (0 = run until Ctrl-C).",
    )
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1).")
    args = parser.parse_args(argv)

    sht = SHT45(bus_number=args.bus)
    # One shared holder: the script samples SHT4x once per second and the
    # SGP41 driver consumes that same value for compensation.
    compensation = {"temperature_c": 25.0, "rh_percent": 50.0}
    sgp = SGP41(
        bus_number=args.bus,
        temperature_provider=lambda: (
            compensation["temperature_c"],
            compensation["rh_percent"],
        ),
    )

    sht_online = False
    sgp_online = False
    sht_serial: int | None = None
    sgp_serial: int | None = None
    self_test: int | None = None

    try:
        sht.initialize()
        sht_serial = sht.serial_number
        sht_online = sht.health().state.value == "ready" and sht_serial is not None

        sgp.initialize()
        sgp_serial = sgp.serial_number
        try:
            self_test = sgp.execute_self_test()
        except Exception:  # noqa: BLE001 - diagnostic only
            self_test = None
        sgp_online = (
            sgp.health().state.value != "disconnected"
            and sgp_serial is not None
            and self_test == SELF_TEST_PASS
        )

        print("=== Stingray SHT4x + SGP41 live validation (1 Hz) ===")
        print(
            f"SHT4x: {'ONLINE' if sht_online else 'OFFLINE'}"
            + (f"  serial=0x{sht_serial:08X}" if sht_serial else "")
            + f"  bus={args.bus} addr=0x{sht.address:02X}"
        )
        print(
            f"SGP41: {'ONLINE' if sgp_online else 'OFFLINE'}"
            + (f"  serial=0x{sgp_serial:012X}" if sgp_serial else "")
            + (f"  self-test=0x{self_test:04X}" if self_test is not None else "")
            + f"  bus={args.bus} addr=0x{sgp.address:02X}"
        )
        print(f"Compensation source: {sgp.compensation_source}")
        print("-" * 72)
        sys.stdout.flush()

        last_raw: dict[MetricId, float | None] = {
            MetricId.VOC_RAW: None,
            MetricId.NOX_RAW: None,
        }
        latest_indices: dict[MetricId, float | None] = {
            MetricId.VOC_INDEX: None,
            MetricId.NOX_INDEX: None,
        }

        start = time.monotonic()
        next_due = start
        cycle = 0
        while True:
            now = time.monotonic()
            if args.seconds > 0 and now - start >= args.seconds:
                break
            # Sleep in small slices so Ctrl-C stays responsive and the
            # acquisition cadence stays near exactly 1 second.
            if next_due > now:
                time.sleep(min(next_due - now, 0.25))
                continue
            next_due = max(next_due + 1.0, now + 1.0)
            cycle += 1

            sht_error: str | None = None
            try:
                temperature_c, rh_percent = sht.sample()
                compensation["temperature_c"] = temperature_c
                compensation["rh_percent"] = rh_percent
            except Exception as exc:  # noqa: BLE001 - report, do not abort
                sht_error = f"{type(exc).__name__}: {exc}"

            sgp_error: str | None = None
            try:
                for measurement in sgp.read():
                    if measurement.metric in last_raw:
                        last_raw[measurement.metric] = measurement.value
                    elif measurement.metric in latest_indices:
                        latest_indices[measurement.metric] = measurement.value
            except Exception as exc:  # noqa: BLE001 - report, do not abort
                sgp_error = f"{type(exc).__name__}: {exc}"

            sht_online = sht_error is None
            if sgp_error is None and sgp.health().state.value != "disconnected":
                sgp_online = True
            else:
                sgp_online = False

            if sgp.in_warmup:
                status = f"CONDITIONING ({sgp.conditioning_cycles_done}/{CONDITIONING_CYCLES})"
            elif not sgp.gas_indices_ready:
                status = "WARM (gas index learning)"
            else:
                status = "READY"

            if sgp_error:
                status += f" | SGP41 error: {sgp_error}"
            if sht_error:
                status += f" | SHT4x error: {sht_error}"

            voc_index = latest_indices[MetricId.VOC_INDEX] if sgp.gas_indices_ready else None
            nox_index = latest_indices[MetricId.NOX_INDEX] if sgp.gas_indices_ready else None
            print(
                f"[{cycle:05d}] SHT4x: {'ONLINE' if sht_online else 'OFFLINE'}"
                f"   SGP41: {'ONLINE' if sgp_online else 'OFFLINE'}"
            )
            print(
                f"         T: {_fmt(temperature_c)} {METRIC_UNITS[MetricId.TEMPERATURE_C]}"
                f"   RH: {_fmt(rh_percent)} {METRIC_UNITS[MetricId.RELATIVE_HUMIDITY_PERCENT]}"
            )
            print(
                f"         VOC Raw: {_fmt(last_raw[MetricId.VOC_RAW], 0)}"
                f"   NOx Raw: {_fmt(last_raw[MetricId.NOX_RAW], 0)}"
            )
            print(
                f"         VOC Index: {_fmt(voc_index, 0) if voc_index is not None else 'WARM'}"
                f"   NOx Index: {_fmt(nox_index, 0) if nox_index is not None else 'WARM'}"
            )
            print(f"         Compensation source: {sgp.compensation_source}")
            print(f"         Status: {status}")
            sys.stdout.flush()
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted; closing sensors.", file=sys.stderr)
        return 130
    finally:
        sgp.close()
        sht.close()


if __name__ == "__main__":
    raise SystemExit(main())
