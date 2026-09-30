# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A README for the Terraform module.
- Stable releases copy the module to
  `terraform-google-datacommons-status-panel`, the repository layout the
  Terraform Registry requires. Release candidates are not copied.
- Probe failures and failed collections are logged, sanitized, so they can be
  read after the request that saw them.

### Fixed

- A probe that could not read was cached for its whole TTL: one failed schema
  read left `schema` and `version_consistency` unknown for an hour, and Refresh
  could not clear it. An unknown result is no longer cached.

## [1.0.0-rc.8] - 2026-09-28

### Fixed

- Bringing your own service account failed to plan on a fresh project: the
  module's `count` depended on `service_account_email`, which is only known
  after apply when the account is created in the same run. A plan-time
  `create_service_account` bool now decides; `service_account_email` is
  required when it is `false`.

## [1.0.0-rc.7] - 2026-09-28

### Fixed

- Latency percentiles, and Cloud Run CPU and memory p95, are now percentiles of
  the traffic: the distributions are merged across revisions (`ALIGN_DELTA` +
  `REDUCE_SUM`) and the percentiles computed from the merged buckets. Before, a
  percentile was taken per revision and the maximum reported, so one revision
  with a few slow requests set it (seen live: a p50 of 4.7 s against a 133 ms
  mean).

## [1.0.0-rc.6] - 2026-09-28

### Added

- The page shows which panel build is serving: `dc-status <version> · <commit>`
  in the footer, linking to the release notes and the commit (#4). The
  document carries it as `panel: { version, commit, source }`, set at image
  build time by the release workflow and recorded as OCI labels.

### Changed

- The bar names the platform version `DCP <version>`, so it is not mistaken for
  the panel's.

## [1.0.0-rc.5] - 2026-09-28

### Fixed

Found by the first deployment against real data:

- `data_sources` failed on Spanner: `ROWS` is a reserved keyword and was used as
  an alias. Every statement is now checked against the reserved list.
- The platform version was empty when the live revision records its image by
  digest only; it is now read from the service template when the digests match.
- A folder is matched to its provenance by the provenance's last path segment,
  ignoring case and separators (`iom-dtm` and `UNDATA/P/IOM_DTM`).
- GCS folder placeholder objects are no longer counted as input files.

## [1.0.0-rc.4] - 2026-09-28

### Added

- `vpc_access`: route the collector's egress through an existing Serverless VPC
  Access connector or Direct VPC egress (`PRIVATE_RANGES_ONLY` by default).
  Required where `constraints/run.allowedVPCEgress` is enforced.

## [1.0.0-rc.3] - 2026-09-28

### Added

- `service_account_email`: run the collector as an existing service account.
  Needed where the deployer is granted `actAs` per service account, because
  Cloud Run checks `actAs` when the service is created.

### Changed

- The module's own service account is now `google_service_account.status[0]`.
  Upgrading from an earlier release candidate re-creates it unless the caller
  adds a `moved` block.

### Documentation

- The deploying principal needs `artifactregistry.repositories.downloadArtifacts`
  on the image's repository, and `iam.serviceAccounts.actAs` on the collector's
  service account.

## [1.0.0-rc.2] - 2026-09-28

### Changed

- The Terraform module accepts the google provider 8.x (`>= 7.0, < 9.0`), tested
  on 7.x and 8.x.

## [1.0.0-rc.1] - 2026-09-28

The v1 redesign: one panel per deployment, answering four questions. See the
[design document](docs/design/2026-09-28-status-panel-v1-design.md).

### Added

- Four dimensions, System, Experience, Quality and Freshness. A dimension's
  state is the worst of its probes; `overall` is the worst dimension.
- Golden signals for the serving path, read from Cloud Monitoring over REST:
  traffic, errors, latency (p50/p95/p99) and saturation (Cloud Run CPU, memory
  and instances; Spanner high-priority CPU), through the `errors`, `latency` and
  `saturation` probes.
- Data-level probes: `import_status` (failed or retrying imports), `row_drift`
  (row drop between the last two successful ingestions) and `pending_uploads`
  (input newer than the last successful ingestion). `ingestions` can also judge
  the age of the last success.
- Configurable targets, carried inside the document so the page draws what the
  collector judged against: `DCS_TARGET_AVAILABILITY_PCT`,
  `DCS_TARGET_LATENCY_P95_MS`, `DCS_TARGET_RUN_CPU_PCT`,
  `DCS_TARGET_RUN_MEMORY_PCT`, `DCS_TARGET_SPANNER_CPU_PCT`,
  `DCS_TARGET_MIN_REQUESTS_PER_HOUR`, `DCS_TARGET_INGESTION_MAX_AGE_HOURS` and
  `DCS_TARGET_MAX_ROW_DROP_PCT`, validated at start-up.
- `DCS_CANARY_NODE`, `DCS_CANARY_NAME` and `DCS_SIGNALS_WINDOW_MINUTES`.
- Terraform: `targets`, `canary` and `signals_window_minutes` variables,
  validated at plan time with the same ranges as the collector.
- Terraform: `create_ghcr_remote` creates an Artifact Registry remote repository
  with `ghcr.io` upstream, and the `ghcr_remote_image` output gives the image
  path to pin through it. `ghcr_remote_repository_id` and `ghcr_image_path`
  adjust it.
- Every probe links to the most specific Cloud Console page for what it checked.
- `GET /api/v1/status`, returning a `schema_version: 2` document, with `?fresh=1`
  to bypass the cache at most once every 10 s per instance.
- Page: verdict, four tiles, findings, then a section per dimension, with
  sparklines, gauges, a coverage bar and a 30-day ingestion timeline. Every
  chart has a tooltip and a text equivalent.
- Page: dark mode with its own chart steps and a System/Light/Dark switch.
- `collector/tests/fixtures/demo.json`, the canonical example document, used by
  the tests, local replay and the README screenshots.
- A public image, `ghcr.io/carlosinfantes/dc-status`, published on each
  `v*.*.*` tag.
- `CHANGELOG.md`.

### Changed

- The panel reports one deployment. Deploy one panel per deployment.
- `dc_api` checks that a configurable canary node resolves to its expected
  name; anything other than 200 means `down`.
- A platform minor version newer than the panel knows is checked against the
  newest known table set, and the probe says so, instead of failing.
- Low traffic, below `min_requests_per_hour`, is shown but not judged.
- The 25 s collection deadline is shared by all probes, and the document cache
  is single-flight: concurrent requests share one collection.
- Terraform: `image` must be pinned by digest (`@sha256:`).
- Terraform: `max_request_concurrency` defaults to 16, matching gunicorn's
  2 workers × 8 threads.
- The page handles a 403 and a 5xx explicitly, sets `aria-busy` while loading,
  auto-refreshes every 60 s only while visible, and announces only a change of
  verdict to assistive technology.

### Removed

- Peer fan-in: `/api/v1/all`, the ID-token door and `peers.py`.
- `/api/v1/self`, replaced by `/api/v1/status`.
- Environment variables `DCS_PEERS`, `DCS_SELF_AUDIENCE` and
  `DCS_ALLOWED_CALLERS`.
- Terraform variables `peers`, `self_url`, `allowed_callers` and
  `invoker_members`.

### Security

- IAP is the only way in. IAP's service agent is the only principal with
  `roles/run.invoker`; the second, ID-token door that bypassed IAP is gone, and
  with it an authentication path that was never verified end to end through
  native IAP.
- Terraform: `iap_members` rejects `allUsers` and `allAuthenticatedUsers`.
- The collector's service account gains `roles/monitoring.viewer`, read-only,
  at project level, because Cloud Monitoring has no IAM below the project.
- Release images are signed with cosign (keyless) and carry SLSA build
  provenance and an SBOM.
- Responses carry `Content-Security-Policy: default-src 'self';
  frame-ancestors 'none'` and `Referrer-Policy: no-referrer`; `/api/*`
  responses carry `Cache-Control: no-store`.

[Unreleased]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.8...HEAD
[1.0.0-rc.8]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.7...v1.0.0-rc.8
[1.0.0-rc.7]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.6...v1.0.0-rc.7
[1.0.0-rc.6]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.5...v1.0.0-rc.6
[1.0.0-rc.5]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.4...v1.0.0-rc.5
[1.0.0-rc.4]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.3...v1.0.0-rc.4
[1.0.0-rc.3]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.2...v1.0.0-rc.3
[1.0.0-rc.2]: https://github.com/carlosinfantes/datacommons-status-panel/compare/v1.0.0-rc.1...v1.0.0-rc.2
[1.0.0-rc.1]: https://github.com/carlosinfantes/datacommons-status-panel/releases/tag/v1.0.0-rc.1
