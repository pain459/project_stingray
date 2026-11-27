#!/usr/bin/env python3
"""Test the V1.8 dashboard with synthetic sensor data.

Run this on the Pi to validate the display layout and rendering.
"""

import time
import sys
sys.path.insert(0, '/opt/stingray/current/src')

from stingray.display import ILI9341Driver, DisplayDashboard, SensorSnapshot


def main():
    # Initialize display
    print("Initializing display...")
    driver = ILI9341Driver()
    driver.open()
    
    # Create dashboard
    dashboard = DisplayDashboard(driver, hostname="stingray")
    dashboard.set_thresholds(pm25=35.0, pm10=55.0)
    
    # Test 1: All sensors online with good values
    print("Test 1: All sensors online, good values...")
    snapshot = SensorSnapshot(
        timestamp=time.time(),
        pm1=5.2,
        pm25=12.5,
        pm4=15.3,
        pm10=18.7,
        co2=450,
        voc=45,
        nox=0.5,
        temperature=25.3,
        humidity=55,
        tps=101.32,
        sensor_online=True,
        sensor_warming=False,
    )
    dashboard.render(snapshot)
    time.sleep(5)
    
    # Test 2: PM2.5 warning level
    print("Test 2: PM2.5 warning level...")
    snapshot = SensorSnapshot(
        timestamp=time.time(),
        pm1=20.1,
        pm25=30.2,  # 75% of 35 = 26.25, so this is warning
        pm4=25.5,
        pm10=40.3,
        co2=450,
        voc=45,
        nox=0.5,
        temperature=25.3,
        humidity=55,
        tps=101.32,
        sensor_online=True,
        sensor_warming=False,
    )
    dashboard.render(snapshot)
    time.sleep(5)
    
    # Test 3: PM2.5 bad level
    print("Test 3: PM2.5 bad level...")
    snapshot = SensorSnapshot(
        timestamp=time.time(),
        pm1=35.5,
        pm25=40.2,  # Above 35, so bad
        pm4=45.8,
        pm10=50.1,
        co2=450,
        voc=45,
        nox=0.5,
        temperature=25.3,
        humidity=55,
        tps=101.32,
        sensor_online=True,
        sensor_warming=False,
    )
    dashboard.render(snapshot)
    time.sleep(5)
    
    # Test 4: CO2 warning
    print("Test 4: CO2 warning level...")
    snapshot = SensorSnapshot(
        timestamp=time.time(),
        pm1=5.2,
        pm25=12.5,
        pm4=15.3,
        pm10=18.7,
        co2=900,  # Above 800, so warning
        voc=45,
        nox=0.5,
        temperature=25.3,
        humidity=55,
        tps=101.32,
        sensor_online=True,
        sensor_warming=False,
    )
    dashboard.render(snapshot)
    time.sleep(5)
    
    # Test 5: Sensor warming up
    print("Test 5: Sensor warming up...")
    snapshot = SensorSnapshot(
        timestamp=time.time(),
        pm1=None,
        pm25=None,
        pm4=None,
        pm10=None,
        co2=None,
        voc=None,
        nox=None,
        temperature=None,
        humidity=None,
        tps=None,
        sensor_online=True,
        sensor_warming=True,
    )
    dashboard.render(snapshot)
    time.sleep(5)
    
    # Test 6: Sensor offline
    print("Test 6: Sensor offline...")
    snapshot = SensorSnapshot(
        timestamp=time.time(),
        pm1=None,
        pm25=None,
        pm4=None,
        pm10=None,
        co2=None,
        voc=None,
        nox=None,
        temperature=None,
        humidity=None,
        tps=None,
        sensor_online=False,
        sensor_warming=False,
    )
    dashboard.render(snapshot)
    time.sleep(5)
    
    print("All tests complete!")
    driver.close()


if __name__ == "__main__":
    main()
