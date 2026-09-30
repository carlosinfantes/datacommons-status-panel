# Data Commons Status Panel module

Deploys [Data Commons Status Panel](https://github.com/carlosinfantes/datacommons-status-panel)
next to a custom Data Commons (Data Commons Platform on Spanner): one page that
tells the operator whether the deployment is up, serving users well, holding
complete data and holding current data.

The module runs the collector on Cloud Run behind Identity-Aware Proxy, grants
it read-only roles on the platform's resources, and can create an Artifact
Registry remote repository in front of GHCR so Cloud Run can pull the signed
public image.

## Usage

```hcl
module "status_panel" {
  source = "github.com/carlosinfantes/datacommons-status-panel//infra/dcp/modules/status_panel?ref=v1.0.0"

  project_id    = var.project_id
  region        = var.region
  instance_name = "prod"

  create_ghcr_remote = true
  image              = "us-central1-docker.pkg.dev/my-project/prod-ghcr/carlosinfantes/dc-status:1.0.0@sha256:<digest>"

  env_id                   = "prod"
  spanner_instance_id      = "<instance>"
  spanner_database_id      = "<database>"
  datacommons_service_name = "<datacommons-cloud-run-service>"
  ingestion_workflow_name  = "<ingestion-workflow>"
  artifacts_bucket_name    = "<bucket>"
  public_endpoint_url      = "https://api.example.org"
  frontend_url             = "https://www.example.org"

  iap_members  = ["group:dc-admins@example.org"]
  iap_audience = "<read after the first deploy>"
}
```

`image` must be pinned by digest. `iap_audience` is required while
`require_auth` is on; the
[Quickstart](https://github.com/carlosinfantes/datacommons-status-panel#quickstart)
explains how to read it after the first deploy, and lists the permissions the
principal running `terraform apply` needs.

## Reference

Every variable is documented in `variables.tf` and in the
[configuration reference](https://github.com/carlosinfantes/datacommons-status-panel#configuration-reference).
The outputs are `service_name`, `service_uri`, `service_account_email` and
`ghcr_remote_image`.

## License

Apache 2.0.
