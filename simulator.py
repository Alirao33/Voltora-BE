"""Voltora simulator.

Two modes (run from VS Code or a terminal, with the backend and database already running):

    python simulator.py swaps       # default: real battery swaps with money moving between rider, station and bootstrap admin
    python simulator.py telemetry   # the original IoT telemetry stream (fake readings for the ST-01..ST-05 demo stations)

Swap mode drives the real API, so every swap shows up in the rider app, the station admin and the bootstrap admin:
orders, payments, the profit log (bootstrap share and station share) and the reports. It never creates home deliveries.

    python simulator.py swaps --rounds 10 --interval 2
    python simulator.py swaps --riders rider1,rider2 --same-soh-chance 0.3 --no-charge

Nothing here is started automatically. Read the option list with:  python simulator.py swaps --help
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

# ======================================================================================
# Mode 1: the original telemetry stream (unchanged)
# ======================================================================================

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
    if random.random() < 0.005:  # rare spike to test 55 C lockout
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


# ======================================================================================
# Mode 2: real swaps through the API
# ======================================================================================

BASE_URL = os.getenv("SIM_BASE_URL", "http://127.0.0.1:8000")
SIM_ADMIN_USER = os.getenv("SIM_ADMIN_USERNAME", "admin")  # the bootstrap admin
SIM_ADMIN_PASS = os.getenv("SIM_ADMIN_PASSWORD", "Admin@2026!")
SIM_STATION_ADMIN_USER = os.getenv("SIM_STATION_ADMIN_USERNAME", "admin_saddar")  # only used for the closing station view
SIM_STATION_ADMIN_PASS = os.getenv("SIM_STATION_ADMIN_PASSWORD", "Saddar@2026!")
SIM_RIDER_PASS = os.getenv("SIM_RIDER_PASSWORD", "Rider@2026!")
DEFAULT_RIDERS = "rider1,rider2,rider3,rider4,rider5"


class SimError(Exception):
    """A request the simulator cannot recover from."""


class Api:
    def __init__(self, base_url: str):
        self.http = httpx.Client(base_url=base_url, timeout=60.0)

    def _send(self, method: str, path: str, token: str | None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            r = self.http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as err:
            raise SimError(f"Cannot reach the backend at {self.http.base_url} ({err}). Is it running?") from err
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise SimError(f"{method} {path} -> {r.status_code}: {detail}")
        return r.json() if r.content else {}

    def get(self, path: str, token: str | None = None, **params):
        return self._send("GET", path, token, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, body: dict, token: str | None = None):
        return self._send("POST", path, token, json=body)

    def patch(self, path: str, body: dict, token: str | None = None):
        return self._send("PATCH", path, token, json=body)

    def login_form(self, username: str, password: str) -> str:
        return self._send("POST", "/token", None, data={"username": username, "password": password})["access_token"]


def admin_login(api: Api, username: str, password: str) -> str:
    """Admin sign-in is two steps. The backend echoes the code in dev; otherwise it is asked for here."""
    challenge = api.post("/auth/admin/login", {"username": username, "password": password})
    code = challenge.get("dev_code") or input(f"Verification code for {username}: ").strip()
    return api.post("/auth/admin/verify", {"challenge_id": challenge["challenge_id"], "code": code})["access_token"]


def rows(data) -> list:
    """The API returns either a list or a {stations|items|data: [...]} wrapper."""
    if isinstance(data, list):
        return data
    for key in ("stations", "items", "data"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def rs(n) -> str:
    return f"Rs {float(n or 0):,.2f}"


class Fleet:
    """A snapshot of every pack, refreshed from the admin API at the start of each swap."""

    def __init__(self, api: Api, admin: str):
        self.api, self.admin = api, admin
        self.packs: dict[str, dict] = {}

    def refresh(self):
        self.packs = {p["id"]: p for p in self.api.get("/api/admin/batteries", self.admin, limit=500)}

    def charged_by_station(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.packs.values():
            if p["state"] == "CHARGED" and (p.get("soc") or 0) >= 20 and p.get("station_id"):
                out[p["station_id"]] = out.get(p["station_id"], 0) + 1
        return out


def post_reading(api: Api, admin: str, pack: dict, *, soc: float, soh: float, cycles: int, hours: float, temp: float):
    """Tell the backend what a pack looks like now. Capacity is sent in Ah so SoC and SoH are worked out exactly."""
    rated = pack["rated_capacity_ah"]
    full = round(rated * soh / 100.0, 3)
    api.post(
        "/api/telemetry/ingest",
        {
            "pack_id": pack["id"],
            "ts": datetime.now(timezone.utc).isoformat(),
            "voltage": round(random.uniform(46.5, 50.5), 2),
            "current_a": 0.0,
            "temperature_c": round(temp, 1),
            "rated_ah": rated,
            "full_charge_ah": full,
            "remaining_ah": round(full * soc / 100.0, 3),
            "cycle_count": cycles,
            "operating_hours": round(hours, 1),
        },
        admin,  # an admin sign-in is accepted in place of the device key
    )


def pick_riders(api: Api, names: list[str], fleet: Fleet) -> dict[str, dict]:
    """Sign each rider in and hand them a pack to ride with (one that is IN_USE or sitting depleted)."""
    spare = [p for p in fleet.packs.values() if p["state"] in ("IN_USE", "DEPLETED")]
    random.shuffle(spare)
    riders: dict[str, dict] = {}
    for name in names:
        try:
            token = api.login_form(name, SIM_RIDER_PASS)
        except SimError as err:
            print(f"  skipping {name}: {err}")
            continue
        if not spare:
            print(f"  skipping {name}: no spare pack for them to ride with")
            continue
        riders[name] = {"token": token, "pack_id": spare.pop()["id"], "swaps": 0, "paid": 0.0, "refunded": 0.0}
    return riders


def ride_wear(held: dict, out: dict, same_soh: bool) -> tuple[float, float]:
    """How the rider's pack looks when it comes back: a little less SoH (never a big drop) and a mostly used-up charge."""
    if same_soh:  # comes back in the same SoH as the pack it is swapped for, so the same-SoH discount applies
        new_soh = min(100.0, max(held["soh"], out["soh"]))
    else:
        new_soh = held["soh"] - random.uniform(0.05, 0.25)
    return round(new_soh, 2), round(random.uniform(8.0, 30.0), 1)


def one_swap(api: Api, admin: str, fleet: Fleet, stations: list[dict], name: str, rider: dict, args, n: int, total: int, charge_queue: list):
    fleet.refresh()
    held = fleet.packs.get(rider["pack_id"])
    if not held:
        print(f"Swap {n}/{total}  {name}: their pack {rider['pack_id']} is not in the fleet any more, skipped.")
        return None

    stock = fleet.charged_by_station()
    open_stations = [s for s in stations if stock.get(s["id"], 0) > 0]
    if not open_stations:
        print(f"Swap {n}/{total}  {name}: no station has a charged pack right now, skipped.")
        return None
    st = random.choice(open_stations)

    # The rider rides with the pack and uses most of its charge.
    used_fraction = random.uniform(0.65, 0.90)
    capacity_used_ah = round(held["full_charge_capacity_ah"] * used_fraction, 2)

    order = api.post(
        "/api/rider/orders",
        {
            "station_id": st["id"],
            "mode": "STATION",  # swaps at a station only, never home delivery
            "latitude": st["lat"],
            "longitude": st["lon"],
            "capacity_used_ah": capacity_used_ah,
            "payment_method": "RAAST",
            "payment_reference": "SIM-" + uuid.uuid4().hex[:12],
            "use_subscription": False,
        },
        rider["token"],
    )
    out = fleet.packs.get(order["pack_out_id"], held)

    # The returned pack's SoC and SoH go down a little, then the swap is priced from those readings.
    same = random.random() < args.same_soh_chance
    new_soh, soc_left = ride_wear(held, out, same)
    temp = random.uniform(30.0, 40.0)
    post_reading(api, admin, held, soc=soc_left, soh=new_soh, cycles=held["cycle_count"] + 1, hours=held["operating_hours"] + random.uniform(1, 3), temp=temp)

    api.post(f"/api/rider/orders/{order['id']}/unlock", {"pickup_code": order["pickup_code"]}, rider["token"])
    done = api.post(f"/api/rider/orders/{order['id']}/complete", {"returned_pack_id": held["id"]}, rider["token"])

    price = done.get("price") or {}
    paid = float(done.get("charged_pkr") or 0)
    refund = max(0.0, -float(done.get("settlement_delta_pkr") or 0))
    rider["swaps"] += 1
    rider["paid"] += paid
    rider["refunded"] += refund
    rider["pack_id"] = order["pack_out_id"]  # they ride away with the pack they were given

    if not args.no_charge:
        charge_queue.append((n + args.charge_delay, held["id"], st["id"], new_soh))

    print(f"Swap {n}/{total}  {name} at {st['id']} ({st['name']})  [{done['order_id']}, receipt {done.get('receipt_no')}]")
    print(f"  returned {held['id']}: SoC {soc_left}%  SoH {held['soh']:.2f} -> {new_soh:.2f} ({new_soh - held['soh']:+.2f})  cycles {held['cycle_count'] + 1}")
    print(f"  issued   {order['pack_out_id']}")
    print(f"  price    SoC {rs(price.get('soc_charge_pkr'))} | SoH {rs(price.get('soh_charge_pkr'))} | same-SoH discount {rs(done.get('price', {}).get('same_soh_discount_pkr'))} | total {rs(done.get('actual_total_pkr') or done.get('total_pkr'))}")
    print(f"  rider    paid {rs(paid)}  refund {rs(refund)}")
    return done["id"]


def charge_due(api: Api, admin: str, fleet: Fleet, queue: list, round_no: int):
    """Chargers fill the packs that came back, so stations do not run dry."""
    for item in [q for q in queue if q[0] <= round_no]:
        _, pack_id, station_id, soh = item
        queue.remove(item)
        fleet.refresh()
        pack = fleet.packs.get(pack_id)
        if not pack or pack["state"] != "DEPLETED":
            continue
        post_reading(api, admin, pack, soc=100.0, soh=soh, cycles=pack["cycle_count"], hours=pack["operating_hours"], temp=random.uniform(28.0, 33.0))
        api.patch(f"/api/admin/batteries/{pack_id}", {"state": "CHARGED", "station_id": station_id}, admin)
        print(f"  charger  {pack_id} is full again at {station_id}")


def profit_line(api: Api, admin: str, order_id: int | None):
    """The profit row of a swap: who earned what once SoC and SoH were priced."""
    if order_id is None:
        return
    log = api.get("/api/admin/profit/log", admin)
    row = next((r for r in log.get("rows", []) if r["order_id"] == order_id), None)
    if row:
        print(f"  profit   {rs(row['profit_pkr'])} = bootstrap {row['bootstrap_pct']:g}% {rs(row['bootstrap_share_pkr'])} + station {rs(row['station_share_pkr'])}")
    else:
        print("  profit   (no SoC/SoH charge on this swap, so nothing to split)")


def closing_report(api: Api, admin: str, riders: dict[str, dict], args):
    print("\n" + "=" * 72)
    print("MONEY AFTER THIS RUN")
    print("=" * 72)
    for name, r in riders.items():
        print(f"  rider {name:<7} {r['swaps']} swaps   paid {rs(r['paid'])}   refunded {rs(r['refunded'])}   net {rs(r['paid'] - r['refunded'])}")
    log = api.get("/api/admin/profit/log", admin)
    t = log.get("totals", {})
    print(f"\n  Bootstrap admin view (all stations, whole profit log so far)")
    print(f"    swaps logged {t.get('swaps', 0)}   SoC+SoH income {rs(t.get('revenue_pkr'))}   cost {rs(t.get('cost_pkr'))}   profit {rs(t.get('profit_pkr'))}")
    print(f"    bootstrap share {rs(t.get('bootstrap_share_pkr'))}   stations keep {rs(t.get('station_share_pkr'))}")
    try:
        st_token = admin_login(api, SIM_STATION_ADMIN_USER, SIM_STATION_ADMIN_PASS)
        st = api.get("/api/admin/profit/log", st_token).get("totals", {})
        print(f"\n  Station admin view ({SIM_STATION_ADMIN_USER}, own station only)")
        print(f"    swaps {st.get('swaps', 0)}   profit {rs(st.get('profit_pkr'))}   paid to bootstrap {rs(st.get('bootstrap_share_pkr'))}   keeps {rs(st.get('station_share_pkr'))}")
    except SimError as err:
        print(f"\n  (station admin view skipped: {err})")

    print("\nWHERE TO LOOK")
    print("  Rider app            My orders (each swap, its price and receipt)")
    print("  Station admin        Orders, Reports (money in), Profit log (own station), Batteries (SoC/SoH now lower)")
    print("  Bootstrap admin      Profit log (10% share), Reports, Orders, Audit log, Batteries")


def run_swaps(args) -> int:
    api = Api(BASE_URL)
    random.seed(args.seed)
    print("=" * 72)
    print("VOLTORA SWAP SIMULATOR  (station swaps only, no home delivery)")
    print("=" * 72)
    print(f"Backend {BASE_URL}  |  {args.rounds} swaps  |  riders {args.riders}\n")

    admin = admin_login(api, SIM_ADMIN_USER, SIM_ADMIN_PASS)
    fleet = Fleet(api, admin)
    fleet.refresh()

    stations = []
    for s in rows(api.get("/api/guest/stations", radius_km=500)):
        sid = s.get("id") or s.get("station_id") or s.get("site_id")
        lat = s.get("latitude", s.get("lat"))
        lon = s.get("longitude", s.get("lon"))
        if sid and lat is not None and lon is not None and not s.get("is_depot"):
            stations.append({"id": sid, "name": s.get("name") or sid, "lat": lat, "lon": lon})
    if not stations:
        raise SimError("No stations came back from /api/guest/stations.")

    riders = pick_riders(api, [r.strip() for r in args.riders.split(",") if r.strip()], fleet)
    if not riders:
        raise SimError("No rider could take part (check SIM_RIDER_PASSWORD and that spare packs exist).")
    print(f"Riders ready: {', '.join(riders)}\n")

    order = list(riders)
    charge_queue: list = []
    for n in range(1, args.rounds + 1):
        name = order[(n - 1) % len(order)]
        try:
            order_id = one_swap(api, admin, fleet, stations, name, riders[name], args, n, args.rounds, charge_queue)
            profit_line(api, admin, order_id)
            charge_due(api, admin, fleet, charge_queue, n)
        except SimError as err:
            print(f"Swap {n}/{args.rounds}  {name}: FAILED - {err}")
        print()
        if n < args.rounds:
            time.sleep(args.interval)

    closing_report(api, admin, riders, args)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voltora simulator")
    sub = parser.add_subparsers(dest="mode")
    sub.add_parser("telemetry", help="stream fake IoT telemetry for the demo stations")
    sw = sub.add_parser("swaps", help="run real station swaps and move money between rider, station and bootstrap admin")
    sw.add_argument("--rounds", type=int, default=5, help="how many swaps to run (default 5)")
    sw.add_argument("--interval", type=float, default=2.0, help="seconds to wait between swaps (default 2)")
    sw.add_argument("--riders", default=DEFAULT_RIDERS, help=f"comma separated rider usernames (default {DEFAULT_RIDERS})")
    sw.add_argument("--same-soh-chance", type=float, default=0.25, help="share of swaps where the pack comes back in the same SoH, which earns the discount (default 0.25)")
    sw.add_argument("--charge-delay", type=int, default=2, help="swaps before a returned pack is charged and put back in stock (default 2)")
    sw.add_argument("--no-charge", action="store_true", help="leave returned packs depleted (stations will run out of charged packs)")
    sw.add_argument("--seed", type=int, default=None, help="fix the random numbers to repeat a run")
    args = parser.parse_args(argv)

    if args.mode == "telemetry":
        try:
            asyncio.run(run_telemetry_daemon())
        except KeyboardInterrupt:
            print("\nSimulator stopped.")
        return 0
    if args.mode is None:  # plain `python simulator.py` runs the swaps with the defaults
        args = parser.parse_args(["swaps"])
    try:
        return run_swaps(args)
    except SimError as err:
        print(f"\nSTOPPED: {err}")
        return 1
    except KeyboardInterrupt:
        print("\nSimulator stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
