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
cp local.env.example local.env   # fill in your own project, then edit
uv run python -m dc_status.cli --env-file local.env
```

The WSGI app (`dc_status.app`) serves the same document over HTTP: `/healthz`,
`/api/v1/self`, `/api/v1/all`, and the static page at `/`. Peers are fetched
server-to-server with an ID token minted for the peer's own URL as audience,
so the page never has to deal with CORS.

### Replaying a saved document

Setting `DCS_REPLAY_FILE` to a JSON file makes the `/api/v1/*` routes serve that
file's contents instead of probing anything — a development affordance for
iterating on the page's design without live credentials or a running
deployment. **The Terraform module never sets this variable**; it is for local
use only.

## Building the image

The collector ships as a container built via Cloud Build and pushed to Artifact
Registry, pinned by digest — never by tag, since tags are mutable:

```bash
cd collector
IMAGE=<REGION>-docker.pkg.dev/<PROJECT>/<REPO>/dc-status
gcloud builds submit . --tag "$IMAGE:0.1.0" --project=<PROJECT>
gcloud artifacts docker images describe "$IMAGE:0.1.0" \
  --project=<PROJECT> --format="value(image_summary.digest)"
```

The resolved digest (`sha256:…`) is combined with the tag as
`<IMAGE>:0.1.0@sha256:…` and that full reference is what gets pinned into the
Terraform module's `image` variable. `DCS_REPLAY_FILE` (see above) is a
development-only affordance; the Terraform module never sets it.
