# Voltora backend

FastAPI API for Voltora, an EV battery-swap network in Karachi: stations, lockers, battery packs, riders, delivery drivers/vans and admins. The Next.js front end lives in the separate `voltora-web` repo and calls this API (through its `/api-proxy` rewrite).

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py          # http://127.0.0.1:8000, interactive docs at /docs
```

Other commands:

```bash
python main.py job daily_routes|sweep   # one-off jobs
python main.py mqtt                                                  # MQTT telemetry ingestor (needs paho-mqtt)
```

Optional extras: `psycopg2-binary` (Postgres), `paho-mqtt`, `httpx` (simulator).

> `requirements.txt` is the complete list (UTF-8, includes `psycopg2-binary`). The old `req.txt` is UTF-16 and incomplete; don't use it.

## Configuration

Environment variables (uppercase name of each field in `config/settings.py`; a local `.env` works):

| Variable | Purpose |
|---|---|
| `ENV` | `dev`, `test` or `prod`. Outside `prod` the API returns login `dev_code`s. |
| `DATABASE_URL` | Defaults to `sqlite:///./voltora.db`. Use `postgresql+psycopg2://...` in production. |
| `SECRET_KEY` | Signs JWTs. |
| `DEVICE_API_KEY` | Auth for devices and the simulator. |
| `CORS_ORIGINS` | Comma-separated origins, default `*`. |
| `PAYMENT_WEBHOOK_SECRET` | Verifies payment webhooks. |
| `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD`, `BOOTSTRAP_ADMIN_PHONE` | Creates the first admin. |
| `SEED_DEMO_DATA` | Seeds demo stations, packs, a van and station-admin accounts. |
| `OTP_DEV_ECHO` | Dev only: return OTPs in API responses. |

The defaults are insecure placeholders. Override all of them in production.

## Project layout

A request flows **route → guard → controller → module → model**.

```
main.py / application.py   entrypoint, app factory, lifespan (init_db + seeding), CORS, metrics
routes/                    URL + method + response model + guards (no logic)
guards/                    auth dependencies (current user, roles, device key)
controllers/               thin: one module call per endpoint
modules/                   business logic, one file per domain (orders, payments, pricing, fleet, ...)
models/                    SQLAlchemy ORM models
schemas/                   Pydantic request/response models
config/                    settings and database setup
constants/                 enums and seed data
utils/                     dates, geo, security, serialization helpers
integrations/              websocket hub, metrics, device commands, MQTT, payment gateway
legacy/                    old single-file monolith, reference only
```

See `CLAUDE.md` for the layering rules and how to add an endpoint.

## Roles

`ADMIN`, `STATION_ADMIN` (scoped to one station), `DRIVER` and `RIDER`. Admin sign-in is two-step (password, then a verification code).

## How it works

- **Plans:** subscriptions are locker passes only (weekly, monthly, yearly). Swaps are not bundled in a plan; each swap is priced from the pack's state of charge and health (see `modules/pricing.py`).
- **Orders:** station, locker and home-delivery swaps. Non-subscribers pay immediately. Subscribed riders can pay now or **pay later**: the bill is due 2 days after the pass ends, then a 5% penalty is added (`constants/orders.py`). Pay a later bill with `POST /api/rider/orders/{id}/pay-bill`.
- **Home delivery:** the order waits in `AWAITING_APPROVAL`. When an admin approves it (`POST /api/admin/orders/{id}/review` with `APPROVE`), the nearest van gets a delivery task. The driver completes it with the rider's delivery OTP. Rejecting or cancelling refunds the payment.
- **Codes:** pickup codes, unlock codes and delivery OTPs are 4 digits.

## Database

There are no Alembic migrations yet. `init_db()` runs on every start: it creates missing tables and adds any model columns an older database lacks (nullable). Anything beyond adding columns needs a real migration.

## PostgreSQL and local Kubernetes

The app already supports PostgreSQL: set `DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db`. `k8s/voltora.yaml` runs two containers in a `voltora` namespace: a PostgreSQL StatefulSet (with a persistent volume) and the FastAPI backend.

Needs Docker Desktop and either its built-in Kubernetes or [kind](https://kind.sigs.k8s.io/), plus `kubectl`.

```bash
docker build -t voltora-backend:local .
kind create cluster --name voltora                      # skip with Docker Desktop's Kubernetes
kind load docker-image voltora-backend:local --name voltora
kubectl apply -f k8s/voltora.yaml
kubectl -n voltora rollout status statefulset/postgres
```

**Move your existing SQLite data** (target must be empty, so stop the backend first):

```bash
kubectl -n voltora scale deploy/backend --replicas=0
kubectl -n voltora port-forward pod/postgres-0 5432:5432          # leave running in another terminal
pip install psycopg2-binary
python scripts/sqlite_to_postgres.py sqlite:///./voltora.db postgresql+psycopg2://voltora_user:voltora_secure_password@localhost:5432/voltora_db
kubectl -n voltora scale deploy/backend --replicas=1
```

Skip the migration to start with a fresh seeded database instead. Then reach the API:

```bash
kubectl -n voltora port-forward svc/backend 8000:8000             # http://localhost:8000/docs
```

The front end keeps working unchanged (`VOLTORA_API_URL=http://localhost:8000`). Change the placeholder passwords in the `Secret` in `k8s/voltora.yaml` before sharing it. Scale the API only after the first pod is Ready (`kubectl -n voltora scale deploy/backend --replicas=2`): each pod runs schema setup and seeding at start.

## Testing

There is no automated test suite. To check a change, run the server and use `/docs`, or script requests with `fastapi.testclient.TestClient` against a throwaway `DATABASE_URL`.

## Simulators

`simulator.py` has two modes. `python simulator.py swaps` runs real station swaps through the API (never home delivery): riders swap packs and pay, the returned pack's SoC and SoH drop a little before the swap is priced, and the rider, station admin and bootstrap admin all see it (orders, payments, profit log, reports). Options: `--rounds`, `--interval`, `--riders`, `--same-soh-chance`, `--charge-delay`, `--no-charge`, `--seed`. Sign-ins come from `SIM_ADMIN_USERNAME`, `SIM_ADMIN_PASSWORD`, `SIM_RIDER_PASSWORD` (defaults are the demo accounts) and `SIM_BASE_URL`. `python simulator.py telemetry` is the original fake IoT telemetry stream (uses the `DEVICE_API_KEY` header). `pilot_simulator.py` is a standalone rebalancing what-if script.

## Deployment notes

Written as a long-running server (a `Dockerfile` is included; the full stack compose file is `../docker-compose.yml`). On Vercel serverless the filesystem is read-only, WebSockets and the background sweeper/MQTT ingestor don't run reliably, and the SQLite default won't persist, so use a hosted Postgres.
