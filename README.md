# Data Commons Status Panel

A status panel for [Data Commons Platform](https://datacommons.org) deployments. It is a
single page that answers, for one or more environments:

- is it healthy?
- what was ingested, and when?
- how much data is there?
- which data sources are covered?

## What it measures

The panel runs ten probes against a deployment:

- `dc_api` — the public API endpoint resolves a known entity
- `dc_service` — Cloud Run service state, latest ready revision, and the live platform version
- `spanner` — instance and database state, processing units, retention
- `schema` — which known tables exist in the database
- `version_consistency` — the running image against the tables the database actually has
- `counts` — row count per table
- `ingestions` — the last ten ingestion runs, correlated with their workflow executions
- `ingestion_lock` — whether the ingestion lock is held, and for how long
- `data_sources` — input objects per source, crossed with the rows served from each
- `frontend` — the frontend responds

## Running locally

```bash
cd collector
uv sync
uv run pytest
```

## Status

The collector and the Terraform module are under construction.
