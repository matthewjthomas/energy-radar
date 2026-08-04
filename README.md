# Energy Radar

A predictive and historical view of energy usage in your home, correlating **electricity**,
**gas**, and **water** consumption (pulled from Home Assistant) with weather data from
[Open-Meteo](https://open-meteo.com/) (free, no API key required). Each utility source is
independently optional. Statistical regression against heating/cooling degree-days powers
both a usage forecast and automatic trend-shift detection, and you can add your own event
markers (e.g. "installed a heat pump") to quantify their before/after impact.

## Architecture

Two containers, defined in [docker-compose.yml](docker-compose.yml):

- **app** — a Python 3.14 FastAPI application that serves both the REST API and the
  server-rendered web UI, plus an in-process APScheduler that polls Home Assistant and
  Open-Meteo on a schedule.
- **db** — TimescaleDB (PostgreSQL + a time-series extension), storing readings and weather
  data in hypertables for efficient range queries.

There is no login/auth on the web UI — it's meant for local/home network use.

## Features

- Dashboard with the last 7 days by default (usage vs. weather, summary cards, forecast,
  auto-generated insights).
- History page with a calendar range picker to explore any date range.
- Weather-correlated forecast of usage (and estimated cost, if you set a price per unit) for
  the next up to 16 days, using the Open-Meteo forecast.
- Daily forecast calibration: stores predictions, scores them against the previous day's
  actual usage, tracks MAPE/RMSE, and bias-corrects live forecasts.
- Automatic detection of trend shifts in usage that aren't explained by weather alone.
- User-added event markers with before/after impact analysis.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `HA_URL` / `HA_TOKEN` — your Home Assistant base URL and a
     [long-lived access token](https://www.home-assistant.io/docs/authentication/#your-account-profile).
   - `POSTGRES_PASSWORD` — a password for the TimescaleDB container.
2. Start everything:

   ```sh
   docker compose up -d
   ```

3. Open `http://localhost:8000` and go to **Settings** to:
   - Discover Home Assistant sensors and map them to electricity/gas/water (each is optional).
   - Enter your address (geocoded via Open-Meteo, no API key needed).
   - Optionally set a price per unit for cost estimates.

### Reverse proxy / subpath

If you expose the app behind a path prefix (e.g. `https://home.example.com/energy/`), set:

```env
APP_BASE_PATH=/energy
```

All pages, API routes, and static assets are then served under that prefix (`/energy/`,
`/energy/api/...`, `/energy/static/...`). Point your proxy at the container **without**
stripping the prefix, for example:

```nginx
location /energy/ {
  proxy_pass http://127.0.0.1:8000/energy/;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Prefix /energy;
}
```

## Development and tests

Local dev builds from source (instead of pulling GHCR):

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

Run the test suite (starts TimescaleDB on port **5433** if it is not already running):

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
make install-dev
make test-cov
```

Or:

```sh
TEST_DATABASE_URL=postgresql+asyncpg://energyradar:changeme@localhost:5433/energyradar pytest
```

GitHub Actions runs `pytest` on every pull request and push to `main` (`.github/workflows/test.yml`),
including a Docker smoke test that builds the image and hits `/health`.

`/health` remains available at both `/health` and `{APP_BASE_PATH}/health`.

## Development

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://energyradar:changeme@localhost:5432/energyradar
uvicorn app.main:app --reload
```

## Container images

Images are built and pushed to `ghcr.io/<owner>/energy-radar` by
[.github/workflows/docker-build.yml](.github/workflows/docker-build.yml) on every push to `main`.

