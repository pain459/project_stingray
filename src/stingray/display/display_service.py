"""Display service: bridges the sensor scheduler to the TFT dashboard.

Runs a dedicated thread that:
1. Reads the latest sensor data from the shared ``LatestReadings`` cache.
2. Builds a ``SensorSnapshot`` from the latest readings.
3. Renders the dashboard on a 1-second monotonic interval.

The display service does NOT drain the ``MeasurementQueue``. The storage
drain loop is the sole drainer and updates the shared cache. This avoids
queue contention between storage and display.

A display failure never interrupts sensor acquisition or other services.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from stingray.core.latest_readings import LatestReadings
from stingray.core.models import Measurement, MetricId, SensorId
from stingray.core.network import NetworkInfo
from stingray.core.scheduler import SensorScheduler

from .dashboard import DisplayDashboard, SensorSnapshot
from .driver import ILI9341Driver

log = logging.getLogger("stingray.display")


class DisplayService:
    """Bridges the sensor scheduler to the TFT display.

    The display service runs on its own thread with a 1-second refresh
    interval. It reads the latest sensor data from the shared
    ``LatestReadings`` cache and renders it to the display.

    A display failure (SPI error, driver error) is logged but never
    propagates to the main service.
    """

    def __init__(
        self,
        scheduler: SensorScheduler,
        latest: LatestReadings,
        hostname: str = "stingray",
        pm25_threshold: float = 35.0,
        pm10_threshold: float = 55.0,
        refresh_interval: float = 1.0,
        network: NetworkInfo | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._latest = latest
        self._hostname = hostname
        self._network = network
        self._refresh_interval = refresh_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Display driver and dashboard (created in start())
        self._driver: ILI9341Driver | None = None
        self._dashboard: DisplayDashboard | None = None

        # Thresholds
        self._pm25_threshold = pm25_threshold
        self._pm10_threshold = pm10_threshold

    def start(self) -> bool:
        """Initialize the display and start the refresh thread.

        Returns:
            True if the display initialized successfully, False otherwise.
            A display failure does not prevent the rest of the service from
            running.
        """
        try:
            self._driver = ILI9341Driver()
            self._driver.open()
            self._dashboard = DisplayDashboard(self._driver, hostname=self._hostname)
            self._dashboard.set_thresholds(self._pm25_threshold, self._pm10_threshold)
            log.info("display initialized")
        except Exception as exc:
            log.warning("display initialization failed: %s", exc)
            self._driver = None
            self._dashboard = None
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="stingray-display", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the display thread and close the driver."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None

    def _run(self) -> None:
        """Main display refresh loop."""
        # Full redraw on startup
        if self._driver is not None:
            try:
                self._driver.fill_screen(0x0000)
            except Exception as exc:
                log.warning("display full redraw failed: %s", exc)
                return

        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            try:
                # Build snapshot and render
                snapshot = self._build_snapshot()
                if self._dashboard is not None:
                    self._dashboard.render(snapshot)
            except Exception as exc:
                log.warning("display refresh error: %s", exc)

            # Wait until the next scheduled refresh
            next_tick += self._refresh_interval
            now = time.monotonic()
            if now < next_tick:
                self._stop_event.wait(timeout=next_tick - now)
            else:
                # We're behind schedule; reset the tick
                next_tick = time.monotonic()

    def _build_snapshot(self) -> SensorSnapshot:
        """Build a SensorSnapshot from the shared LatestReadings cache."""
        cache = self._latest.snapshot()

        def get(sensor: SensorId, metric: MetricId) -> Optional[float]:
            m = cache.get((sensor, metric))
            return m.value if m is not None else None

        # SPS30 metrics
        pm1 = get(SensorId.SPS30, MetricId.PM1_UG_M3)
        pm25 = get(SensorId.SPS30, MetricId.PM2_5_UG_M3)
        pm4 = get(SensorId.SPS30, MetricId.PM4_UG_M3)
        pm10 = get(SensorId.SPS30, MetricId.PM10_UG_M3)
        tps = get(SensorId.SPS30, MetricId.TPS_UM)

        # SCD4X metrics
        co2 = get(SensorId.SCD4X, MetricId.CO2_PPM)
        scd4x_temp = get(SensorId.SCD4X, MetricId.TEMPERATURE_C)
        scd4x_hum = get(SensorId.SCD4X, MetricId.RELATIVE_HUMIDITY_PERCENT)

        # SHT45 metrics (authoritative for temp/humidity)
        sht45_temp = get(SensorId.SHT45, MetricId.TEMPERATURE_C)
        sht45_hum = get(SensorId.SHT45, MetricId.RELATIVE_HUMIDITY_PERCENT)

        # SGP41 metrics
        voc = get(SensorId.SGP41, MetricId.VOC_INDEX)
        nox = get(SensorId.SGP41, MetricId.NOX_INDEX)

        # Temperature and humidity: SHT45 is authoritative
        temperature = sht45_temp if sht45_temp is not None else scd4x_temp
        humidity = sht45_hum if sht45_hum is not None else scd4x_hum

        # Sensor health
        health = self._scheduler.health()
        sps30_online = health.get(SensorId.SPS30) is not None and health[SensorId.SPS30].state.value == "ready"
        scd4x_online = health.get(SensorId.SCD4X) is not None and health[SensorId.SCD4X].state.value == "ready"
        sht45_online = health.get(SensorId.SHT45) is not None and health[SensorId.SHT45].state.value == "ready"
        sgp41_online = health.get(SensorId.SGP41) is not None and health[SensorId.SGP41].state.value == "ready"

        # Overall sensor online: at least one sensor is ready
        any_online = sps30_online or scd4x_online or sht45_online or sgp41_online

        # Warming: SCD4X is online but no CO2 reading yet
        scd4x_warming = scd4x_online and co2 is None

        # Timestamp: use the most recent measurement timestamp
        timestamp = 0.0
        if cache:
            max_observed = max(m.observed_at for m in cache.values())
            timestamp = max_observed.timestamp()

        return SensorSnapshot(
            timestamp=timestamp,
            pm1=pm1,
            pm25=pm25,
            pm4=pm4,
            pm10=pm10,
            co2=co2,
            voc=voc,
            nox=nox,
            temperature=temperature,
            humidity=humidity,
            tps=tps,
            sensor_online=any_online,
            sensor_warming=scd4x_warming,
            wifi_rssi=self._network.wifi_rssi if self._network is not None else None,
        )
