from __future__ import annotations

from anvil.trajectory import parse_tool_calls


def test_parse_openai_style_tool_calls() -> None:
    result = parse_tool_calls(
        {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"path": "/mnt/user-data/workspace/a.py", "content": "print(1)"}',
                    },
                }
            ]
        }
    )

    assert len(result.calls) == 1
    assert result.calls[0].id == "call_1"
    assert result.calls[0].name == "write_file"
    assert result.calls[0].args["path"] == "/mnt/user-data/workspace/a.py"
    assert result.calls[0].source_format == "native"


def test_parse_responses_api_function_call_items() -> None:
    result = parse_tool_calls(
        {
            "type": "function_call",
            "name": "bash",
            "arguments": '{"cmd": "pwd"}',
            "call_id": "tc-1",
        }
    )

    assert len(result.calls) == 1
    assert result.calls[0].id == "tc-1"
    assert result.calls[0].name == "bash"
    assert result.calls[0].args == {"cmd": "pwd"}


def test_parse_fenced_json_and_xml_tool_calls() -> None:
    result = parse_tool_calls(
        """
        Use this:
        ```json
        {"name": "web_search", "args": {"query": "anvil"}}
        ```
        <tool_call>{"name": "read_file", "arguments": {"path": "/mnt/user-data/workspace/README.md"}}</tool_call>
        """
    )

    assert [(call.name, call.source_format) for call in result.calls] == [
        ("web_search", "fenced_json"),
        ("read_file", "xml_json"),
    ]


def test_parse_function_line_and_labeled_text() -> None:
    function_line = parse_tool_calls("run_command(command='pytest -q', timeout_seconds=30)")
    labeled = parse_tool_calls("tool: browser_navigate\narguments: {\"url\":\"https://example.com\"}")

    assert function_line.calls[0].name == "run_command"
    assert function_line.calls[0].args["command"] == "pytest -q"
    assert function_line.calls[0].args["timeout_seconds"] == 30
    assert function_line.calls[0].confidence == 0.65
    assert labeled.calls[0].name == "browser_navigate"
    assert labeled.calls[0].args["url"] == "https://example.com"


def test_parser_scrubs_secret_values_and_reports_invalid_args() -> None:
    result = parse_tool_calls('{"name": "web_fetch", "args": "not json but sk-testsecretsecretsecret"}')

    assert result.calls[0].args["raw"] == "not json but [REDACTED:api_key]"
    assert any("invalid JSON" in item for item in result.diagnostics)


def test_parser_deduplicates_equivalent_calls() -> None:
    result = parse_tool_calls(
        """
        {"name": "web_search", "args": {"query": "anvil"}}
        ```json
        {"name": "web_search", "args": {"query": "anvil"}}
        ```
        """
    )

    assert len(result.calls) == 1
    assert result.calls[0].name == "web_search"
