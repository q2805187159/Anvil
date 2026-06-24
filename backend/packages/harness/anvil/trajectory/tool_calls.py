from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from anvil.memory.scrubber import MemorySecretScrubber


FENCED_JSON_RE = re.compile(r"```(?:json|tool|tool_call|function_call)?\s*(?P<body>[\s\S]*?)```", re.IGNORECASE)
XML_TOOL_RE = re.compile(r"<(?P<tag>tool_call|function_call|tool|function)>(?P<body>[\s\S]*?)</(?P=tag)>", re.IGNORECASE)
FUNCTION_LINE_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)\s*\((?P<args>[\s\S]*?)\)\s*$")
TOOL_NAME_RE = re.compile(r"^\s*(?:tool|function|name)\s*[:=]\s*(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)\s*$", re.IGNORECASE | re.MULTILINE)
ARGUMENTS_RE = re.compile(r"^\s*(?:arguments|args|parameters)\s*[:=]\s*(?P<args>[\s\S]*)$", re.IGNORECASE | re.MULTILINE)


class ParsedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    source_format: str
    confidence: float = 1.0
    raw: str = ""


class ToolCallParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[ParsedToolCall] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class ToolCallParser:
    """Parse common text tool-call encodings into Anvil's normalized call shape."""

    def __init__(self, *, scrubber: MemorySecretScrubber | None = None) -> None:
        self.scrubber = scrubber or MemorySecretScrubber()

    def parse(self, value: Any) -> ToolCallParseResult:
        diagnostics: list[str] = []
        calls: list[ParsedToolCall] = []
        self._collect(value, calls=calls, diagnostics=diagnostics, source_hint="native")
        if isinstance(value, str):
            self._parse_text(value, calls=calls, diagnostics=diagnostics)
        unique: list[ParsedToolCall] = []
        seen: set[tuple[str, str]] = set()
        for call in calls:
            key = (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False))
            if key in seen:
                continue
            seen.add(key)
            unique.append(call)
        return ToolCallParseResult(calls=unique, diagnostics=diagnostics)

    def _parse_text(self, text: str, *, calls: list[ParsedToolCall], diagnostics: list[str]) -> None:
        stripped = text.strip()
        if not stripped:
            return
        self._try_json_text(stripped, calls=calls, diagnostics=diagnostics, source_format="json")
        for match in FENCED_JSON_RE.finditer(text):
            self._try_json_text(match.group("body").strip(), calls=calls, diagnostics=diagnostics, source_format="fenced_json")
        for match in XML_TOOL_RE.finditer(text):
            body = match.group("body").strip()
            if not self._try_json_text(body, calls=calls, diagnostics=diagnostics, source_format="xml_json"):
                self._parse_labeled_text(body, calls=calls, diagnostics=diagnostics, source_format="xml_labeled")
        self._parse_function_line(stripped, calls=calls, diagnostics=diagnostics)
        self._parse_labeled_text(stripped, calls=calls, diagnostics=diagnostics, source_format="labeled")

    def _try_json_text(self, text: str, *, calls: list[ParsedToolCall], diagnostics: list[str], source_format: str) -> bool:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return False
        before = len(calls)
        self._collect(payload, calls=calls, diagnostics=diagnostics, source_hint=source_format)
        return len(calls) > before

    def _collect(
        self,
        value: Any,
        *,
        calls: list[ParsedToolCall],
        diagnostics: list[str],
        source_hint: str,
    ) -> None:
        if isinstance(value, list):
            for item in value:
                self._collect(item, calls=calls, diagnostics=diagnostics, source_hint=source_hint)
            return
        if not isinstance(value, dict):
            return
        if "tool_calls" in value and isinstance(value["tool_calls"], list):
            for item in value["tool_calls"]:
                self._collect(item, calls=calls, diagnostics=diagnostics, source_hint=source_hint)
        if "function_call" in value:
            self._collect(value["function_call"], calls=calls, diagnostics=diagnostics, source_hint=source_hint)
        if "function" in value and isinstance(value["function"], dict):
            function = value["function"]
            merged = {
                "id": value.get("id"),
                "name": function.get("name"),
                "arguments": function.get("arguments") or function.get("args"),
            }
            self._append_call(merged, calls=calls, diagnostics=diagnostics, source_format=source_hint)
            return
        if str(value.get("type") or "") == "function_call" and isinstance(value.get("name"), str):
            self._append_call(value, calls=calls, diagnostics=diagnostics, source_format=source_hint)
            return
        if any(key in value for key in ("name", "tool", "tool_name", "function_name")):
            self._append_call(value, calls=calls, diagnostics=diagnostics, source_format=source_hint)

    def _append_call(
        self,
        value: dict[str, Any],
        *,
        calls: list[ParsedToolCall],
        diagnostics: list[str],
        source_format: str,
    ) -> None:
        name = value.get("name") or value.get("tool") or value.get("tool_name") or value.get("function_name")
        if not isinstance(name, str) or not name.strip():
            diagnostics.append(f"{source_format}: missing tool name")
            return
        args_value = value.get("args")
        if args_value is None:
            args_value = value.get("arguments")
        if args_value is None:
            args_value = value.get("parameters")
        args = self._normalize_args(args_value, diagnostics=diagnostics, source_format=source_format)
        calls.append(
            ParsedToolCall(
                id=str(value.get("id") or value.get("tool_call_id") or value.get("call_id") or f"parsed_{uuid4().hex[:10]}"),
                name=name.strip(),
                args=_scrub_payload(self.scrubber, args),
                source_format=source_format,
                confidence=1.0 if source_format in {"native", "json", "fenced_json", "xml_json"} else 0.7,
                raw=self._safe_raw(value),
            )
        )

    def _parse_function_line(self, text: str, *, calls: list[ParsedToolCall], diagnostics: list[str]) -> None:
        match = FUNCTION_LINE_RE.search(text)
        if not match:
            return
        args = self._parse_loose_args(match.group("args"), diagnostics=diagnostics)
        calls.append(
            ParsedToolCall(
                id=f"parsed_{uuid4().hex[:10]}",
                name=match.group("name"),
                args=_scrub_payload(self.scrubber, args),
                source_format="function_line",
                confidence=0.65,
                raw=_scrub_text(self.scrubber, text[:1000]),
            )
        )

    def _parse_labeled_text(self, text: str, *, calls: list[ParsedToolCall], diagnostics: list[str], source_format: str) -> None:
        name_match = TOOL_NAME_RE.search(text)
        args_match = ARGUMENTS_RE.search(text)
        if not name_match:
            return
        args = {}
        if args_match:
            args_text = args_match.group("args").strip()
            args = self._normalize_args(args_text, diagnostics=diagnostics, source_format=source_format)
        calls.append(
            ParsedToolCall(
                id=f"parsed_{uuid4().hex[:10]}",
                name=name_match.group("name"),
                args=_scrub_payload(self.scrubber, args),
                source_format=source_format,
                confidence=0.6,
                raw=_scrub_text(self.scrubber, text[:1000]),
            )
        )

    def _normalize_args(self, value: Any, *, diagnostics: list[str], source_format: str) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
                diagnostics.append(f"{source_format}: arguments JSON is not an object")
                return {"value": parsed}
            except json.JSONDecodeError:
                return self._parse_loose_args(text, diagnostics=diagnostics)
        diagnostics.append(f"{source_format}: unsupported arguments type {type(value).__name__}")
        return {"value": value}

    def _parse_loose_args(self, text: str, *, diagnostics: list[str]) -> dict[str, Any]:
        if not text.strip():
            return {}
        if text.strip().startswith("{"):
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError as exc:
                diagnostics.append(f"loose_args: invalid JSON arguments: {exc.msg}")
                return {"raw": _scrub_text(self.scrubber, text)}
        if "=" not in text:
            diagnostics.append("loose_args: invalid JSON arguments")
            return {"raw": _scrub_text(self.scrubber, text)}
        args: dict[str, Any] = {}
        for part in _split_top_level_commas(text):
            if not part.strip():
                continue
            if "=" not in part:
                args.setdefault("_positional", []).append(_scrub_text(self.scrubber, part.strip().strip("\"'")))
                continue
            key, raw_value = part.split("=", 1)
            args[key.strip()] = _coerce_scalar(raw_value.strip().strip("\"'"))
        return args

    def _safe_raw(self, value: dict[str, Any]) -> str:
        try:
            return _scrub_text(self.scrubber, json.dumps(value, ensure_ascii=False, sort_keys=True)[:1000])
        except TypeError:
            return _scrub_text(self.scrubber, str(value)[:1000])


def parse_tool_calls(value: Any) -> ToolCallParseResult:
    return ToolCallParser().parse(value)


def _scrub_text(scrubber: MemorySecretScrubber, value: str) -> str:
    return scrubber.scrub(value).text


def _scrub_payload(scrubber: MemorySecretScrubber, value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(scrubber, value)
    if isinstance(value, list):
        return [_scrub_payload(scrubber, item) for item in value]
    if isinstance(value, dict):
        return {str(key): _scrub_payload(scrubber, item) for key, item in value.items()}
    return value


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
            current.append(char)
            continue
        if char in ")]}":
            depth = max(depth - 1, 0)
            current.append(char)
            continue
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _coerce_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
