"""Backfill 30 days of mock sensor data into Parquet storage.

Generates realistic mock data matching the production cadence:
  - sht45: 5s (temp + rh)
  - scd4x: 5s (co2 + temp + rh)
  - sgp41: 1s (voc_raw + nox_raw + voc_index + nox_index)
  - sps30: 10s (pm1 + pm25 + pm4 + pm10 + 4x num)

Builds PyArrow tables directly (no pydantic) for speed.

Usage:
    python scripts/backfill.py --days 30 --output /mnt/stingray/parquet
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger("backfill")

# Production cadence (seconds)
CADENCE = {
    "sht45": 5.0,
    "scd4x": 5.0,
    "sgp41": 1.0,
    "sps30": 10.0,
}

SCHEMA = pa.schema(
    [
        pa.field("sensor", pa.string()),
        pa.field("metric", pa.string()),
        pa.field("value", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("observed_at", pa.string()),
        pa.field("received_at", pa.string()),
        pa.field("quality", pa.string()),
        pa.field("diagnostics", pa.string()),
        pa.field("observed_epoch", pa.float64()),
    ]
)


def _gen_day(
    rng: random.Random,
    day_start: datetime,
    day_end: datetime,
    states: dict[str, dict],
) -> pa.Table:
    """Generate all measurements for one day as a PyArrow table."""
    sensors: list[str] = []
    metrics: list[str] = []
    values: list[float] = []
    units: list[str] = []
    observed_ats: list[str] = []
    received_ats: list[str] = []
    qualities: list[str] = []
    diagnostics: list[str] = []
    epochs: list[float] = []

    for sid, cadence in CADENCE.items():
        state = states[sid]
        t = day_start
        while t < day_end:
            iso = t.isoformat()
            epoch = t.timestamp()
            if sid == "sht45":
                state["temp"] = max(-40.0, min(125.0, state["temp"] + rng.uniform(-0.05, 0.05)))
                state["rh"] = max(0.0, min(100.0, state["rh"] + rng.uniform(-0.2, 0.2)))
                sensors.extend(["sht45", "sht45"])
                metrics.extend(["temperature_c", "relative_humidity_percent"])
                values.extend([round(state["temp"], 2), round(state["rh"], 2)])
                units.extend(["degC", "%"])
                observed_ats.extend([iso, iso])
                received_ats.extend([iso, iso])
                qualities.extend(["good", "good"])
                diagnostics.extend(["{}", "{}"])
                epochs.extend([epoch, epoch])
            elif sid == "scd4x":
                state["co2"] = max(0.0, state["co2"] + rng.uniform(-2.0, 2.0))
                state["temp"] = max(-40.0, min(125.0, state["temp"] + rng.uniform(-0.05, 0.05)))
                state["rh"] = max(0.0, min(100.0, state["rh"] + rng.uniform(-0.2, 0.2)))
                sensors.extend(["scd4x", "scd4x", "scd4x"])
                metrics.extend(["co2_ppm", "temperature_c", "relative_humidity_percent"])
                values.extend([round(state["co2"], 1), round(state["temp"], 2), round(state["rh"], 2)])
                units.extend(["ppm", "degC", "%"])
                observed_ats.extend([iso, iso, iso])
                received_ats.extend([iso, iso, iso])
                qualities.extend(["good", "good", "good"])
                diagnostics.extend(["{}", "{}", "{}"])
                epochs.extend([epoch, epoch, epoch])
            elif sid == "sgp41":
                state["voc_raw"] = max(0.0, state["voc_raw"] + rng.uniform(-1.0, 1.0))
                state["nox_raw"] = max(0.0, state["nox_raw"] + rng.uniform(-0.5, 0.5))
                voc_idx = max(0.0, min(500.0, state["voc_raw"] / 4.0))
                nox_idx = max(0.0, min(500.0, state["nox_raw"] / 2.0))
                sensors.extend(["sgp41", "sgp41", "sgp41", "sgp41"])
                metrics.extend(["voc_raw", "nox_raw", "voc_index", "nox_index"])
                values.extend([round(state["voc_raw"], 1), round(state["nox_raw"], 1), round(voc_idx, 1), round(nox_idx, 1)])
                units.extend(["raw", "raw", "index", "index"])
                observed_ats.extend([iso, iso, iso, iso])
                received_ats.extend([iso, iso, iso, iso])
                qualities.extend(["good", "good", "good", "good"])
                diagnostics.extend(["{}", "{}", "{}", "{}"])
                epochs.extend([epoch, epoch, epoch, epoch])
            elif sid == "sps30":
                state["pm25"] = max(0.0, state["pm25"] + rng.uniform(-0.5, 0.5))
                state["pm1"] = max(0.0, state["pm1"] + rng.uniform(-0.2, 0.2))
                state["pm4"] = max(0.0, state["pm4"] + rng.uniform(-0.3, 0.3))
                state["pm10"] = max(0.0, state["pm10"] + rng.uniform(-0.4, 0.4))
                sensors.extend(["sps30"] * 8)
                metrics.extend([
                    "pm1_ug_m3", "pm2_5_ug_m3", "pm4_ug_m3", "pm10_ug_m3",
                    "pm1_num_m3", "pm2_5_num_m3", "pm4_num_m3", "pm10_num_m3",
                ])
                values.extend([
                    round(state["pm1"], 2), round(state["pm25"], 2),
                    round(state["pm4"], 2), round(state["pm10"], 2),
                    round(state["pm1"] * 100, 0), round(state["pm25"] * 100, 0),
                    round(state["pm4"] * 100, 0), round(state["pm10"] * 100, 0),
                ])
                units.extend(["ug/m3", "ug/m3", "ug/m3", "ug/m3", "1/m3", "1/m3", "1/m3", "1/m3"])
                observed_ats.extend([iso] * 8)
                received_ats.extend([iso] * 8)
                qualities.extend(["good"] * 8)
                diagnostics.extend(["{}"] * 8)
                epochs.extend([epoch] * 8)
            t += timedelta(seconds=cadence)

    return pa.table(
        {
            "sensor": sensors,
            "metric": metrics,
            "value": values,
            "unit": units,
            "observed_at": observed_ats,
            "received_at": received_ats,
            "quality": qualities,
            "diagnostics": diagnostics,
            "observed_epoch": epochs,
        },
        schema=SCHEMA,
    )


def backfill(output_dir: str | Path, days: int, seed: int = 42) -> int:
    """Generate `days` days of mock data. Returns total measurements written."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    states = {
        "sht45": {"temp": 25.0, "rh": 50.0},
        "scd4x": {"co2": 450.0, "temp": 25.0, "rh": 50.0},
        "sgp41": {"voc_raw": 200.0, "nox_raw": 100.0},
        "sps30": {"pm25": 12.0, "pm1": 6.0, "pm4": 14.4, "pm10": 18.0},
    }

    now = datetime.now(UTC)
    start = now - timedelta(days=days)

    total = 0
    chunk_counter = 0
    t = start.replace(hour=0, minute=0, second=0, microsecond=0)

    while t < now:
        day_end = t + timedelta(days=1)
        if day_end > now:
            day_end = now

        table = _gen_day(rng, t, day_end, states)
        n = table.num_rows
        total += n

        # Write to Hive-partitioned path
        d = t.astimezone(UTC)
        day_dir = output_dir / f"year={d.year:04d}" / f"month={d.month:02d}" / f"day={d.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)
        chunk_counter += 1
        final_path = day_dir / f"chunk_{chunk_counter:010d}.parquet"
        tmp_path = day_dir / f"{final_path.name}.tmp"
        pq.write_table(table, tmp_path, compression="zstd")
        tmp_path.rename(final_path)

        log.info("day %02d-%02d-%02d: %d rows, total=%d", d.month, d.day, d.year, n, total)
        t = day_end

    log.info("backfill complete: %d measurements written", total)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill mock sensor data")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backfill")
    parser.add_argument("--output", type=str, default="/tmp/stingray-backfill", help="Output directory")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start = time.monotonic()
    total = backfill(args.output, args.days, args.seed)
    elapsed = time.monotonic() - start
    log.info("done in %.1f seconds", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
