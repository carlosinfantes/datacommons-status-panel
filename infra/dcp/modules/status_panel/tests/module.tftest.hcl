# Copyright 2026 Carlos Infantes
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Plan-only tests against a mocked provider: they need no credentials, so CI can
# run them on every change.

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }
}

variables {
  project_id               = "example-project"
  region                   = "us-central1"
  image                    = "us-central1-docker.pkg.dev/example-project/ops/dc-status:1.0.0@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  env_id                   = "prod"
  spanner_instance_id      = "example-instance"
  spanner_database_id      = "example-db"
  datacommons_service_name = "example-dc-service"
  ingestion_workflow_name  = "example-ingestion"
  artifacts_bucket_name    = "example-artifacts"
  public_endpoint_url      = "https://api.example.org"
  frontend_url             = "https://www.example.org"
  iap_audience             = "/projects/123456789012/locations/us-central1/services/example"
  iap_members              = ["group:dc-admins@example.org"]
}

run "defaults_plan" {
  command = plan

  assert {
    condition     = output.ghcr_remote_image == null
    error_message = "No GHCR remote is created unless asked for."
  }
}

run "partial_targets_keep_the_other_defaults" {
  command = plan

  variables {
    targets = { availability_pct = 99.9 }
  }

  assert {
    condition     = var.targets.spanner_cpu_pct == 65 && var.targets.availability_pct == 99.9
    error_message = "Unset targets must fall back to their defaults."
  }
}

run "ghcr_remote_points_at_ghcr" {
  command = plan

  variables {
    create_ghcr_remote = true
  }

  assert {
    condition     = one(google_artifact_registry_repository.ghcr[*].remote_repository_config[0].common_repository[0].uri) == "https://ghcr.io"
    error_message = "The remote repository must proxy ghcr.io."
  }

  assert {
    condition     = endswith(output.ghcr_remote_image, "/carlosinfantes/dc-status")
    error_message = "The output must name the image path through the remote."
  }
}

run "rejects_an_image_without_a_digest" {
  command = plan

  variables {
    image = "ghcr.io/carlosinfantes/dc-status:1.0.0"
  }

  expect_failures = [var.image]
}

run "rejects_public_iap_members" {
  command = plan

  variables {
    iap_members = ["allUsers"]
  }

  expect_failures = [var.iap_members]
}

run "rejects_a_percentage_above_100" {
  command = plan

  variables {
    targets = { run_cpu_pct = 120 }
  }

  expect_failures = [var.targets]
}
