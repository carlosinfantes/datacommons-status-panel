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

FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

RUN useradd --uid 1001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=1001:1001 /app /app

# Which build this is, shown on the page. The release workflow passes the tag,
# commit and repository; a local build keeps the package version.
ARG VERSION=""
ARG REVISION=""
ARG SOURCE=""
LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="${SOURCE}"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DCS_BUILD_VERSION="${VERSION}" \
    DCS_BUILD_COMMIT="${REVISION}" \
    DCS_BUILD_SOURCE="${SOURCE}"

USER 1001
EXPOSE 8080

# Two workers of eight threads. A collection runs its probes on its own pool, so
# request threads mostly wait: on the single-flight document cache (one
# collection per worker at a time, shared by every concurrent reader) or on the
# network. The 60 s timeout clears the 25 s collection deadline with room over.
CMD exec gunicorn --bind ":$PORT" --workers 2 --threads 8 --timeout 60 \
    --access-logfile - dc_status.app:application
