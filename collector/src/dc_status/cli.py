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

"""Run the collector from a terminal. Same code path as the web handler."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .assemble import collect_status
from .cache import TTLCache
from .clients import build_clients
from .config import ConfigError, load_config, load_env_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dc_status.cli", description="Print the status document")
    parser.add_argument("--env-file", help="KEY=VALUE file layered over the process environment")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    environ = dict(os.environ)
    if args.env_file:
        environ.update(load_env_file(args.env_file))

    try:
        config = load_config(environ)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    clients = build_clients(config)
    cache = TTLCache()
    document = collect_status(config, clients, cache)
    print(json.dumps(document, indent=args.indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
