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

## The page

The page is a readout, not a dashboard. It has three parts, in the order you need
them:

1. **The verdict** — one sentence naming the worst state and the check that caused
   it, in the largest type on the page. If nothing is wrong it says so.
2. **The matrix** — measures down, environments across. Every figure sits on a
   shared right-hand baseline, so comparing two deployments means looking down a
   column instead of reading one card and remembering it while you read the next.
   The measure column stays put while the environment columns scroll.
3. **Findings**, then per-environment data sources and ingestions. Findings lists
   only the checks that are not healthy, because a passing check has nothing to
   say; `Show every check` reveals the rest.

### How long a check took, against how long it had

Each probe emits `budget_ms` alongside `elapsed_ms`, because a duration on its own
cannot say whether a check is comfortable or one second from being dropped. The
page draws the ratio as a hairline under the timing, quiet below half the budget,
warning above it, critical from 85%.

The budget is whichever deadline actually binds that probe, not a single number
for all of them:

| Probe | Budget | Why |
|---|---|---|
| `dc_api`, `frontend` | 8 s | `PublicClient` timeout, no retries |
| `counts` | 20 s | the probe's own `budget_seconds` |
| everything else | 25 s | the collection pool deadline — their REST calls retry up to 30 s, so the pool is what gives up first |
| `version_consistency` | none (`0`) | derived from two other probes, does no I/O, never raced against a clock |

Reporting a flat 25 s would have been worse than reporting nothing: a frontend
check dying at its own 8 s ceiling would have rendered as a comfortable 32% of
budget. `test_a_probe_reports_whichever_deadline_actually_binds_it` pins the table
to the client constants so the two cannot drift apart.

A peer running an older collector will not send `budget_ms`; the page falls back
to showing the bare figure rather than inventing a denominator.

Two conventions the page does not break: **state is never carried by colour
alone** (every state also has a drawn glyph and a word), and **figures are only
ever set in the mono face**, so a number is recognisable as a measurement before
you read it.

`overall` at the document level drives the verdict. Note that a *difference in
platform version between environments* is no longer flagged as an alarm — staging
running ahead of production is routine. The failure that matters is an image newer
than the database it is serving from, and that is what the `version_consistency`
probe reports, per environment. The versions are still side by side in the matrix,
where a difference is visible without being editorialised.

### Fonts

IBM Plex Sans (variable) and IBM Plex Mono ship inside the image as woff2 in
`dc_status/web/fonts/`, about 60 KB together, and are served from `/static/fonts/`.
They are bundled rather than fetched from a CDN because the page sits behind IAP
and must not depend on a third-party request. They are OFL-1.1; the notice is in
`dc_status/web/fonts/OFL.txt`, which ships in the wheel but is deliberately not a
route.

### Motion

One central definition per curve, `transform` and `opacity` only, everything
under 300 ms, and `prefers-reduced-motion` drops movement while keeping the fade.

## Access control

The document names projects, buckets, tables, row counts and ingestion history —
a reconnaissance map of the platform. It is admin-only, and access is controlled
in two layers.

**The perimeter** is the Terraform module. IAP fronts the service
(`enable_iap`, on by default), `allUsers` is never granted, and the only invoker
is IAP's own service agent. With `iap_members` left empty nobody can get in, so
the default posture is closed and access is something you add deliberately:

```hcl
iap_members = ["group:dc-admins@example.org"]
```

A group rather than individuals, so joining and leaving is not a `terraform
apply`.

`invoker_members` is the second door and the weaker one: a principal there
reaches Cloud Run with an ID token, without the IAP consent screen or
`roles/iap.httpsResourceAccessor`. It exists so peer panels can fan in. A
`validation` block rejects anything that is not a `serviceAccount:`, because a
human added there "just to curl it" would be a permanent, silent bypass.

**The backstop** is the collector itself (`dc_status/auth.py`). Every route
except `/healthz` requires one of two verified credentials:

| Caller | Credential | Verified against |
|---|---|---|
| a human via the browser | `X-Goog-IAP-JWT-Assertion` | IAP's key set, `iss` of `https://cloud.google.com/iap`, `aud` of `DCS_IAP_AUDIENCE` |
| a peer panel fanning in | `Authorization: Bearer <ID token>` | Google's OIDC keys, `aud` of `DCS_SELF_AUDIENCE`, `email` in `DCS_ALLOWED_CALLERS` |

Anything else gets `403 {"error": "forbidden"}` — the reason goes to the logs,
never to the caller, so a probe cannot learn which door it got closest to.
`/healthz` stays open because Cloud Run's startup probe does not traverse IAP and
the route says nothing beyond "the process is up".

Authorisation is deliberately **not** re-implemented in the app. Once an
assertion verifies, IAP has already decided the caller holds
`iap.httpsResourceAccessor`; copying that list into an env var would only let the
two drift. The machine door is different — an ID token proves identity and
nothing about permission — so those callers are named explicitly.

`DCS_REQUIRE_AUTH` defaults to **on**, and the collector refuses to start
serving if it is on with no credential configured: a panel nobody can reach is a
misconfiguration, and it should say so once rather than look like an access
problem to every admin who tries. The module checks the same condition with
cross-variable `validation`, not a `lifecycle` precondition, so it fails in
`terraform plan` with no GCP credentials needed — which means CI catches it.

Two things to know before the first deploy:

- **`iap_audience` has to be read, not guessed.** The `aud` format differs
  between IAP behind a load balancer and IAP enabled natively on Cloud Run, so
  the module does not assume one. Decode the assertion header from a real request
  after the first deploy and pin the value. A wrong value fails closed.
- **IAP needs an OAuth consent screen** (`google_iap_brand`) in the project. This
  module does not create one; confirm the deployment repo does, or the first
  `apply` fails.

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
