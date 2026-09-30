# Contributing

Issues and pull requests are welcome. For anything larger than a small fix,
open an issue first so the change can be agreed before the work is done.

## Checks

Everything CI runs can be run locally:

```bash
cd collector
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
node --test tests/web/app.test.js

cd ../infra/dcp/modules/status_panel
terraform fmt -check -recursive
terraform init -backend=false && terraform validate
```

## Conventions

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).
- Every source file carries the Apache-2.0 header; a test enforces it.
- The repository describes no particular deployment. Names of real projects,
  organisations or buckets belong in the deployment that uses the module, not here.
- A probe that cannot tell reports `unknown`, never `healthy`.
- The page never carries state by colour alone, and sets figures in the mono face.

By contributing you agree that your contributions are licensed under the
Apache License 2.0.
