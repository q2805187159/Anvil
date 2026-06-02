from __future__ import annotations

import base64
from copy import deepcopy
from email.message import EmailMessage
import html
import json
import os
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from anvil.config import ConfigResolutionResult


DEFAULT_GMAIL_BASE_URL = "https://gmail.googleapis.com"
DEFAULT_CALENDAR_BASE_URL = "https://www.googleapis.com"
DEFAULT_GMAIL_USER_ID = "me"
DEFAULT_CALENDAR_ID = "primary"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_GMAIL_READ_CHARS = 12000
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|ya29\.[A-Za-z0-9._-]{20,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})"
)
HTML_TAG_RE = re.compile(r"<[^>]+>")


class GoogleWorkspaceService:
    """Gmail and Calendar REST adapters with mockable, JSON-safe contracts."""

    def __init__(self) -> None:
        self._mock_sent: dict[str, list[dict[str, Any]]] = {}
        self._mock_drafts: dict[str, list[dict[str, Any]]] = {}
        self._mock_created_events: dict[str, list[dict[str, Any]]] = {}

    def gmail_search(
        self,
        *,
        config_result: ConfigResolutionResult,
        query: str = "",
        max_results: int = 10,
        label_ids: list[str] | None = None,
        include_spam_trash: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        max_results = _bounded_int(max_results, default=10, minimum=1, maximum=50)
        try:
            if _mock_enabled(settings):
                messages = self._mock_messages(config_result, settings)
                matches = _filter_mock_messages(messages, query=query, label_ids=label_ids)[:max_results]
                return {
                    "success": True,
                    "provider": "mock",
                    "query": query,
                    "messages": [_gmail_summary(item) for item in matches],
                    "total_results": len(matches),
                }
            gmail_user = _gmail_user_id(settings, user_id)
            params: dict[str, Any] = {"maxResults": max_results, "includeSpamTrash": str(bool(include_spam_trash)).lower()}
            if query:
                params["q"] = query
            if label_ids:
                params["labelIds"] = label_ids
            response = _request_json(
                settings=settings,
                service="gmail",
                method="GET",
                path=f"/gmail/v1/users/{urllib.parse.quote(gmail_user, safe='')}/messages",
                query=params,
            )
            messages = []
            for item in response.get("messages") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                detail = self._gmail_get_metadata(settings=settings, user_id=gmail_user, message_id=str(item["id"]))
                messages.append(_gmail_api_summary(detail))
            return {
                "success": True,
                "provider": "gmail",
                "query": query,
                "messages": messages,
                "total_results": len(messages),
                "next_page_token": response.get("nextPageToken"),
                "result_size_estimate": response.get("resultSizeEstimate"),
            }
        except Exception as exc:
            return _error_payload("gmail", exc)

    def gmail_read(
        self,
        *,
        config_result: ConfigResolutionResult,
        message_id: str,
        user_id: str | None = None,
        max_chars: int = DEFAULT_GMAIL_READ_CHARS,
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        max_chars = _bounded_int(max_chars, default=DEFAULT_GMAIL_READ_CHARS, minimum=500, maximum=50000)
        try:
            if _mock_enabled(settings):
                message = _find_mock_message(self._mock_messages(config_result, settings), message_id)
                if message is None:
                    return {"success": False, "provider": "mock", "error": f"message '{message_id}' not found"}
                return {"success": True, "provider": "mock", "message": _gmail_detail(message, max_chars=max_chars)}
            gmail_user = _gmail_user_id(settings, user_id)
            response = _request_json(
                settings=settings,
                service="gmail",
                method="GET",
                path=f"/gmail/v1/users/{urllib.parse.quote(gmail_user, safe='')}/messages/{urllib.parse.quote(message_id, safe='')}",
                query={"format": "full"},
            )
            return {"success": True, "provider": "gmail", "message": _gmail_api_detail(response, max_chars=max_chars)}
        except Exception as exc:
            return _error_payload("gmail", exc)

    def gmail_labels(self, *, config_result: ConfigResolutionResult, user_id: str | None = None) -> dict[str, Any]:
        settings = _settings(config_result)
        try:
            if _mock_enabled(settings):
                labels = settings.get("mock_gmail_labels")
                if not isinstance(labels, list):
                    ids = sorted({label for item in self._mock_messages(config_result, settings) for label in item.get("label_ids", [])})
                    labels = [{"id": label, "name": label} for label in ids]
                return {"success": True, "provider": "mock", "labels": labels}
            gmail_user = _gmail_user_id(settings, user_id)
            response = _request_json(
                settings=settings,
                service="gmail",
                method="GET",
                path=f"/gmail/v1/users/{urllib.parse.quote(gmail_user, safe='')}/labels",
            )
            return {"success": True, "provider": "gmail", "labels": response.get("labels") or []}
        except Exception as exc:
            return _error_payload("gmail", exc)

    def gmail_send(
        self,
        *,
        config_result: ConfigResolutionResult,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        try:
            raw = _email_raw(settings=settings, to=to, subject=subject, body=body, cc=cc, bcc=bcc)
            if _mock_enabled(settings):
                key = config_result.fingerprint
                sent = self._mock_sent.setdefault(key, [])
                message = {
                    "id": f"sent_{len(sent) + 1}",
                    "thread_id": f"thread_sent_{len(sent) + 1}",
                    "to": to,
                    "cc": cc or "",
                    "bcc": bcc or "",
                    "subject": subject,
                    "body": body,
                    "label_ids": ["SENT"],
                }
                sent.append(message)
                return {"success": True, "provider": "mock", "message": _gmail_summary(message)}
            gmail_user = _gmail_user_id(settings, user_id)
            response = _request_json(
                settings=settings,
                service="gmail",
                method="POST",
                path=f"/gmail/v1/users/{urllib.parse.quote(gmail_user, safe='')}/messages/send",
                payload={"raw": raw},
            )
            return {"success": True, "provider": "gmail", "message": _scrub_data(response)}
        except Exception as exc:
            return _error_payload("gmail", exc)

    def gmail_create_draft(
        self,
        *,
        config_result: ConfigResolutionResult,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        try:
            raw = _email_raw(settings=settings, to=to, subject=subject, body=body, cc=cc, bcc=bcc)
            if _mock_enabled(settings):
                key = config_result.fingerprint
                drafts = self._mock_drafts.setdefault(key, [])
                draft = {
                    "id": f"draft_{len(drafts) + 1}",
                    "message": {
                        "id": f"draft_message_{len(drafts) + 1}",
                        "to": to,
                        "cc": cc or "",
                        "bcc": bcc or "",
                        "subject": subject,
                        "body": body,
                        "label_ids": ["DRAFT"],
                    },
                }
                drafts.append(draft)
                return {"success": True, "provider": "mock", "draft": _gmail_draft_summary(draft)}
            gmail_user = _gmail_user_id(settings, user_id)
            response = _request_json(
                settings=settings,
                service="gmail",
                method="POST",
                path=f"/gmail/v1/users/{urllib.parse.quote(gmail_user, safe='')}/drafts",
                payload={"message": {"raw": raw}},
            )
            return {"success": True, "provider": "gmail", "draft": _scrub_data(response)}
        except Exception as exc:
            return _error_payload("gmail", exc)

    def calendar_list_events(
        self,
        *,
        config_result: ConfigResolutionResult,
        calendar_id: str | None = None,
        time_min: str | None = None,
        time_max: str | None = None,
        query: str | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        max_results = _bounded_int(max_results, default=10, minimum=1, maximum=100)
        effective_calendar_id = _calendar_id(settings, calendar_id)
        try:
            if _mock_enabled(settings):
                events = _filter_mock_events(
                    self._mock_events(config_result, settings),
                    calendar_id=effective_calendar_id,
                    time_min=time_min,
                    time_max=time_max,
                    query=query,
                )[:max_results]
                return {"success": True, "provider": "mock", "calendar_id": effective_calendar_id, "events": [_event_summary(item) for item in events], "total_results": len(events)}
            params: dict[str, Any] = {"singleEvents": "true", "orderBy": "startTime", "maxResults": max_results}
            if time_min:
                params["timeMin"] = time_min
            if time_max:
                params["timeMax"] = time_max
            if query:
                params["q"] = query
            response = _request_json(
                settings=settings,
                service="calendar",
                method="GET",
                path=f"/calendar/v3/calendars/{urllib.parse.quote(effective_calendar_id, safe='')}/events",
                query=params,
            )
            return {"success": True, "provider": "calendar", "calendar_id": effective_calendar_id, "events": [_event_summary(item) for item in response.get("items") or []], "next_page_token": response.get("nextPageToken")}
        except Exception as exc:
            return _error_payload("calendar", exc)

    def calendar_create_event(
        self,
        *,
        config_result: ConfigResolutionResult,
        summary: str,
        start: str,
        end: str,
        calendar_id: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        time_zone: str | None = None,
        send_updates: str = "none",
        create_meet_link: bool = False,
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        effective_calendar_id = _calendar_id(settings, calendar_id)
        try:
            event = _event_payload(
                summary=summary,
                start=start,
                end=end,
                description=description,
                location=location,
                attendees=attendees,
                time_zone=time_zone,
                create_meet_link=create_meet_link,
            )
            if _mock_enabled(settings):
                event = deepcopy(event)
                event.setdefault("id", f"event_{uuid4().hex[:10]}")
                event["calendar_id"] = effective_calendar_id
                self._mock_created_events.setdefault(config_result.fingerprint, []).append(event)
                return {"success": True, "provider": "mock", "calendar_id": effective_calendar_id, "event": _event_summary(event)}
            query = {"sendUpdates": _send_updates(send_updates)}
            if create_meet_link:
                query["conferenceDataVersion"] = 1
            response = _request_json(
                settings=settings,
                service="calendar",
                method="POST",
                path=f"/calendar/v3/calendars/{urllib.parse.quote(effective_calendar_id, safe='')}/events",
                query=query,
                payload=event,
            )
            return {"success": True, "provider": "calendar", "calendar_id": effective_calendar_id, "event": _event_summary(response)}
        except Exception as exc:
            return _error_payload("calendar", exc)

    def calendar_update_event(
        self,
        *,
        config_result: ConfigResolutionResult,
        event_id: str,
        calendar_id: str | None = None,
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        time_zone: str | None = None,
        status: str | None = None,
        send_updates: str = "none",
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        effective_calendar_id = _calendar_id(settings, calendar_id)
        try:
            patch = _event_patch(
                summary=summary,
                start=start,
                end=end,
                description=description,
                location=location,
                attendees=attendees,
                time_zone=time_zone,
                status=status,
            )
            if not patch:
                return {"success": False, "provider": "calendar", "error": "no event fields were provided for update"}
            if _mock_enabled(settings):
                event = _find_mock_event(self._mock_events(config_result, settings), event_id, effective_calendar_id)
                if event is None:
                    return {"success": False, "provider": "mock", "error": f"event '{event_id}' not found"}
                _merge_event_patch(event, patch)
                return {"success": True, "provider": "mock", "calendar_id": effective_calendar_id, "event": _event_summary(event)}
            response = _request_json(
                settings=settings,
                service="calendar",
                method="PATCH",
                path=f"/calendar/v3/calendars/{urllib.parse.quote(effective_calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
                query={"sendUpdates": _send_updates(send_updates)},
                payload=patch,
            )
            return {"success": True, "provider": "calendar", "calendar_id": effective_calendar_id, "event": _event_summary(response)}
        except Exception as exc:
            return _error_payload("calendar", exc)

    def calendar_delete_event(
        self,
        *,
        config_result: ConfigResolutionResult,
        event_id: str,
        calendar_id: str | None = None,
        send_updates: str = "none",
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        effective_calendar_id = _calendar_id(settings, calendar_id)
        try:
            if _mock_enabled(settings):
                events = self._mock_events(config_result, settings)
                before = len(events)
                events[:] = [item for item in events if not (str(item.get("id")) == str(event_id) and str(item.get("calendar_id") or effective_calendar_id) == effective_calendar_id)]
                return {"success": before != len(events), "provider": "mock", "calendar_id": effective_calendar_id, "event_id": event_id}
            _request_json(
                settings=settings,
                service="calendar",
                method="DELETE",
                path=f"/calendar/v3/calendars/{urllib.parse.quote(effective_calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
                query={"sendUpdates": _send_updates(send_updates)},
            )
            return {"success": True, "provider": "calendar", "calendar_id": effective_calendar_id, "event_id": event_id}
        except Exception as exc:
            return _error_payload("calendar", exc)

    def calendar_free_busy(
        self,
        *,
        config_result: ConfigResolutionResult,
        time_min: str,
        time_max: str,
        calendar_ids: list[str] | None = None,
        time_zone: str | None = None,
    ) -> dict[str, Any]:
        settings = _settings(config_result)
        calendars = calendar_ids or [_calendar_id(settings, None)]
        try:
            if _mock_enabled(settings):
                busy = {}
                for calendar_id in calendars:
                    ranges = []
                    for event in _filter_mock_events(self._mock_events(config_result, settings), calendar_id=calendar_id, time_min=time_min, time_max=time_max):
                        ranges.append({"start": _event_time(event, "start"), "end": _event_time(event, "end")})
                    busy[calendar_id] = {"busy": ranges}
                return {"success": True, "provider": "mock", "time_min": time_min, "time_max": time_max, "calendars": busy}
            response = _request_json(
                settings=settings,
                service="calendar",
                method="POST",
                path="/calendar/v3/freeBusy",
                payload={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    **({"timeZone": time_zone} if time_zone else {}),
                    "items": [{"id": item} for item in calendars],
                },
            )
            return {"success": True, "provider": "calendar", **_scrub_data(response)}
        except Exception as exc:
            return _error_payload("calendar", exc)

    def _gmail_get_metadata(self, *, settings: dict[str, Any], user_id: str, message_id: str) -> dict[str, Any]:
        return _request_json(
            settings=settings,
            service="gmail",
            method="GET",
            path=f"/gmail/v1/users/{urllib.parse.quote(user_id, safe='')}/messages/{urllib.parse.quote(message_id, safe='')}",
            query={
                "format": "metadata",
                "metadataHeaders": ["Subject", "From", "To", "Date"],
            },
        )

    def _mock_messages(self, config_result: ConfigResolutionResult, settings: dict[str, Any]) -> list[dict[str, Any]]:
        messages = [dict(item) for item in settings.get("mock_gmail_messages") or [] if isinstance(item, dict)]
        messages.extend(self._mock_sent.get(config_result.fingerprint, []))
        for draft in self._mock_drafts.get(config_result.fingerprint, []):
            message = draft.get("message")
            if isinstance(message, dict):
                messages.append(dict(message))
        return messages

    def _mock_events(self, config_result: ConfigResolutionResult, settings: dict[str, Any]) -> list[dict[str, Any]]:
        if "_mock_calendar_events_runtime" not in settings:
            settings["_mock_calendar_events_runtime"] = [deepcopy(item) for item in settings.get("mock_calendar_events") or [] if isinstance(item, dict)]
        events = settings["_mock_calendar_events_runtime"]
        if not isinstance(events, list):
            events = []
            settings["_mock_calendar_events_runtime"] = events
        events.extend(item for item in self._mock_created_events.get(config_result.fingerprint, []) if item not in events)
        return events


def _settings(config_result: ConfigResolutionResult) -> dict[str, Any]:
    raw = (
        config_result.effective_config.additional_settings.get("google_workspace")
        or config_result.effective_config.additional_settings.get("workspace_tools")
        or config_result.effective_config.additional_settings.get("google")
        or {}
    )
    return dict(raw) if isinstance(raw, dict) else {}


def _mock_enabled(settings: dict[str, Any]) -> bool:
    provider = str(settings.get("provider") or "").strip().lower()
    return provider == "mock" or settings.get("mock_gmail_messages") is not None or settings.get("mock_calendar_events") is not None


def _gmail_user_id(settings: dict[str, Any], user_id: str | None) -> str:
    return str(user_id or settings.get("gmail_user_id") or os.getenv("GOOGLE_GMAIL_USER_ID") or DEFAULT_GMAIL_USER_ID).strip() or DEFAULT_GMAIL_USER_ID


def _calendar_id(settings: dict[str, Any], calendar_id: str | None) -> str:
    return str(calendar_id or settings.get("calendar_id") or os.getenv("GOOGLE_CALENDAR_ID") or DEFAULT_CALENDAR_ID).strip() or DEFAULT_CALENDAR_ID


def _request_json(
    *,
    settings: dict[str, Any],
    service: str,
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url = _base_url(settings, service)
    query_items: list[tuple[str, str]] = []
    for key, value in (query or {}).items():
        if value is None:
            continue
        if isinstance(value, list):
            query_items.extend((key, str(item)) for item in value)
        else:
            query_items.append((key, str(value)))
    api_key = _resolve_secret(settings.get("api_key") or "$GOOGLE_API_KEY")
    if api_key:
        query_items.append(("key", api_key))
    url = base_url.rstrip("/") + path
    if query_items:
        url += "?" + urllib.parse.urlencode(query_items)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "User-Agent": "AnvilGoogleWorkspaceTools/1.0",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    token = _resolve_secret(settings.get("access_token") or settings.get("oauth_access_token") or "$GOOGLE_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if not token and not api_key:
        raise ValueError("GOOGLE_ACCESS_TOKEN or GOOGLE_API_KEY is not configured")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_timeout(settings)) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {_scrub_text(body or exc.reason)}") from exc
    if not body.strip():
        return {}
    return _scrub_data(json.loads(body))


def _base_url(settings: dict[str, Any], service: str) -> str:
    if service == "gmail":
        return _resolve_secret(settings.get("gmail_base_url") or "$GOOGLE_GMAIL_BASE_URL") or DEFAULT_GMAIL_BASE_URL
    return _resolve_secret(settings.get("calendar_base_url") or "$GOOGLE_CALENDAR_BASE_URL") or DEFAULT_CALENDAR_BASE_URL


def _timeout(settings: dict[str, Any]) -> int:
    return _bounded_int(settings.get("timeout_seconds"), default=DEFAULT_TIMEOUT_SECONDS, minimum=1, maximum=120)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _filter_mock_messages(messages: list[dict[str, Any]], *, query: str, label_ids: list[str] | None) -> list[dict[str, Any]]:
    terms = str(query or "").lower().split()
    labels = {str(item) for item in (label_ids or [])}
    result = []
    for item in messages:
        item_labels = {str(label) for label in item.get("label_ids", [])}
        if labels and not labels.issubset(item_labels):
            continue
        haystack = " ".join(str(item.get(key) or "") for key in ("subject", "from", "to", "snippet", "body")).lower()
        if terms and not all(term in haystack for term in terms):
            continue
        result.append(item)
    return result


def _find_mock_message(messages: list[dict[str, Any]], message_id: str) -> dict[str, Any] | None:
    return next((item for item in messages if str(item.get("id")) == str(message_id)), None)


def _gmail_summary(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(message.get("id") or ""),
        "thread_id": str(message.get("thread_id") or message.get("threadId") or ""),
        "subject": str(message.get("subject") or ""),
        "from": str(message.get("from") or ""),
        "to": str(message.get("to") or ""),
        "date": str(message.get("date") or ""),
        "snippet": _scrub_text(str(message.get("snippet") or message.get("body") or ""))[:500],
        "label_ids": list(message.get("label_ids") or message.get("labelIds") or []),
    }


def _gmail_detail(message: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    detail = _gmail_summary(message)
    body = str(message.get("body") or message.get("text") or "")
    detail["body"] = _scrub_text(body[:max_chars])
    detail["truncated"] = len(body) > max_chars
    return detail


def _gmail_api_summary(message: dict[str, Any]) -> dict[str, Any]:
    headers = _gmail_headers(message)
    return {
        "id": str(message.get("id") or ""),
        "thread_id": str(message.get("threadId") or ""),
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "snippet": _scrub_text(str(message.get("snippet") or ""))[:500],
        "label_ids": list(message.get("labelIds") or []),
    }


def _gmail_api_detail(message: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    detail = _gmail_api_summary(message)
    body = _extract_gmail_body(message.get("payload") or {})
    detail["body"] = _scrub_text(body[:max_chars])
    detail["truncated"] = len(body) > max_chars
    return detail


def _gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    headers = {}
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    for item in payload.get("headers") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        if name:
            headers[name] = _scrub_text(str(item.get("value") or ""))
    return headers


def _extract_gmail_body(payload: dict[str, Any]) -> str:
    candidates: list[tuple[int, str]] = []

    def visit(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType") or "")
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        data = body.get("data")
        if isinstance(data, str) and data:
            text = _decode_base64url(data)
            if mime_type == "text/plain":
                candidates.append((0, text))
            elif mime_type == "text/html":
                candidates.append((1, _html_to_text(text)))
            else:
                candidates.append((2, text))
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                visit(child)

    visit(payload)
    candidates.sort(key=lambda item: item[0])
    return "\n\n".join(text for _, text in candidates if text).strip()


def _decode_base64url(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _html_to_text(value: str) -> str:
    return html.unescape(HTML_TAG_RE.sub(" ", value)).replace("\xa0", " ").strip()


def _email_raw(*, settings: dict[str, Any], to: str, subject: str, body: str, cc: str | None, bcc: str | None) -> str:
    if not str(to or "").strip():
        raise ValueError("to is required")
    message = EmailMessage()
    sender = str(settings.get("gmail_from") or os.getenv("GOOGLE_GMAIL_FROM") or "").strip()
    if sender:
        message["From"] = sender
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


def _gmail_draft_summary(draft: dict[str, Any]) -> dict[str, Any]:
    message = draft.get("message") if isinstance(draft.get("message"), dict) else {}
    return {"id": draft.get("id"), "message": _gmail_summary(message)}


def _event_payload(
    *,
    summary: str,
    start: str,
    end: str,
    description: str | None,
    location: str | None,
    attendees: list[str] | None,
    time_zone: str | None,
    create_meet_link: bool,
) -> dict[str, Any]:
    if not str(summary or "").strip():
        raise ValueError("summary is required")
    if not str(start or "").strip() or not str(end or "").strip():
        raise ValueError("start and end are required")
    payload: dict[str, Any] = {
        "summary": summary,
        "start": _calendar_time(start, time_zone),
        "end": _calendar_time(end, time_zone),
    }
    if description:
        payload["description"] = description
    if location:
        payload["location"] = location
    if attendees:
        payload["attendees"] = [{"email": item} for item in attendees if str(item).strip()]
    if create_meet_link:
        payload["conferenceData"] = {"createRequest": {"requestId": uuid4().hex}}
    return payload


def _event_patch(
    *,
    summary: str | None,
    start: str | None,
    end: str | None,
    description: str | None,
    location: str | None,
    attendees: list[str] | None,
    time_zone: str | None,
    status: str | None,
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if summary is not None:
        patch["summary"] = summary
    if start is not None:
        patch["start"] = _calendar_time(start, time_zone)
    if end is not None:
        patch["end"] = _calendar_time(end, time_zone)
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if attendees is not None:
        patch["attendees"] = [{"email": item} for item in attendees if str(item).strip()]
    if status is not None:
        patch["status"] = status
    return patch


def _calendar_time(value: str, time_zone: str | None) -> dict[str, str]:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return {"date": text}
    payload = {"dateTime": text}
    if time_zone:
        payload["timeZone"] = time_zone
    return payload


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(event.get("id") or ""),
        "calendar_id": str(event.get("calendar_id") or event.get("calendarId") or ""),
        "summary": _scrub_text(str(event.get("summary") or "")),
        "description": _scrub_text(str(event.get("description") or ""))[:1000],
        "location": str(event.get("location") or ""),
        "start": _event_time(event, "start"),
        "end": _event_time(event, "end"),
        "status": str(event.get("status") or ""),
        "html_link": str(event.get("htmlLink") or ""),
        "attendees": [
            {"email": str(item.get("email") or ""), "response_status": str(item.get("responseStatus") or "")}
            for item in event.get("attendees") or []
            if isinstance(item, dict)
        ],
    }


def _event_time(event: dict[str, Any], key: str) -> str:
    value = event.get(key)
    if isinstance(value, dict):
        return str(value.get("dateTime") or value.get("date") or "")
    return str(value or "")


def _filter_mock_events(
    events: list[dict[str, Any]],
    *,
    calendar_id: str,
    time_min: str | None,
    time_max: str | None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    result = []
    query_text = str(query or "").lower().strip()
    for event in events:
        item_calendar_id = str(event.get("calendar_id") or event.get("calendarId") or calendar_id)
        if item_calendar_id != calendar_id:
            continue
        start = _event_time(event, "start")
        end = _event_time(event, "end")
        if time_min and end and end < time_min:
            continue
        if time_max and start and start > time_max:
            continue
        if query_text:
            haystack = " ".join(str(event.get(key) or "") for key in ("summary", "description", "location")).lower()
            if query_text not in haystack:
                continue
        result.append(event)
    return sorted(result, key=lambda item: _event_time(item, "start"))


def _find_mock_event(events: list[dict[str, Any]], event_id: str, calendar_id: str) -> dict[str, Any] | None:
    return next((item for item in events if str(item.get("id")) == str(event_id) and str(item.get("calendar_id") or calendar_id) == calendar_id), None)


def _merge_event_patch(event: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        event[key] = value


def _send_updates(value: str) -> str:
    normalized = str(value or "none").strip().lower()
    return normalized if normalized in {"all", "externalonly", "none"} else "none"


def _resolve_secret(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1], "")
    if text.startswith("$"):
        return os.getenv(text[1:], "")
    return text


def _scrub_text(value: str) -> str:
    return SECRET_VALUE_RE.sub("[REDACTED]", value)


def _scrub_data(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, list):
        return [_scrub_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _scrub_data(item) for key, item in value.items()}
    return value


def _error_payload(provider: str, exc: Exception) -> dict[str, Any]:
    return {"success": False, "provider": provider, "error": _scrub_text(str(exc))}
