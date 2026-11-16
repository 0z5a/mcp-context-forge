# -*- coding: utf-8 -*-
"""Location: ./tests/unit/mcpgateway/db/test_translate_jsonpath_filters_migration.py
Copyright contributors to the MCP-CONTEXT-FORGE project
SPDX-License-Identifier: Apache-2.0

Tests for the jq-to-CEL jsonpath_filter translation migration.
"""

# Standard
import importlib
import logging
from unittest.mock import patch

# Third-Party
import pytest
from sqlalchemy import create_engine, text

MIGRATION_MODULE = "mcpgateway.alembic.versions.f2c3d4e5a6b7_translate_jsonpath_filters_to_cel"

TRANSLATED = [
    (".", "response"),
    (".a", "response.a"),
    (".data.items", "response.data.items"),
    (".[0]", "response[0]"),
    (".a[2].b", "response.a[2].b"),
    (".[].a", "response.map(x, x.a)"),
    (".items[].name", "response.items.map(x, x.name)"),
    (".data.items[].id", "response.data.items.map(x, x.id)"),
    (".items[]", "response.items"),
    (".[]", "response"),
    (" .a ", "response.a"),
]

LEFT_ALONE = ["$.main", "$ENV", "select(.a)", ".a | .b", ".[].a[].b", '.["a-b"]', ".a?", "user@example.com", "response.a"]


@pytest.fixture
def migration():
    """Load the migration module."""
    return importlib.import_module(MIGRATION_MODULE)


@pytest.mark.parametrize("jq,cel", TRANSLATED)
def test_jq_to_cel(migration, jq, cel):
    """Each simple jq shape has one CEL equivalent."""
    assert migration.jq_to_cel(jq) == cel


@pytest.mark.parametrize("jq", LEFT_ALONE)
def test_jq_to_cel_refuses_anything_else(migration, jq):
    """A filter outside the simple shapes is not guessed at."""
    assert migration.jq_to_cel(jq) is None


@pytest.mark.parametrize("jq,cel", [(jq.strip(), cel) for jq, cel in TRANSLATED if jq.strip() not in (".items[]", ".[]")])
def test_cel_to_jq_reverses_the_translation(migration, jq, cel):
    """Downgrade restores the original jq for every shape that keeps its structure."""
    assert migration.cel_to_jq(cel) == jq


@pytest.mark.parametrize("cel", ["response.items.filter(x, x.a)", "response.map(i, i.a)", '{"n": response.a}', "response.a + 1", ".a"])
def test_cel_to_jq_refuses_hand_written_cel(migration, cel):
    """A CEL expression that was not produced by the upgrade has no jq form."""
    assert migration.cel_to_jq(cel) is None


@pytest.fixture
def connection():
    """Create a minimal tools table."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tools (id TEXT PRIMARY KEY, name TEXT, jsonpath_filter TEXT)"))
        rows = [("t1", ".a"), ("t2", ".data.items[].id"), ("t3", ""), ("t4", None), ("t5", "$.main"), ("t6", "response.items.filter(x, x.a)"), ("t7", "select(.a)")]
        for tool_id, stored in rows:
            conn.execute(text("INSERT INTO tools VALUES (:id, :name, :f)"), {"id": tool_id, "name": f"tool-{tool_id}", "f": stored})
    with engine.begin() as conn:
        yield conn
    engine.dispose()


def _filters(connection):
    """Read every stored filter keyed by tool id."""
    return dict(connection.execute(text("SELECT id, jsonpath_filter FROM tools ORDER BY id")).all())


def test_upgrade_translates_simple_filters_and_warns_about_the_rest(migration, connection, caplog):
    """Simple jq paths become CEL, the rest stay as stored and are named in a warning."""
    with patch.object(migration.op, "get_bind", return_value=connection), caplog.at_level(logging.WARNING, logger=MIGRATION_MODULE):
        migration.upgrade()

    assert _filters(connection) == {"t1": "response.a", "t2": "response.data.items.map(x, x.id)", "t3": "", "t4": None, "t5": "$.main", "t6": "response.items.filter(x, x.a)", "t7": "select(.a)"}
    warned = [record.getMessage() for record in caplog.records]
    assert any("'tool-t5'" in message and "(id t5)" in message and "$.main" in message and "CEL" in message for message in warned)
    assert any("t7" in message and "select(.a)" in message for message in warned)
    assert not any("t6" in message for message in warned)


def test_upgrade_is_idempotent(migration, connection):
    """Running the upgrade twice changes nothing the second time."""
    with patch.object(migration.op, "get_bind", return_value=connection):
        migration.upgrade()
        first = _filters(connection)
        migration.upgrade()
    assert _filters(connection) == first


def test_downgrade_restores_the_translated_filters(migration, connection, caplog):
    """Downgrade reverses the upgrade and leaves CEL that has no jq form, with a warning."""
    with patch.object(migration.op, "get_bind", return_value=connection), caplog.at_level(logging.WARNING, logger=MIGRATION_MODULE):
        migration.upgrade()
        caplog.clear()
        migration.downgrade()

    filters = _filters(connection)
    assert filters["t1"] == ".a"
    assert filters["t2"] == ".data.items[].id"
    assert filters["t5"] == "$.main"
    assert filters["t6"] == "response.items.filter(x, x.a)"
    assert filters["t7"] == "select(.a)"
    assert any("t6" in record.getMessage() and "jq" in record.getMessage() for record in caplog.records)


def test_missing_tools_table_is_safe(migration):
    """A fresh database without a tools table is skipped."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection, patch.object(migration.op, "get_bind", return_value=connection):
        migration.upgrade()
        migration.downgrade()
    engine.dispose()
