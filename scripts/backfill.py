"""Backfill 30 days of mock sensor data into Parquet storage.

Generates realistic mock data matching the production cadence:
  - sht45: 5s (temp + rh)
  - scd4x: 5s (co2 + temp + rh)
  - sgp41: 1s (voc_raw + nox_raw + voc_index + nox_index)
  - sps30: 10s (pm1 + pm25 + pm4 + pm10 + 4x num)

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

from stingray.core.models import Measurement, MetricId, SensorId
from stingray.storage.parquet_store import ParquetWriter

log = logging.getLogger("backfill")

# Production cadence (seconds)
CADENCE = {
    SensorId.SHT45: 5.0,
    SensorId.SCD4X: 5.0,
    SensorId.SGP41: 1.0,
    SensorId.SPS30: 10.0,
}


def _gen_sht45(rng: random.Random, t: datetime, state: dict) -> list[Measurement]:
    state["temp"] = max(-40.0, min(125.0, state["temp"] + rng.uniform(-0.05, 0.05)))
    state["rh"] = max(0.0, min(100.0, state["rh"] + rng.uniform(-0.2, 0.2)))
    return [
        Measurement(
            sensor=SensorId.SHT45,
            metric=MetricId.TEMPERATURE_C,
            value=round(state["temp"], 2),
            unit="degC",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SHT45,
            metric=MetricId.RELATIVE_HUMIDITY_PERCENT,
            value=round(state["rh"], 2),
            unit="%",
            observed_at=t,
            received_at=t,
        ),
    ]


def _gen_scd4x(rng: random.Random, t: datetime, state: dict) -> list[Measurement]:
    state["co2"] = max(0.0, state["co2"] + rng.uniform(-2.0, 2.0))
    state["temp"] = max(-40.0, min(125.0, state["temp"] + rng.uniform(-0.05, 0.05)))
    state["rh"] = max(0.0, min(100.0, state["rh"] + rng.uniform(-0.2, 0.2)))
    return [
        Measurement(
            sensor=SensorId.SCD4X,
            metric=MetricId.CO2_PPM,
            value=round(state["co2"], 1),
            unit="ppm",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SCD4X,
            metric=MetricId.TEMPERATURE_C,
            value=round(state["temp"], 2),
            unit="degC",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SCD4X,
            metric=MetricId.RELATIVE_HUMIDITY_PERCENT,
            value=round(state["rh"], 2),
            unit="%",
            observed_at=t,
            received_at=t,
        ),
    ]


def _gen_sgp41(rng: random.Random, t: datetime, state: dict) -> list[Measurement]:
    state["voc_raw"] = max(0.0, state["voc_raw"] + rng.uniform(-1.0, 1.0))
    state["nox_raw"] = max(0.0, state["nox_raw"] + rng.uniform(-0.5, 0.5))
    voc_idx = max(0.0, min(500.0, state["voc_raw"] / 4.0))
    nox_idx = max(0.0, min(500.0, state["nox_raw"] / 2.0))
    return [
        Measurement(
            sensor=SensorId.SGP41,
            metric=MetricId.VOC_RAW,
            value=round(state["voc_raw"], 1),
            unit="raw",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SGP41,
            metric=MetricId.NOX_RAW,
            value=round(state["nox_raw"], 1),
            unit="raw",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SGP41,
            metric=MetricId.VOC_INDEX,
            value=round(voc_idx, 1),
            unit="index",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SGP41,
            metric=MetricId.NOX_INDEX,
            value=round(nox_idx, 1),
            unit="index",
            observed_at=t,
            received_at=t,
        ),
    ]


def _gen_sps30(rng: random.Random, t: datetime, state: dict) -> list[Measurement]:
    state["pm25"] = max(0.0, state["pm25"] + rng.uniform(-0.5, 0.5))
    state["pm1"] = max(0.0, state["pm1"] + rng.uniform(-0.2, 0.2))
    state["pm4"] = max(0.0, state["pm4"] + rng.uniform(-0.3, 0.3))
    state["pm10"] = max(0.0, state["pm10"] + rng.uniform(-0.4, 0.4))
    return [
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM1_UG_M3,
            value=round(state["pm1"], 2),
            unit="ug/m3",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM2_5_UG_M3,
            value=round(state["pm25"], 2),
            unit="ug/m3",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM4_UG_M3,
            value=round(state["pm4"], 2),
            unit="ug/m3",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM10_UG_M3,
            value=round(state["pm10"], 2),
            unit="ug/m3",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM1_NUM_M3,
            value=round(state["pm1"] * 100, 0),
            unit="1/m3",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM2_5_NUM_M3,
            value=round(state["pm25"] * 100, 0),
            unit="1/m3",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM4_NUM_M3,
            value=round(state["pm4"] * 100, 0),
            unit="1/m3",
            observed_at=t,
            received_at=t,
        ),
        Measurement(
            sensor=SensorId.SPS30,
            metric=MetricId.PM10_NUM_M3,
            value=round(state["pm10"] * 100, 0),
            unit="1/m3",
            observed_at=t,
            received_at=t,
        ),
    ]


GENERATORS = {
    SensorId.SHT45: _gen_sht45,
    SensorId.SCD4X: _gen_scd4x,
    SensorId.SGP41: _gen_sgp41,
    SensorId.SPS30: _gen_sps30,
}


def backfill(output_dir: str | Path, days: int, seed: int = 42) -> int:
    """Generate `days` days of mock data. Returns total measurements written."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    writer = ParquetWriter(output_dir, batch_size=5000)

    # Initial state for each sensor
    states = {
        SensorId.SHT45: {"temp": 25.0, "rh": 50.0},
        SensorId.SCD4X: {"co2": 450.0, "temp": 25.0, "rh": 50.0},
        SensorId.SGP41: {"voc_raw": 200.0, "nox_raw": 100.0},
        SensorId.SPS30: {"pm25": 12.0, "pm1": 6.0, "pm4": 14.4, "pm10": 18.0},
    }

    now = datetime.now(UTC)
    start = now - timedelta(days=days)

    total = 0
    t = start
    day = 0
    while t < now:
        day += 1
        day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        if day_end > now:
            day_end = now

        # Process one day at a time
        t = day_start
        while t < day_end:
            batch: list[Measurement] = []
            for sid, cadence in CADENCE.items():
                # Check if this sensor fires at time t
                # We use a simple approach: for each sensor, generate readings
                # at its cadence interval starting from day_start
                pass

            # Better approach: for each sensor, generate all readings in this day
            for sid, cadence in CADENCE.items():
                gen = GENERATORS[sid]
                t_sensor = day_start
                while t_sensor < day_end:
                    batch.extend(gen(rng, t_sensor, states[sid]))
                    t_sensor += timedelta(seconds=cadence)

            writer.add_many(batch)
            total += len(batch)

        if day % 5 == 0:
            log.info("day %d/%d done, total=%d", day, days, total)

    flushed = writer.flush()
    total += flushed
    log.info("backfill complete: %d measurements written", total)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill mock sensor data")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backfill")
    parser.add_argument("--output", type=str, default="/mnt/stingray/parquet", help="Output directory")
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
