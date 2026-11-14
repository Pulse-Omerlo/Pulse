# Load test

Measures how fast the tenant API answers when many people browse PULSE at once, using
[Locust](https://docs.locust.io/en/stable/). It only runs on synthetic data.

Target: **p95 under 300 ms and 0% failed requests with 50 users** on the organization list,
one organization, duplicate candidates, sync runs and sector mix.

Locust is in its own dependency group (`load`), so it is not installed by `uv sync`, not in CI and
not in the Docker image.

## 1. Load the data

From `backend/`, with Postgres running and the schema up to date (`uv run alembic upgrade head`):

```bash
uv run python -m synthetic.mock_omerlo           # in a second terminal, keep it running
uv run python -m app.seed
uv run python -m app.sync --tenant demo-capitale --full
uv run python -m loadtest.make_orgs --count 2000
uv run python -m app.import_file --tenant demo-capitale --kind organization loadtest/out/organizations.csv --full
```

`make_orgs` writes 2,000 organizations plus a copy of one in ten (same website, name in capitals),
so matching has 200 pairs to group. `--full` marks rows from an earlier, bigger file as removed.
The import takes about 20 seconds on a laptop.

## 2. Start the API

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 3. Run the load test

Headless, 50 users for 2 minutes, results saved as CSV:

```bash
uv run --group load locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless --users 50 --spawn-rate 10 --run-time 2m --csv loadtest/out/run
```

`loadtest/out/run_stats.csv` has one line per endpoint: the 95% column is p95 in milliseconds, and
`Failure Count` must be 0. Leave out `--headless ... --csv ...` to use the web UI at
http://localhost:8089 instead. `LOAD_TENANT=demo-estrie` tests the other tenant.

Each simulated user waits 1 to 3 seconds between requests, like a person reading the page, so 50
users send about 25 requests per second.
