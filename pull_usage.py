#!/usr/bin/env python3
"""Pull water usage data from Eye on Water (Liberty Utilities)."""

import asyncio
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from pyonwater import Account, Client

load_dotenv()

HOSTNAME = os.getenv("EOW_HOSTNAME", "liberty.eyeonwater.com")
USERNAME = os.getenv("EOW_USERNAME")
PASSWORD = os.getenv("EOW_PASSWORD")
DAYS_TO_LOAD = int(os.getenv("EOW_DAYS", "90"))
OUTPUT_DIR = Path("data")


async def main():
    if not USERNAME or not PASSWORD:
        print("Error: Set EOW_USERNAME and EOW_PASSWORD in .env")
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    account = Account(
        eow_hostname=HOSTNAME,
        username=USERNAME,
        password=PASSWORD,
    )

    async with aiohttp.ClientSession() as session:
        client = Client(session, account)

        print(f"Logging in as {USERNAME} @ {HOSTNAME}...")
        await client.authenticate()
        print("Authenticated.")

        meters = await account.fetch_meters(client)
        print(f"Found {len(meters)} meter(s)")

        for meter in meters:
            print(f"\n--- Meter: {meter.meter_id} ---")

            await meter.read_meter_info(client=client)
            print(f"  Current reading: {meter.reading}")
            print(f"  Unit: {meter.native_unit_of_measurement}")

            history = await meter.read_historical_data(
                client=client,
                days_to_load=DAYS_TO_LOAD,
            )
            print(f"  Historical data points: {len(history)}")

            if not history:
                continue

            # Build data list
            data = []
            for point in history:
                data.append({
                    "date": point.dt.isoformat(),
                    "reading": point.reading,
                    "unit": point.unit,
                })

            # Save as JSON
            json_path = OUTPUT_DIR / f"meter_{meter.meter_id}.json"
            with open(json_path, "w") as f:
                json.dump({
                    "meter_id": meter.meter_id,
                    "meter_uuid": meter.meter_uuid,
                    "unit": meter.native_unit_of_measurement,
                    "pulled_at": datetime.now().isoformat(),
                    "days": DAYS_TO_LOAD,
                    "data_points": len(data),
                    "history": data,
                }, f, indent=2)
            print(f"  Saved JSON: {json_path}")

            # Save as CSV
            csv_path = OUTPUT_DIR / f"meter_{meter.meter_id}.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["date", "reading", "unit"])
                for point in data:
                    writer.writerow([point["date"], point["reading"], point["unit"]])
            print(f"  Saved CSV: {csv_path}")

    print(f"\nDone. Data pulled at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())
