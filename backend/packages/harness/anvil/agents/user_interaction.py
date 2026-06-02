# BOUNDARY: agent-runtime-only
from __future__ import annotations

from hashlib import sha1
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SelectionMode = Literal["single", "multiple", "text"]
InteractionKind = Literal["choice", "input", "form"]


class UserInteractionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str | None = None
    recommended: bool = False
    disabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "label")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class UserInteractionField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    description: str | None = None
    selection_mode: SelectionMode = "single"
    options: list[UserInteractionOption] = Field(default_factory=list)
    min_selections: int = 1
    max_selections: int | None = 1
    allow_custom: bool = False
    custom_label: str | None = None
    placeholder: str | None = None
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("field_id", "label")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("min_selections")
    @classmethod
    def _min_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("min_selections must be non-negative")
        return value

    @field_validator("max_selections")
    @classmethod
    def _max_positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("max_selections must be positive")
        return value

    @model_validator(mode="after")
    def _validate_selection_bounds(self) -> "UserInteractionField":
        if self.selection_mode == "text":
            self.options = []
            self.min_selections = 1 if self.required else 0
            self.max_selections = None
            return self
        if not self.options and not self.allow_custom:
            raise ValueError("choice fields require options or allow_custom=true")
        if self.selection_mode == "single":
            self.max_selections = 1
            if self.required and self.min_selections == 0:
                self.min_selections = 1
        if self.max_selections is not None and self.min_selections > self.max_selections:
            raise ValueError("min_selections cannot exceed max_selections")
        if self.options and self.max_selections is not None and self.max_selections > len(self.options) and not self.allow_custom:
            self.max_selections = len(self.options)
        return self


class UserInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    kind: InteractionKind = "choice"
    title: str | None = None
    question: str
    description: str | None = None
    selection_mode: SelectionMode = "single"
    options: list[UserInteractionOption] = Field(default_factory=list)
    min_selections: int = 1
    max_selections: int | None = 1
    allow_custom: bool = False
    custom_label: str | None = None
    placeholder: str | None = None
    required: bool = True
    source_tool_name: str = "ask_clarification"
    fields: list[UserInteractionField] = Field(default_factory=list)

    @field_validator("request_id", "question")
    @classmethod
    def _required_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("min_selections")
    @classmethod
    def _min_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("min_selections must be non-negative")
        return value

    @field_validator("max_selections")
    @classmethod
    def _max_positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("max_selections must be positive")
        return value

    @model_validator(mode="after")
    def _validate_selection_bounds(self) -> "UserInteractionRequest":
        if self.fields:
            self.kind = "form" if len(self.fields) > 1 else ("input" if self.fields[0].selection_mode == "text" else "choice")
            first = self.fields[0]
            self.selection_mode = first.selection_mode
            self.options = list(first.options)
            self.min_selections = first.min_selections
            self.max_selections = first.max_selections
            self.allow_custom = first.allow_custom
            self.custom_label = first.custom_label
            self.placeholder = first.placeholder
            self.required = first.required
            return self
        if self.selection_mode == "text":
            self.kind = "input"
            self.options = []
            self.min_selections = 1 if self.required else 0
            self.max_selections = None
            return self
        if not self.options and not self.allow_custom:
            raise ValueError("choice interactions require options or allow_custom=true")
        if self.selection_mode == "single":
            self.max_selections = 1
            if self.required and self.min_selections == 0:
                self.min_selections = 1
        if self.max_selections is not None and self.min_selections > self.max_selections:
            raise ValueError("min_selections cannot exceed max_selections")
        if self.options and self.max_selections is not None and self.max_selections > len(self.options) and not self.allow_custom:
            self.max_selections = len(self.options)
        return self


class UserInteractionSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    selected_option_ids: list[str] = Field(default_factory=list)
    custom_response: str | None = None
    free_text: str | None = None
    field_responses: list["UserInteractionFieldResponse"] = Field(default_factory=list)


class UserInteractionFieldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    selected_option_ids: list[str] = Field(default_factory=list)
    custom_response: str | None = None
    free_text: str | None = None

    @field_validator("field_id")
    @classmethod
    def _field_id_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field_id must not be empty")
        return normalized


def build_user_interaction_request(args: dict[str, Any], *, request_id: str | None = None) -> UserInteractionRequest:
    question = str(args.get("question") or "").strip() or "More information is required before the runtime can continue."
    fields = _normalize_fields(args.get("fields"))
    normalized_options = _normalize_options(args.get("options"))
    if fields:
        first = fields[0]
        normalized_options = list(first.options)
    selection_mode = _normalize_selection_mode(
        args.get("selection_mode")
        or args.get("response_type")
        or args.get("input_type")
        or args.get("format"),
        has_options=bool(normalized_options),
    )
    if fields:
        selection_mode = fields[0].selection_mode
    required = bool(args.get("required", True))
    min_selections = _coerce_int(args.get("min_selections"), default=1 if required else 0)
    max_selections = _coerce_optional_int(args.get("max_selections"))
    if selection_mode == "single":
        max_selections = 1
    elif selection_mode == "multiple" and max_selections is None and normalized_options:
        max_selections = len(normalized_options)
    elif selection_mode == "text":
        max_selections = None
    return UserInteractionRequest(
        request_id=str(request_id or args.get("request_id") or _stable_request_id(question, normalized_options)),
        kind="input" if selection_mode == "text" else "choice",
        title=_optional_string(args.get("title")),
        question=question,
        description=_optional_string(args.get("description") or args.get("context")),
        selection_mode=selection_mode,
        options=normalized_options,
        min_selections=min_selections,
        max_selections=max_selections,
        allow_custom=bool(args.get("allow_custom", False)),
        custom_label=_optional_string(args.get("custom_label")),
        placeholder=_optional_string(args.get("placeholder")),
        required=required,
        source_tool_name="ask_clarification",
        fields=fields,
    )


def render_user_interaction_message(request: UserInteractionRequest) -> str:
    lines = [request.question]
    if request.description:
        lines.extend(["", request.description])
    if request.fields:
        lines.append("")
        lines.append("Fields:")
        for field in request.fields:
            lines.append(f"- {field.label} ({field.field_id}) [{field.selection_mode}]")
            if field.description:
                lines.append(f"  {field.description}")
            for index, option in enumerate(field.options, start=1):
                suffix = " (recommended)" if option.recommended else ""
                disabled = " [disabled]" if option.disabled else ""
                description = f" - {option.description}" if option.description else ""
                lines.append(f"  {index}. {option.label}{suffix}{disabled}{description}")
            if field.allow_custom:
                lines.append(f"  Custom response allowed: {field.custom_label or 'Other'}")
            if field.selection_mode == "text" and field.placeholder:
                lines.append(f"  Placeholder: {field.placeholder}")
    elif request.options:
        lines.append("")
        for index, option in enumerate(request.options, start=1):
            suffix = " (recommended)" if option.recommended else ""
            description = f" - {option.description}" if option.description else ""
            lines.append(f"{index}. {option.label}{suffix}{description}")
    if request.allow_custom:
        lines.append("")
        lines.append(f"Custom response allowed: {request.custom_label or 'Other'}")
    return "\n".join(lines)


def _normalize_fields(value: Any) -> list[UserInteractionField]:
    if not isinstance(value, list):
        return []
    result: list[UserInteractionField] = []
    used_ids: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("question") or item.get("title") or item.get("name") or item.get("id") or "").strip()
        if not label:
            continue
        field_id = _unique_id(str(item.get("field_id") or item.get("id") or item.get("name") or _slug(label) or f"field-{index}"), used_ids, index=index)
        selection_mode = _normalize_selection_mode(
            item.get("selection_mode") or item.get("response_type") or item.get("input_type") or item.get("format"),
            has_options=bool(item.get("options")),
        )
        required = bool(item.get("required", True))
        options = _normalize_options(item.get("options"))
        min_selections = _coerce_int(item.get("min_selections"), default=1 if required else 0)
        max_selections = _coerce_optional_int(item.get("max_selections"))
        if selection_mode == "single":
            max_selections = 1
        elif selection_mode == "multiple" and max_selections is None and options:
            max_selections = len(options)
        elif selection_mode == "text":
            max_selections = None
        field = UserInteractionField(
            field_id=field_id,
            label=label,
            description=_optional_string(item.get("description") or item.get("context") or item.get("detail")),
            selection_mode=selection_mode,
            options=options,
            min_selections=min_selections,
            max_selections=max_selections,
            allow_custom=bool(item.get("allow_custom", False)),
            custom_label=_optional_string(item.get("custom_label")),
            placeholder=_optional_string(item.get("placeholder")),
            required=required,
            metadata=dict(item.get("metadata") or {}),
        )
        used_ids.add(field.field_id)
        result.append(field)
    return result


def _normalize_selection_mode(value: Any, *, has_options: bool) -> SelectionMode:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"multiple", "multi", "multi_select", "multiselect", "checkbox", "checkboxes"}:
        return "multiple"
    if normalized in {"text", "free_text", "input", "textarea", "short_text"}:
        return "text"
    if normalized in {"single", "single_select", "radio", "select", "choice"}:
        return "single"
    return "single" if has_options else "text"


def _normalize_options(value: Any) -> list[UserInteractionOption]:
    if not isinstance(value, list):
        return []
    result: list[UserInteractionOption] = []
    used_ids: set[str] = set()
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("title") or item.get("value") or item.get("id") or "").strip()
            if not label:
                continue
            option_id = str(item.get("id") or item.get("value") or _slug(label) or f"option-{index}").strip()
            option = UserInteractionOption(
                id=_unique_id(option_id, used_ids, index=index),
                label=label,
                description=_optional_string(item.get("description") or item.get("detail")),
                recommended=bool(item.get("recommended", False)),
                disabled=bool(item.get("disabled", False)),
                metadata=dict(item.get("metadata") or {}),
            )
        else:
            label = str(item).strip()
            if not label:
                continue
            option = UserInteractionOption(id=_unique_id(_slug(label) or f"option-{index}", used_ids, index=index), label=label)
        used_ids.add(option.id)
        result.append(option)
    return result


def _unique_id(value: str, used_ids: set[str], *, index: int) -> str:
    normalized = _slug(value) or f"option-{index}"
    if normalized not in used_ids:
        return normalized
    suffix = 2
    while f"{normalized}-{suffix}" in used_ids:
        suffix += 1
    return f"{normalized}-{suffix}"


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return normalized[:64]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stable_request_id(question: str, options: list[UserInteractionOption]) -> str:
    seed = question + "|" + "|".join(option.id for option in options)
    return f"interaction-{sha1(seed.encode('utf-8')).hexdigest()[:12]}"
