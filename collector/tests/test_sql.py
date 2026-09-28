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

"""The probes' SQL is only ever run against Spanner by a fake in these tests, so
the fakes cannot catch a statement Spanner would reject. This checks the one
mistake that shipped: an alias that is a GoogleSQL reserved keyword (`AS Rows`)."""

from __future__ import annotations

import re

import pytest

from dc_status import probes

# GoogleSQL reserved keywords (Spanner). An unquoted alias from this list is a
# syntax error at query time.
_RESERVED = frozenset(
    [
        "ALL",
        "AND",
        "ANY",
        "ARRAY",
        "AS",
        "ASC",
        "ASSERT_ROWS_MODIFIED",
        "AT",
        "BETWEEN",
        "BY",
        "CASE",
        "CAST",
        "COLLATE",
        "CONTAINS",
        "CREATE",
        "CROSS",
        "CUBE",
        "CURRENT",
        "DEFAULT",
        "DEFINE",
        "DESC",
        "DISTINCT",
        "ELSE",
        "END",
        "ENUM",
        "ESCAPE",
        "EXCEPT",
        "EXCLUDE",
        "EXISTS",
        "EXTRACT",
        "FALSE",
        "FETCH",
        "FOLLOWING",
        "FOR",
        "FROM",
        "FULL",
        "GROUP",
        "GROUPING",
        "GROUPS",
        "HASH",
        "HAVING",
        "IF",
        "IGNORE",
        "IN",
        "INNER",
        "INTERSECT",
        "INTERVAL",
        "INTO",
        "IS",
        "JOIN",
        "LATERAL",
        "LEFT",
        "LIKE",
        "LIMIT",
        "LOOKUP",
        "MERGE",
        "NATURAL",
        "NEW",
        "NO",
        "NOT",
        "NULL",
        "NULLS",
        "OF",
        "ON",
        "OR",
        "ORDER",
        "OUTER",
        "OVER",
        "PARTITION",
        "PRECEDING",
        "PROTO",
        "RANGE",
        "RECURSIVE",
        "RESPECT",
        "RIGHT",
        "ROLLUP",
        "ROWS",
        "SELECT",
        "SET",
        "SOME",
        "STRUCT",
        "TABLESAMPLE",
        "THEN",
        "TO",
        "TREAT",
        "TRUE",
        "UNBOUNDED",
        "UNION",
        "UNNEST",
        "USING",
        "WHEN",
        "WHERE",
        "WINDOW",
        "WITH",
        "WITHIN",
    ]
)

_STATEMENTS = {
    name: value
    for name, value in vars(probes).items()
    if name.endswith("_SQL") and isinstance(value, str)
}


def test_there_are_statements_to_check():
    assert "_PROVENANCE_SQL" in _STATEMENTS


@pytest.mark.parametrize("name", sorted(_STATEMENTS))
def test_no_alias_is_a_reserved_keyword(name):
    aliases = re.findall(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", _STATEMENTS[name], re.IGNORECASE)
    assert [a for a in aliases if a.upper() in _RESERVED] == []
