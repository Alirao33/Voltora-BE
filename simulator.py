import asyncio
import os
import random
from datetime import datetime, timezone
import httpx

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000/api/telemetry/ingest")
DEVICE_KEY = os.getenv("DEVICE_API_KEY", "change-me-device-key")  # must match DEVICE_API_KEY in backend .env

STATIONS = [
    {"station_id": "ST-01", "name": "Clifton Block 2", "slots": 8},
    {"station_id": "ST-02", "name": "Gulshan-e-Iqbal 13D", "slots": 10},
    {"station_id": "ST-03", "name": "DHA Phase 6 Commercial", "slots": 6},
    {"station_id": "ST-04", "name": "Scheme 33 City Mall", "slots": 12},
    {"station_id": "ST-05", "name": "Malir Halt", "slots": 8},
]

PACKS = {}  # each pack keeps stable health data between readings

def pack_state(pack_id: str) -> dict:
    if pack_id not in PACKS:
        PACKS[pack_id] = {
            "soh": round(random.uniform(82.0, 98.0), 1),
            "cycles": random.randint(120, 650),
            "hours": random.randint(200, 800),
            "faults": random.choice([0, 0, 0, 0, 1]),
        }
    return PACKS[pack_id]

async def generate_telemetry_reading(station_id: str, slot_id: int):
    base_temp = random.uniform(32.0, 41.0)  # Karachi ambient range
    if random.random() < 0.005:              # rare spike to test 55 C lockout
        temperature = random.uniform(56.0, 61.5)
    else:
        temperature = base_temp + random.uniform(0.5, 4.0)

    pack_id = f"VOLT-BAT-{station_id[-2:]}-{slot_id:02d}"
    p = pack_state(pack_id)
    return {
        "station_id": station_id,
        "slot_id": str(slot_id),
        "pack_id": pack_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "voltage": round(random.uniform(47.5, 54.2), 2),
        "current_a": round(random.uniform(0.5, 12.0), 2),
        "temperature_c": round(temperature, 1),
        "soc": round(random.uniform(20.0, 100.0), 1),
        "soh": p["soh"],
        "cycle_count": p["cycles"],
        "fault_count": p["faults"],
        "operating_hours": p["hours"],
        "supply_voltage_v": round(random.uniform(205, 235), 1),
    }

async def run_telemetry_daemon():
    print("=" * 65)
    print("VOLTORA IoT TELEMETRY SIMULATOR")
    print("=" * 65)
    print(f"Streaming telemetry to {BACKEND_URL}\n")
    headers = {"X-Device-Key": DEVICE_KEY}

    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            for station in STATIONS:
                for slot in range(1, station["slots"] + 1):
                    payload = await generate_telemetry_reading(station["station_id"], slot)
                    try:
                        r = await client.post(BACKEND_URL, json=payload, headers=headers)
                        if r.status_code in (200, 202):
                            print(f"OK {r.status_code} {payload['pack_id']} | SoC {payload['soc']}% | Temp {payload['temperature_c']} C")
                        else:
                            print(f"WARN {r.status_code}: {r.text}")
                    except Exception as err:
                        print(f"CONNECT ERROR - is the backend running on port 8000? {err}")
            print("\nCycle complete. Next pulse in 5 seconds...\n")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(run_telemetry_daemon())
    except KeyboardInterrupt:
        print("\nSimulator stopped.")