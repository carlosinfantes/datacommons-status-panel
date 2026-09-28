# Status panel v1: one deployment, four dimensions

- **Status:** accepted
- **Author:** Carlos Infantes
- **Date:** 2026-09-28

## Context

The Data Commons Platform (DCP) runs on Cloud Run, Spanner, Workflows and Cloud
Storage. Google now ships liveness endpoints (`/healthz`, `/status`) and runs a
prober against its own releases. An operator of a running instance still has no
single place that answers:

- Is it up?
- Are users being served well?
- Is the data complete?
- Is the data current?

The upstream user guide sends operators to Spanner Studio to browse tables, and
to `variables.tf` to find the running version.

The v0 panel answered part of this for several environments at once, with peer
fan-in. v1 narrows and deepens it:

- **One panel per deployment.** Each operator deploys their own.
- **Four dimensions** visible at a glance: System, Experience, Quality and
  Freshness.
- **Distributable.** No threshold is hard-coded, nothing names a tenant, and
  there is a signed public image.

## Goals

1. **Verdict and dimensions at a glance.** One sentence gives the worst state
   and its cause. Four tiles give the state of each dimension.
2. **Golden signals for the serving path.** Traffic, errors, latency and
   saturation, read from Cloud Monitoring.
3. **Data-level signals nothing else shows:**
   - source coverage;
   - orphan provenances;
   - failed imports;
   - row drops between ingestions;
   - time since the last successful ingestion;
   - uploads that have not been ingested yet.
4. **Configurable targets.** Every target is set through the environment and
   the Terraform module. The page draws the target the backend judged against,
   never one of its own.
5. **Google-grade engineering:** CI, signed releases, SBOM, provenance and a
   documented security model.

## Non-goals

- **Alerting.** It stays in Cloud Monitoring. A later metrics export may feed
  it, but that is a separate project.
- **Classic custom Data Commons.** Deployments on Cloud SQL or MySQL are out of
  scope. v1 targets DCP on Spanner.
- **Multiple environments on one page.** Open one panel per deployment.
- **Stored history.** The row history comes from `IngestionHistory`, which the
  platform already keeps. The panel stores nothing.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Drop peer fan-in entirely: `peers.py`, `/api/v1/all`, the ID-token door, `invoker_members` | Each operator cares about their own deployment. Dropping it removes an unverified auth path (ID tokens through native IAP) and halves the access model. |
| D2 | IAP is the only way in | One door, verified in the app as a backstop. |
| D3 | Cloud Monitoring over REST, no client library | Keeps the dependency set at `google-auth`, `requests` and `gunicorn`. |
| D4 | Four dimensions. A dimension's status is the worst of its probes; `overall` is the worst dimension | Answers the operator's four questions in the order they ask them. |
| D5 | Targets travel inside the document | What is drawn and what is judged cannot drift apart. |
| D6 | Low traffic is shown but not judged | A quiet deployment would otherwise flap between healthy and degraded. |
| D7 | An unknown newer platform minor version is checked against the newest known table set and says so | An operator who upgrades before the panel does should not see a red panel. |
| D8 | Figures are set in the mono face, hero figures included | Page convention: a number reads as a measurement before it is read. |
| D9 | Direction "Instrument": verdict, four tiles, findings, then one section per dimension | Chosen over an editorial band in design review. |
| D10 | Dark mode has its own chart steps, validated against the dark surface. A System/Light/Dark switch is stored locally | Not an automatic inversion. |
| D11 | A public image on GHCR, signed with cosign keyless, with SLSA provenance and an SBOM. The module can create an Artifact Registry remote repository in front of GHCR | Cloud Run cannot pull from GHCR directly. |

## Probes

The shared deadline is 25 s for the whole collection, not 25 s per probe. Each
probe reports `elapsed_ms` and `budget_ms`.

| Dimension | Probe | Source | Degraded when |
|---|---|---|---|
| System | `dc_api` | GET `/core/api/v2/node?nodes=<canary>&property=->name` | The canary node does not resolve to the canary name. Anything other than 200 means `down`. |
| System | `dc_service` | Cloud Run Admin v2 | The latest revision is not ready. |
| System | `spanner` | Spanner Admin | The instance or database is not `READY`. |
| System | `schema` | `information_schema.tables` | A required table is missing. |
| System | `version_consistency` | derived | The image is newer than the schema supports. |
| System | `frontend` | GET frontend `/` | It answers anything other than 200. |
| Experience | `errors` | `run.googleapis.com/request_count` by `response_code_class` | Availability, 1 − 5xx/total, is below `availability_pct`. Not judged below `min_requests_per_hour`. |
| Experience | `latency` | `run.googleapis.com/request_latencies`, p50/p95/p99 | p95 is above `latency_p95_ms`. Not judged below `min_requests_per_hour`. |
| Experience | `saturation` | Cloud Run `container/cpu/utilizations`, `container/memory/utilizations`, `container/instance_count` (active); Spanner `instance/cpu/utilization_by_priority` (priority `high`) | Any value is at or above its target, or instances equal the configured maximum. |
| Quality | `counts` | `COUNT(*)` per table | A count fails. This is unchanged from v0. |
| Quality | `data_sources` | GCS listing crossed with `TimeSeries` by provenance | A configured source has no files or serves no rows. |
| Quality | `import_status` | `ImportStatus.State` | Any import is in `FAILURE` or `RETRY`. `PENDING`, `RUNNING` and `STAGING` count as in progress. If the table is absent, the probe reports "not available on this version" as healthy. |
| Quality | `row_drift` | derived from the last two successful `IngestionHistory` rows (`NodeCount`, `EdgeCount`, `ObservationCount`, `TimeSeriesCount`) | A count fell by more than `max_row_drop_pct`. |
| Freshness | `ingestions` | `IngestionHistory`, joined with Workflows executions | The latest run failed or is stuck (v0 logic). Also degraded if it is older than `ingestion_max_age_hours`, when that target is set. |
| Freshness | `ingestion_lock` | `IngestionLock` | The lock has been held with no active workflow (v0 logic). |
| Freshness | `pending_uploads` | derived: `data_sources` `last_updated` against the last successful completion | Any source has input newer than the last successful ingestion. |

Every probe sets `console_url` to the most specific Cloud Console page for the
thing it checked.

The collector's service account needs `roles/monitoring.viewer`. It can only be
granted at project level; the module grants it there and documents this.

## Configuration

These are new environment variables, all prefixed `DCS_`. Every one is optional.

| Variable | Default | Meaning |
|---|---|---|
| `CANARY_NODE` | `country/GTM` | The node `dc_api` resolves. |
| `CANARY_NAME` | `Guatemala` | The name that node is expected to have. |
| `SIGNALS_WINDOW_MINUTES` | `60` | Window for the golden signals, one point per minute. |
| `TARGET_AVAILABILITY_PCT` | `99.5` | |
| `TARGET_LATENCY_P95_MS` | `1000` | |
| `TARGET_RUN_CPU_PCT` | `80` | |
| `TARGET_RUN_MEMORY_PCT` | `80` | |
| `TARGET_SPANNER_CPU_PCT` | `65` | Google's recommendation for regional instances; use 45 for multi-region. |
| `TARGET_MIN_REQUESTS_PER_HOUR` | `100` | Below this, errors and latency are shown but not judged. |
| `TARGET_INGESTION_MAX_AGE_HOURS` | empty | Empty means the age is shown but not judged. |
| `TARGET_MAX_ROW_DROP_PCT` | `10` | |

Removed: `PEERS`, `SELF_AUDIENCE` and `ALLOWED_CALLERS`.

Validation happens at start-up. A percentage outside 0–100, or a negative
duration or count, is a `ConfigError` naming the variable. The Terraform module
takes a `targets` object with `optional()` defaults, a `canary` object and
`signals_window_minutes`, and validates the same ranges at plan time.

## Document, `schema_version: 2`

`GET /api/v1/status` returns the document below. `?fresh=1` bypasses the
30-second document cache, at most once every 10 s per instance. The cache is
single-flight: concurrent requests share one collection.

```jsonc
{
  "schema_version": 2,
  "generated_at": "2026-09-28T13:01:18Z",
  "partial": false,                      // true if any probe is unknown
  "overall": "degraded",                 // worst dimension
  "deployment": { "id": "prod", "label": "Production", "dcp_version": "1.1.4",
                  "project_id": "example-project", "region": "us-central1" },
  "panel": { "version": "1.0.0-rc.6", "commit": "b630f5d6c9eb",     // set at image build
             "source": "https://github.com/carlosinfantes/datacommons-status-panel" },
  "targets": { "availability_pct": 99.5, "latency_p95_ms": 1000, "run_cpu_pct": 80,
               "run_memory_pct": 80, "spanner_cpu_pct": 65, "min_requests_per_hour": 100,
               "ingestion_max_age_hours": null, "max_row_drop_pct": 10 },
  "dimensions": [                        // fixed order
    { "id": "system",     "status": "healthy",  "probes": ["dc_api", "dc_service", "spanner", "schema", "version_consistency", "frontend"] },
    { "id": "experience", "status": "degraded", "probes": ["errors", "latency", "saturation"] },
    { "id": "quality",    "status": "degraded", "probes": ["counts", "data_sources", "import_status", "row_drift"] },
    { "id": "freshness",  "status": "degraded", "probes": ["ingestions", "ingestion_lock", "pending_uploads"] }
  ],
  "probes": [ { "id": "errors", "dimension": "experience", "status": "healthy", "detail": "",
                "elapsed_ms": 820, "budget_ms": 25000,
                "console_url": "https://console.cloud.google.com/run/detail/…", "data": {} } ],
  "signals": {                           // null when Monitoring could not be read
    "window_minutes": 60, "step_seconds": 60,
    "traffic":    { "rps": 42.1, "requests": 151560, "series": [/* rps per step */] },
    "errors":     { "availability_pct": 99.94, "error_pct": 0.06, "judged": true, "series": [/* 5xx % per step */] },
    "latency":    { "p50_ms": 180, "p95_ms": 610, "p99_ms": 1400, "judged": true, "series_p95": [/* ms per step */] },
    "saturation": { "run_cpu_pct": 38, "run_memory_pct": 61, "instances": 3,
                    "max_instances": 6, "spanner_cpu_pct": 71 }
  },
  "counts": { "Observation": 191870402 },
  "count_history": [                     // successful ingestions, oldest first, at most 10
    { "completed_at": "…", "Node": 1240870, "Edge": 7010332, "Observation": 191870402, "TimeSeries": 3020114 }
  ],
  "imports": { "total": 3, "succeeded": 3, "in_progress": 0, "failed": [] },
  "ingestions": [ /* v0 shape: creation, completion, status, stage, failure, execution_seconds, imports, workflow_execution_id, workflow_state */ ],
  "freshness": { "last_success_at": "…", "age_hours": 48.2,
                 "pending_uploads": [ { "prefix": "health", "last_updated": "…" } ],
                 "lock": { "held": false, "owner": null, "since": null } },
  "data_sources": [ /* v0 shape: prefix, files, bytes, last_updated, rows */ ],
  "unmatched_provenances": [ { "provenance": "LEGACY_IMPORT", "rows": 1200000 } ]
}
```

The page derives the tile figures:

- **System:** healthy probes out of the total.
- **Experience:** `availability_pct`, with the p95 series against its target.
- **Quality:** sources serving rows out of the configured sources.
- **Freshness:** `age_hours`.

`collector/tests/fixtures/demo.json` is the canonical example. Tests read it,
it backs demos through `DCS_REPLAY_FILE`, and the README screenshots are taken
from it. It contains no real names.

## Page

The page follows direction A, as approved in the mockup.

- **Bar:** deployment label, version, snapshot age, an "auto-refresh 60 s" note,
  the theme switch and Refresh.
- **Verdict:** `<h1>` naming the worst state and its cause, plus a count of the
  other findings.
- **Four tiles.** Each tile is a link to its section.
  - System: component chips.
  - Experience: p95 sparkline with the SLO line.
  - Quality: coverage bar.
  - Freshness: 30-day ingestion timeline, with pending uploads.
- **Findings:** non-healthy probes only, each with its dimension, detail and
  "Open in console ↗". A "Show every check" button reveals the rest.
- **System:** each check with a hairline showing time taken against budget.
- **Experience:**
  - traffic, errors and latency as a figure plus a sparkline, with a target
    line where there is a target;
  - saturation as four arc gauges with a limit tick.
- **Quality:**
  - a coverage bar and a sources table with swatches;
  - rows per table, each with a sparkline from `count_history`;
  - import status.
- **Freshness:** a wide 30-day timeline and the recent ingestions table.

Charts:

- **Hover:** every chart has a tooltip on hover and keyboard focus.
- **Accessibility:** each chart has an accessible text equivalent, and the
  tables are the table view.
- **Colour:** the categorical palette is validated in light and dark.
- **State:** always glyph + word, never colour alone.

Fixes carried over from review:

- Handle non-OK responses: a 403 shows "You don't have access to this panel",
  and a 5xx shows the collector's `detail`.
- Scroll regions get `tabindex="0"` and `role="region"` with a label.
- No clipped columns at any width from 360 px up.
- Loading sets `aria-busy`.
- Auto-refresh every 60 s runs only while the tab is visible.
- Of all changes, only a change of verdict is announced to assistive technology.

Security headers:

- `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`
- `Referrer-Policy: no-referrer`
- `Cache-Control: no-store` on `/api/*`

## Distribution

- **Versioning.** SemVer; the first public release is `v1.0.0`. The changelog
  is `CHANGELOG.md`.
- **Release.** `.github/workflows/release.yml` runs on a `v*.*.*` tag. It builds
  `linux/amd64`, pushes `ghcr.io/<owner>/dc-status`, signs it with cosign, and
  attests provenance and an SBOM.
- **Terraform module:**
  - `image` must carry a digest;
  - `create_ghcr_remote` (default `false`) creates an Artifact Registry remote
    repository with GHCR upstream;
  - it outputs the image path to use through that repository.
- **README:** what it is, screenshots (light and dark), a quickstart, the
  configuration reference, the security model and the architecture.
  Decisions D1–D11 are recorded here.

## Testing

- **Probes:** unit tests for every new probe, with Monitoring responses faked
  for normal traffic, low traffic and an error spike.
- **Aggregation:** tests for dimension aggregation, the verdict inputs and
  config validation.
- **Cache and headers:** the single-flight cache (two threads trigger one
  collection) and the security headers.
- **Removed routes:** `/api/v1/all` returns 404.
- **Contract:** a test that `demo.json` has every key the page reads, in the
  documented shape.
- **Page:** checked by screenshot against the fixture at 390, 1280 and 1920 px,
  in light and dark, plus a keyboard pass and simulated 403, 500 and network
  failures.
- **CI:** ruff, pytest, `terraform validate`, `node --check` and an image build.

## Rollout

1. Build the image, deploy to a staging DCP, and read the real IAP `aud`.
2. Production, behind IAP, for the first operator.
3. Tag `v1.0.0`, make the repository public and publish the image.
