# CLAUDE.md — Voltora backend

FastAPI API for Voltora, an EV battery-swap network in Karachi (stations, lockers, battery packs, riders, drivers/vans, admin). It's deployed to Vercel on its own. The front end lives in a separate repo (`voltora-web`, Next.js) and calls this API through its `/api-proxy` rewrite.

## Layout

The old single-file `main.py` was split into layers. A request flows **route → guard → controller → module → model (ORM)**.

```
main.py              entrypoint: `app = create_app()`; `python main.py` runs cli.main()
application.py       create_app(): FastAPI instance, lifespan, CORS, metrics middleware, mounts routes.api_router
cli.py               `python main.py [job <name> | mqtt]` and the uvicorn server

routes/              URL + method + response_model + guards. One file per resource. No logic.
  __init__.py        api_router: mounts every router (order matters for path matching)
  admin/ rider/      sub-packages that share a prefix (/api/admin, /api/rider)
guards/              auth dependencies: get_current_user, require_roles/require_admin/require_super_admin/require_driver
                     (auth.py), require_device_or_admin and the WebSocket device check (device.py)
controllers/         thin: take request params, call ONE module function, return its result. No queries, no rules.
                     Mirrors routes/ one-to-one.
modules/             business logic, one file per domain (orders, bookings, stations, lockers, batteries, fleet,
                     pricing, payments, telemetry, auth, users, ...). Takes plain args + a Session.
models/              SQLAlchemy ORM models, one file per table. `import models` registers every table.
schemas/             Pydantic request/response models, grouped by domain.
config/              settings.py (env-driven Settings), database.py (engine, SessionLocal, Base, get_db, init_db)
constants/           enums.py (all enums), plus roles, bookings, orders, battery, pilot, seed, security constants
utils/               shared helpers: dates (PKT/UTC), numbers, geo, security (hashing), access (station scoping),
                     serialization (dual snake/camel JSON), db_types
integrations/        infrastructure adapters: websocket hub, prometheus metrics, device commands, MQTT, payment gateway
```

### Rules for the layers

- **Dependencies point down only:** routes → controllers → modules → models/utils/constants/config. `modules/` must never import `routes`, `controllers` or `guards`. `models/` import only `config`, `constants` and `utils`.
- **Authorization lives at the route level.** Each route declares its guard with `dependencies=[Depends(require_...)]`. Controllers receive the principal with `Depends(get_current_user)` (cached, so the guard doesn't run twice) and pass it to the module.
- **Controllers are thin.** One module call per endpoint. If a controller grows an `if`, a query or a loop, move it into `modules/`.
- **All database access goes through the ORM** (`select()`, `Session`, models). The only raw SQL is DDL in `config/database.py` (`ALTER TABLE` migration, TimescaleDB setup).
- **Modules must not import each other in a cycle.** Keep low-level modules (`audit`, `alerts`, `lockers`, `payments`, `stations`) free of imports from the higher-level ones (`orders`, `sites`, `delivery`, `fleet`). That's why admin site management is in `modules/sites.py` and not in `stations.py`.
- Prefer deep modules: a small set of public functions that hide the rules. Don't add pass-through modules.

### Adding an endpoint

1. Request/response models in `schemas/<domain>.py`.
2. Business logic in `modules/<domain>.py` (plain function: `db`, the actor, parameters).
3. Controller in `controllers/...` calling that function.
4. Route in `routes/...` with `router.add_api_route(path, controller.fn, methods=[...], response_model=..., dependencies=[Depends(guard)])`.
5. New file in `routes/`? Mount it in `routes/__init__.py`.
6. New table? Add `models/<name>.py` and export it from `models/__init__.py`.

## Run locally

```bash
python -m venv .venv && .venv\Scripts\activate
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings bcrypt pyjwt python-multipart prometheus-client
python main.py                # http://127.0.0.1:8000, docs at /docs (reload on unless ENV=prod)
python main.py job daily_routes|sweep
python main.py mqtt           # MQTT telemetry ingestor (needs paho-mqtt)
```

Optional extras are `psycopg2-binary` (Postgres), `paho-mqtt` and `httpx` (simulator).

Vercel's Python runtime expects `requirements.txt`. When you touch dependencies, keep it complete.

`simulator.py` posts fake telemetry to `/api/telemetry/ingest` with the `DEVICE_API_KEY` header. `voltora.db` is the local SQLite dev database.

## Configuration

Every setting is a field on `Settings` in `config/settings.py` and can be overridden by an env var with the same name in uppercase. These matter for deploys:

- `ENV` (`dev`/`test`/`prod`)
- `DATABASE_URL`: defaults to `sqlite:///./voltora.db`. For prod, use `postgresql+psycopg2://...`.
- `SECRET_KEY`: signs JWTs.
- `DEVICE_API_KEY`: for devices and the simulator.
- `CORS_ORIGINS`: comma-separated, defaults to `*`.
- `PAYMENT_WEBHOOK_SECRET`
- `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD`, `BOOTSTRAP_ADMIN_PHONE`
- `SEED_DEMO_DATA`
- `OTP_DEV_ECHO`: dev only. It returns the OTP in the response.

The defaults are insecure placeholders (`change-me-*`, `Admin@2026!`), so always override them in prod.

## Vercel deployment caveats

The app was written as a long-running server (Dockerfile and docker-compose with Postgres and Redis). On Vercel serverless:

- **The filesystem is read-only** except `/tmp`, and `/tmp` doesn't persist. The SQLite default won't persist, so use a hosted Postgres `DATABASE_URL`.
- **The `lifespan` (in `application.py`) runs on every cold start.** It calls `init_db()`, which does `create_all` plus an ad-hoc `ALTER TABLE users ADD COLUMN station_id`, and then the seeding. Keep these idempotent and fast.
- **The background sweeper (`modules/jobs.py`) and the MQTT ingestor won't run reliably.** Booking/order expiry (`sweep`) and the route job need a Vercel Cron hitting an endpoint, or an external worker.
- **WebSockets (`/ws/telemetry/{pack_id}`, `/ws/alerts`) aren't supported** on Vercel serverless functions. The in-memory hub and Prometheus counters are per-instance.
- A Vercel entrypoint only needs to expose `app` from `main.py`.

## API conventions

- Roles (`Role` in `constants/enums.py`) are `ADMIN`, `STATION_ADMIN`, `DRIVER` and `RIDER`. `STATION_ADMIN` is scoped to `user.station_id` through `scoped_station_id()` (`utils/access.py`).
- Device endpoints use `require_device_or_admin` (device API key header or admin JWT).
- Responses go through `DualKeyJSONResponse` (`utils/serialization.py`), which emits both `snake_case` and `camelCase` keys. Don't hand-build camelCase.
- Datetimes are stored as naive UTC through `UTCDateTime`. Naive datetimes from clients count as PKT (UTC+5); use `now_utc()` / `now_pkt()` from `utils/dates.py`.
- Money is PKR. Pricing logic is in `modules/pricing.py` (`calculate_price`, `PricingConfig`, stored in `AppSetting`).
- Admin and state-changing actions should call `audit(...)` from `modules/audit.py`.
- No Alembic yet. Schema changes rely on `create_all`, which won't alter existing tables. Add a guarded migration in `init_db()` or introduce Alembic.

## Contract with the front end

The front-end repo (`voltora-web`) has typed calls for these endpoints in `src/lib/endpoints.ts` and `src/lib/types.ts`. If you rename a route, change a response shape or change auth behaviour (`/token`, `/auth/refresh`, `/auth/admin/login` + `/auth/admin/verify`), update the front end too, or tell the user to.

## Testing

There's no automated test suite. The refactor from the monolith was checked by diffing `app.openapi()` before and after (identical) and by replaying the same request sequence against both versions. To check a change, run the server and use `/docs`, or script requests with `fastapi.testclient.TestClient` against a throwaway `DATABASE_URL`.
