# -*- coding: utf-8 -*-
"""Location: ./tests/unit/mcpgateway/services/test_tool_service_helpers.py
Copyright contributors to the MCP-CONTEXT-FORGE project
SPDX-License-Identifier: Apache-2.0

Helper tests for tool_service schema and response filter utilities.
"""

# Third-Party
import jsonschema
import pytest

# First-Party
from mcpgateway.services import tool_service


def test_validate_with_cached_schema_success_and_error():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    tool_service._validate_with_cached_schema({"a": "ok"}, schema)

    with pytest.raises(jsonschema.exceptions.ValidationError):
        tool_service._validate_with_cached_schema({"a": 1}, schema)


def test_get_validator_fallback_draft4():
    schema = {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "type": "number",
        "minimum": 0,
        "exclusiveMinimum": True,
    }
    schema_json = tool_service._canonicalize_schema(schema)
    validator_cls, parsed = tool_service._get_validator_class_and_check(schema_json)
    assert parsed["type"] == "number"
    assert validator_cls is not None


def test_apply_response_filter_edge_cases():
    assert tool_service.apply_response_filter({"a": 1}, "") == {"a": 1}
    assert tool_service.apply_response_filter("not json", "response.a") == ["Invalid JSON string provided."]
    assert tool_service.apply_response_filter(123, "response.a") == ["Input data must be a JSON string, dictionary, or list."]


def test_apply_response_filter_rejects_a_filter_that_is_not_cel():
    """A stored jq-era filter is reported at invoke time, not executed."""
    # First-Party
    from mcpgateway.common.models import TextContent

    result = tool_service.apply_response_filter({"a": 1}, "$ENV")
    assert result == [TextContent(type="text", text="jsonpath filter is not a valid CEL expression")]


def test_apply_response_filter_does_not_echo_engine_errors(monkeypatch):
    """Filter engine detail goes to the log, never to the caller."""
    # First-Party
    from mcpgateway.common.models import TextContent
    from mcpgateway.utils.cel_runner import CelEvaluationError

    monkeypatch.setattr(tool_service, "evaluate_value", lambda *_args: (_ for _ in ()).throw(CelEvaluationError("secret path /etc/app/x")))
    result = tool_service.apply_response_filter({"a": 1}, "response.a")
    assert result == [TextContent(type="text", text="Error applying jsonpath filter")]
    assert "secret path" not in result[0].text


def test_apply_response_filter_reports_timeout_distinctly(monkeypatch):
    """A timed-out filter is distinguishable from a malformed one."""
    # First-Party
    from mcpgateway.common.models import TextContent
    from mcpgateway.utils.cel_runner import CelTimeout

    monkeypatch.setattr(tool_service, "evaluate_value", lambda *_args: (_ for _ in ()).throw(CelTimeout("too slow")))
    result = tool_service.apply_response_filter({"a": 1}, "response.a")
    assert result == [TextContent(type="text", text="jsonpath filter exceeded the execution time limit")]


def test_apply_response_filter_reports_busy_distinctly_from_timeout(monkeypatch):
    """A full pool with no free worker is distinguishable from a genuine timeout."""
    # First-Party
    from mcpgateway.common.models import TextContent
    from mcpgateway.utils.cel_runner import CelBusy

    monkeypatch.setattr(tool_service, "evaluate_value", lambda *_args: (_ for _ in ()).throw(CelBusy("no free worker")))
    result = tool_service.apply_response_filter({"a": 1}, "response.a")
    assert result == [TextContent(type="text", text="jsonpath filter sandbox is busy, try again")]



def test_as_content_blocks_passes_a_block_list_through():
    """A filter that selected whole content blocks keeps them as blocks."""
    blocks = [{"type": "text", "text": "a"}, {"type": "image", "data": "x", "mimeType": "image/png"}]
    assert tool_service._as_content_blocks(blocks) is blocks


@pytest.mark.parametrize("value,expected", [("a", '"a"'), (1, "1"), ({"a": 1}, '{"a":1}'), (["a", "b"], '["a","b"]'), ([], "[]"), (None, "null"), ([{"a": 1}], '[{"a":1}]')])
def test_as_content_blocks_wraps_anything_else_as_text(value, expected):
    """A value picked out of a block becomes one text block carrying its JSON."""
    # First-Party
    from mcpgateway.common.models import TextContent

    assert tool_service._as_content_blocks(value) == [TextContent(type="text", text=expected)]
