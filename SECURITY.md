# Security policy

The status panel renders a reconnaissance map of a Data Commons Platform
deployment: project, database, bucket and table names, row counts, and ingestion
history. A weakness in its access control is therefore a real exposure, and
reports are welcome.

## Reporting a vulnerability

Please report privately through GitHub's
[private vulnerability reporting](../../security/advisories/new) on this
repository. Do not open a public issue.

Include what you found, how to reproduce it, and the version (tag or image
digest) you tested. You should get an acknowledgement within three working days.

## Supported versions

Only the latest release receives security fixes.

## Verifying a release image

Every release image is signed with cosign (keyless) and carries SLSA build
provenance. Verify before deploying:

```bash
cosign verify ghcr.io/carlosinfantes/dc-status@sha256:<digest> \
  --certificate-identity-regexp 'https://github.com/carlosinfantes/datacommons-status-panel/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
