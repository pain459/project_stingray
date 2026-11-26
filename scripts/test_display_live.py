#!/usr/bin/env python3
"""Test the ILI9341 display with live sensor data from the API."""

import json
import sys
import time
import urllib.request

sys.path.insert(0, "/home/ravik/src_git/project_stingray/src")

from stingray.display import ILI9341Display


def fetch_live_data(api_url: str = "http://localhost:8081/api/live") -> dict:
    """Fetch live sensor data from the API."""
    try:
        with urllib.request.urlopen(api_url, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data
    except Exception as e:
        print(f"Failed to fetch live data: {e}")
        return {}


def main():
    print("Initializing display...")
    display = ILI9341Display()
    display.open()

    print("Display initialized. Fetching live sensor data...")

    try:
        while True:
            data = fetch_live_data()

            # Extract sensor values
            pm25 = None
            pm10 = None
            co2 = None
            temp = None
            humidity = None

            for reading in data.get("live", []):
                sensor = reading.get("sensor")
                metric = reading.get("metric")
                value = reading.get("value")

                if sensor == "sps30" and metric == "pm2_5_ug_m3":
                    pm25 = value
                elif sensor == "sps30" and metric == "pm10_ug_m3":
                    pm10 = value
                elif sensor == "scd4x" and metric == "co2_ppm":
                    co2 = value
                elif sensor == "scd4x" and metric == "temperature_c":
                    temp = value
                elif sensor == "scd4x" and metric == "relative_humidity_percent":
                    humidity = value

            print(f"PM2.5: {pm25}, PM10: {pm10}, CO2: {co2}, Temp: {temp}, Humidity: {humidity}")

            # Display on LCD
            display.show_sensor_data(
                pm25=pm25,
                pm10=pm10,
                co2=co2,
                temp=temp,
                humidity=humidity,
            )

            # Update every 5 seconds
            time.sleep(5)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        display.close()


if __name__ == "__main__":
    main()
