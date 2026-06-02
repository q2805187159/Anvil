from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4
import zlib

from anvil.config import ConfigResolutionResult


SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})"
)
PRIVATE_HOST_RE = re.compile(
    r"^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[0-1])\.|192\.168\.|0\.0\.0\.0|::1$)",
    re.IGNORECASE,
)
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_SNAPSHOT_CHARS = 12000
DEFAULT_NAVIGATION_WAIT_MS = 400
SUPPORTED_SCREENSHOT_FORMATS = {"png", "jpeg"}
PIXEL_DIFF_GRID_COLUMNS = 16
PIXEL_DIFF_GRID_ROWS = 10
PIXEL_DIFF_MAX_PIXELS = 8_000_000
PIXEL_DIFF_TOP_CELL_LIMIT = 24
PIXEL_DIFF_CHANNEL_THRESHOLD = 8


class BrowserToolsService:
    """Browser automation facade with mock and Chrome DevTools Protocol backends."""

    def __init__(self) -> None:
        self._cdp_sessions: dict[str, dict[str, Any]] = {}
        self._mock_sessions: dict[str, dict[str, Any]] = {}
        self._rpc_id = 0

    def default_screenshot_virtual_path(self, *, image_format: str = "png") -> str:
        normalized = _normalize_screenshot_format(image_format)
        extension = "jpg" if normalized == "jpeg" else normalized
        return f"/mnt/user-data/outputs/browser/screenshot_{uuid4().hex[:12]}.{extension}"

    def default_report_screenshot_virtual_path(self, *, report_id: str | None = None, image_format: str = "png") -> str:
        normalized = _normalize_screenshot_format(image_format)
        extension = "jpg" if normalized == "jpeg" else normalized
        slug = _safe_output_segment(report_id or uuid4().hex[:12])
        return f"/mnt/user-data/outputs/presentation-browser-evidence/{slug}/screenshot.{extension}"

    def default_report_diff_virtual_paths(self, *, report_id: str | None = None, image_format: str = "png") -> dict[str, str]:
        normalized = _normalize_screenshot_format(image_format)
        extension = "jpg" if normalized == "jpeg" else normalized
        slug = _safe_output_segment(report_id or uuid4().hex[:12])
        root = f"/mnt/user-data/outputs/presentation-browser-diffs/{slug}"
        return {
            "baseline": f"{root}/baseline.{extension}",
            "candidate": f"{root}/candidate.{extension}",
            "overlay": f"{root}/overlay.png",
            "manifest": f"{root}/manifest.json",
        }

    def navigate(self, *, config_result: ConfigResolutionResult, session_id: str, url: str) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        try:
            normalized_url = _validate_url(url, allow_private=_allow_private_urls(settings))
        except ValueError as exc:
            return {"success": False, "provider": provider, "session_id": session_id, "error": _scrub_text(str(exc))}
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            page = _mock_page(settings, normalized_url)
            history = list(session.get("history") or [])
            if session.get("url"):
                history.append(session["url"])
            session.update(
                {
                    "url": normalized_url,
                    "title": page.get("title") or _mock_title(normalized_url),
                    "snapshot": page.get("snapshot") or _mock_snapshot(normalized_url),
                    "images": list(page.get("images") or session.get("images") or []),
                    "console_messages": list(page.get("console_messages") or session.get("console_messages") or []),
                    "eval_results": dict(page.get("eval_results") or session.get("eval_results") or {}),
                    "screenshot_base64": page.get("screenshot_base64") or session.get("screenshot_base64") or "",
                    "screenshot_jpeg_base64": page.get("screenshot_jpeg_base64") or session.get("screenshot_jpeg_base64") or "",
                    "history": history,
                }
            )
            return self._mock_page_payload(session=session, session_id=session_id, provider=provider)
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id)
            self._cdp_call(settings=settings, tab=tab, method="Page.navigate", params={"url": normalized_url})
            _sleep_after_navigation(settings)
            self._install_console_capture(settings=settings, tab=tab)
            title = self._cdp_eval(settings=settings, tab=tab, expression="document.title")
            final_url = self._cdp_eval(settings=settings, tab=tab, expression="location.href")
            snapshot = self._cdp_snapshot(settings=settings, tab=tab)
            return {
                "success": True,
                "provider": provider,
                "session_id": session_id,
                "url": _scrub_text(str(final_url or normalized_url)),
                "title": _scrub_text(str(title or "")),
                "snapshot": _limit_text(snapshot, _max_snapshot_chars(settings)),
            }
        return _unsupported_provider(provider)

    def snapshot(self, *, config_result: ConfigResolutionResult, session_id: str, full: bool = False) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        max_chars = _max_snapshot_chars(settings) * (2 if full else 1)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            snapshot = str(session.get("snapshot") or _mock_snapshot(str(session.get("url") or "about:blank")))
            payload = self._mock_page_payload(session=session, session_id=session_id, provider=provider)
            payload["snapshot"] = _limit_text(snapshot, max_chars)
            payload["truncated"] = len(snapshot) > max_chars
            return payload
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            self._install_console_capture(settings=settings, tab=tab)
            snapshot = self._cdp_snapshot(settings=settings, tab=tab)
            title = self._cdp_eval(settings=settings, tab=tab, expression="document.title")
            url = self._cdp_eval(settings=settings, tab=tab, expression="location.href")
            return {
                "success": True,
                "provider": provider,
                "session_id": session_id,
                "url": _scrub_text(str(url or "")),
                "title": _scrub_text(str(title or "")),
                "snapshot": _limit_text(snapshot, max_chars),
                "truncated": len(snapshot) > max_chars,
            }
        return _unsupported_provider(provider)

    def click(self, *, config_result: ConfigResolutionResult, session_id: str, ref: str) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        normalized_ref = _normalize_ref(ref)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            session.setdefault("events", []).append({"action": "click", "ref": normalized_ref})
            return {"success": True, "provider": provider, "session_id": session_id, "clicked": normalized_ref}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            selector = _selector_from_ref(normalized_ref)
            result = self._cdp_eval(
                settings=settings,
                tab=tab,
                expression=_dom_action_script(selector, "click"),
                return_by_value=True,
            )
            if isinstance(result, dict) and result.get("ok") is False:
                return {"success": False, "provider": provider, "session_id": session_id, "error": str(result.get("error") or "click failed"), "ref": normalized_ref}
            return {"success": True, "provider": provider, "session_id": session_id, "clicked": normalized_ref}
        return _unsupported_provider(provider)

    def type_text(self, *, config_result: ConfigResolutionResult, session_id: str, ref: str, text: str) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        normalized_ref = _normalize_ref(ref)
        normalized_text = str(text or "")
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            session.setdefault("events", []).append({"action": "type", "ref": normalized_ref, "text": normalized_text})
            return {"success": True, "provider": provider, "session_id": session_id, "typed": normalized_text, "element": normalized_ref}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            selector = _selector_from_ref(normalized_ref)
            result = self._cdp_eval(
                settings=settings,
                tab=tab,
                expression=_dom_action_script(selector, "type", text=normalized_text),
                return_by_value=True,
            )
            if isinstance(result, dict) and result.get("ok") is False:
                return {"success": False, "provider": provider, "session_id": session_id, "error": str(result.get("error") or "type failed"), "ref": normalized_ref}
            return {"success": True, "provider": provider, "session_id": session_id, "typed": normalized_text, "element": normalized_ref}
        return _unsupported_provider(provider)

    def scroll(self, *, config_result: ConfigResolutionResult, session_id: str, direction: str) -> dict[str, Any]:
        normalized_direction = str(direction or "").strip().lower()
        if normalized_direction not in {"up", "down"}:
            return {"success": False, "error": "direction must be 'up' or 'down'"}
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            session.setdefault("events", []).append({"action": "scroll", "direction": normalized_direction})
            return {"success": True, "provider": provider, "session_id": session_id, "scrolled": normalized_direction}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            delta = 650 if normalized_direction == "down" else -650
            self._cdp_eval(settings=settings, tab=tab, expression=f"window.scrollBy(0, {delta})")
            return {"success": True, "provider": provider, "session_id": session_id, "scrolled": normalized_direction}
        return _unsupported_provider(provider)

    def back(self, *, config_result: ConfigResolutionResult, session_id: str) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            history = list(session.get("history") or [])
            if history:
                session["url"] = history.pop()
                session["history"] = history
            session.setdefault("events", []).append({"action": "back"})
            return {"success": True, "provider": provider, "session_id": session_id, "url": session.get("url", "")}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            self._cdp_eval(settings=settings, tab=tab, expression="history.back()")
            _sleep_after_navigation(settings)
            url = self._cdp_eval(settings=settings, tab=tab, expression="location.href")
            return {"success": True, "provider": provider, "session_id": session_id, "url": _scrub_text(str(url or ""))}
        return _unsupported_provider(provider)

    def press(self, *, config_result: ConfigResolutionResult, session_id: str, key: str) -> dict[str, Any]:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return {"success": False, "error": "key is required"}
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            session.setdefault("events", []).append({"action": "press", "key": normalized_key})
            return {"success": True, "provider": provider, "session_id": session_id, "pressed": normalized_key}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            self._cdp_call(settings=settings, tab=tab, method="Input.dispatchKeyEvent", params={"type": "keyDown", "key": normalized_key})
            self._cdp_call(settings=settings, tab=tab, method="Input.dispatchKeyEvent", params={"type": "keyUp", "key": normalized_key})
            return {"success": True, "provider": provider, "session_id": session_id, "pressed": normalized_key}
        return _unsupported_provider(provider)

    def console(
        self,
        *,
        config_result: ConfigResolutionResult,
        session_id: str,
        clear: bool = False,
        expression: str | None = None,
    ) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            if expression:
                eval_results = session.get("eval_results") if isinstance(session.get("eval_results"), dict) else {}
                result = eval_results.get(expression, "")
                return {"success": True, "provider": provider, "session_id": session_id, "result": _scrub_data(result), "result_type": type(result).__name__}
            messages = list(session.get("console_messages") or [])
            if clear:
                session["console_messages"] = []
            return {"success": True, "provider": provider, "session_id": session_id, "console_messages": _scrub_data(messages), "js_errors": [], "total_messages": len(messages), "total_errors": 0}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            self._install_console_capture(settings=settings, tab=tab)
            if expression:
                result = self._cdp_eval(settings=settings, tab=tab, expression=expression, return_by_value=True)
                return {"success": True, "provider": provider, "session_id": session_id, "result": _scrub_data(result), "result_type": type(result).__name__}
            messages = self._cdp_eval(settings=settings, tab=tab, expression="window.__anvilConsoleMessages || []", return_by_value=True)
            if clear:
                self._cdp_eval(settings=settings, tab=tab, expression="window.__anvilConsoleMessages = []")
            if not isinstance(messages, list):
                messages = []
            js_errors = [item for item in messages if isinstance(item, dict) and item.get("level") in {"error", "exception"}]
            return {
                "success": True,
                "provider": provider,
                "session_id": session_id,
                "console_messages": _scrub_data(messages),
                "js_errors": _scrub_data(js_errors),
                "total_messages": len(messages),
                "total_errors": len(js_errors),
            }
        return _unsupported_provider(provider)

    def get_images(self, *, config_result: ConfigResolutionResult, session_id: str) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            images = list(session.get("images") or [])
            return {"success": True, "provider": provider, "session_id": session_id, "images": _scrub_data(images), "count": len(images)}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            images = self._cdp_eval(
                settings=settings,
                tab=tab,
                expression=(
                    "[...document.images].map((img, index) => ({"
                    "ref: `@img${index + 1}`, src: img.currentSrc || img.src, alt: img.alt || '', "
                    "width: img.naturalWidth || img.width || 0, height: img.naturalHeight || img.height || 0"
                    "})).filter(img => img.src && !img.src.startsWith('data:'))"
                ),
                return_by_value=True,
            )
            if not isinstance(images, list):
                images = []
            return {"success": True, "provider": provider, "session_id": session_id, "images": _scrub_data(images), "count": len(images)}
        return _unsupported_provider(provider)

    def screenshot(
        self,
        *,
        config_result: ConfigResolutionResult,
        session_id: str,
        output_path: Path,
        output_virtual_path: str,
        full_page: bool = True,
        format: str = "png",
    ) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        normalized_format = _normalize_screenshot_format(format)
        if provider == "mock":
            output_path.parent.mkdir(parents=True, exist_ok=True)
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            output_path.write_bytes(_mock_screenshot_bytes(session=session, image_format=normalized_format))
            return {"success": True, "provider": provider, "session_id": session_id, "output_path": output_virtual_path, "format": normalized_format, "bytes": output_path.stat().st_size}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            payload = self._cdp_call(
                settings=settings,
                tab=tab,
                method="Page.captureScreenshot",
                params={"format": normalized_format, "captureBeyondViewport": bool(full_page), "fromSurface": True},
            )
            data = (payload.get("result") or {}).get("data")
            if not isinstance(data, str) or not data:
                return {"success": False, "provider": provider, "session_id": session_id, "error": "CDP screenshot returned no data"}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(base64.b64decode(data))
            return {"success": True, "provider": provider, "session_id": session_id, "output_path": output_virtual_path, "format": normalized_format, "bytes": output_path.stat().st_size}
        return _unsupported_provider(provider)

    def vision(
        self,
        *,
        config_result: ConfigResolutionResult,
        session_id: str,
        output_path: Path,
        output_virtual_path: str,
        question: str | None = None,
        full_page: bool = True,
    ) -> dict[str, Any]:
        screenshot_payload = self.screenshot(
            config_result=config_result,
            session_id=session_id,
            output_path=output_path,
            output_virtual_path=output_virtual_path,
            full_page=full_page,
            format="png",
        )
        if not screenshot_payload.get("success"):
            return screenshot_payload
        snapshot_payload = self.snapshot(config_result=config_result, session_id=session_id, full=False)
        return {
            **screenshot_payload,
            "question": question or "",
            "snapshot": snapshot_payload.get("snapshot", ""),
            "analysis": "Screenshot captured; use the returned artifact and snapshot for visual reasoning.",
        }

    def report_snapshot(
        self,
        *,
        config_result: ConfigResolutionResult,
        session_id: str,
        report_url: str,
        output_path: Path,
        output_virtual_path: str,
        full_page: bool = True,
        format: str = "png",
    ) -> dict[str, Any]:
        navigation = self.navigate(config_result=config_result, session_id=session_id, url=report_url)
        if not navigation.get("success"):
            return {
                "success": False,
                "provider": navigation.get("provider") or _provider(_browser_settings(config_result)),
                "session_id": session_id,
                "report_url": _scrub_text(report_url),
                "error": navigation.get("error") or "browser navigation failed",
                "navigation": navigation,
            }
        screenshot_payload = self.screenshot(
            config_result=config_result,
            session_id=session_id,
            output_path=output_path,
            output_virtual_path=output_virtual_path,
            full_page=full_page,
            format=format,
        )
        if not screenshot_payload.get("success"):
            return {
                **screenshot_payload,
                "report_url": _scrub_text(report_url),
                "navigation": navigation,
            }
        snapshot_payload = self.snapshot(config_result=config_result, session_id=session_id, full=False)
        return {
            **screenshot_payload,
            "report_url": _scrub_text(report_url),
            "navigation": {
                "success": True,
                "url": navigation.get("url"),
                "title": navigation.get("title"),
            },
            "snapshot": snapshot_payload.get("snapshot", ""),
            "snapshot_truncated": bool(snapshot_payload.get("truncated", False)),
        }

    def compare_report_snapshots(
        self,
        *,
        config_result: ConfigResolutionResult,
        baseline_session_id: str,
        candidate_session_id: str,
        baseline_url: str,
        candidate_url: str,
        baseline_output_path: Path,
        baseline_output_virtual_path: str,
        candidate_output_path: Path,
        candidate_output_virtual_path: str,
        overlay_output_path: Path | None = None,
        overlay_output_virtual_path: str | None = None,
        full_page: bool = True,
        format: str = "png",
    ) -> dict[str, Any]:
        baseline = self.report_snapshot(
            config_result=config_result,
            session_id=baseline_session_id,
            report_url=baseline_url,
            output_path=baseline_output_path,
            output_virtual_path=baseline_output_virtual_path,
            full_page=full_page,
            format=format,
        )
        if not baseline.get("success"):
            return {
                "success": False,
                "status": "failed",
                "error": "baseline report snapshot failed",
                "baseline": baseline,
            }
        candidate = self.report_snapshot(
            config_result=config_result,
            session_id=candidate_session_id,
            report_url=candidate_url,
            output_path=candidate_output_path,
            output_virtual_path=candidate_output_virtual_path,
            full_page=full_page,
            format=format,
        )
        if not candidate.get("success"):
            return {
                "success": False,
                "status": "failed",
                "error": "candidate report snapshot failed",
                "baseline": baseline,
                "candidate": candidate,
            }
        comparison = _compare_screenshot_files(
            baseline_output_path=baseline_output_path,
            candidate_output_path=candidate_output_path,
            baseline_snapshot=str(baseline.get("snapshot") or ""),
            candidate_snapshot=str(candidate.get("snapshot") or ""),
            overlay_output_path=overlay_output_path,
            overlay_output_virtual_path=overlay_output_virtual_path,
        )
        return {
            "success": True,
            "status": "changed" if comparison["bytes_changed"] or comparison["snapshot_changed"] else "unchanged",
            "baseline": baseline,
            "candidate": candidate,
            "comparison": comparison,
        }

    def cdp(
        self,
        *,
        config_result: ConfigResolutionResult,
        session_id: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            session.setdefault("events", []).append({"action": "cdp", "method": method, "params": params or {}})
            return {"success": True, "provider": provider, "session_id": session_id, "result": {}}
        if provider == "cdp_http":
            clean_method = str(method or "").strip()
            if not clean_method or "." not in clean_method:
                return {"success": False, "provider": provider, "session_id": session_id, "error": "CDP method must look like Domain.method"}
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            payload = self._cdp_call(settings=settings, tab=tab, method=clean_method, params=params or {})
            return {"success": True, "provider": provider, "session_id": session_id, "result": _scrub_data(payload.get("result") or {})}
        return _unsupported_provider(provider)

    def dialog(
        self,
        *,
        config_result: ConfigResolutionResult,
        session_id: str,
        accept: bool = True,
        prompt_text: str | None = None,
    ) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        if provider == "mock":
            session = self._mock_session(config_result=config_result, settings=settings, session_id=session_id)
            session.setdefault("events", []).append({"action": "dialog", "accept": bool(accept), "prompt_text": prompt_text or ""})
            return {"success": True, "provider": provider, "session_id": session_id, "accepted": bool(accept)}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            params: dict[str, Any] = {"accept": bool(accept)}
            if prompt_text is not None:
                params["promptText"] = str(prompt_text)
            payload = self._cdp_call(settings=settings, tab=tab, method="Page.handleJavaScriptDialog", params=params)
            return {"success": True, "provider": provider, "session_id": session_id, "result": _scrub_data(payload.get("result") or {})}
        return _unsupported_provider(provider)

    def close(self, *, config_result: ConfigResolutionResult, session_id: str) -> dict[str, Any]:
        settings = _browser_settings(config_result)
        provider = _provider(settings)
        session_key = self._session_key(config_result=config_result, session_id=session_id)
        if provider == "mock":
            self._mock_sessions.pop(session_key, None)
            return {"success": True, "provider": provider, "session_id": session_id}
        if provider == "cdp_http":
            tab = self._cdp_tab(config_result=config_result, settings=settings, session_id=session_id, create=False)
            try:
                _json_request(_tab_url(settings, f"/json/close/{tab['id']}"), timeout=_timeout(settings))
            finally:
                self._cdp_sessions.pop(session_key, None)
            return {"success": True, "provider": provider, "session_id": session_id}
        return _unsupported_provider(provider)

    def _mock_page_payload(self, *, session: dict[str, Any], session_id: str, provider: str) -> dict[str, Any]:
        snapshot = str(session.get("snapshot") or _mock_snapshot(str(session.get("url") or "about:blank")))
        return {
            "success": True,
            "provider": provider,
            "session_id": session_id,
            "url": session.get("url", ""),
            "title": session.get("title", ""),
            "snapshot": _limit_text(_scrub_text(snapshot), DEFAULT_MAX_SNAPSHOT_CHARS),
        }

    def _mock_session(self, *, config_result: ConfigResolutionResult, settings: dict[str, Any], session_id: str) -> dict[str, Any]:
        session_key = self._session_key(config_result=config_result, session_id=session_id)
        if session_key not in self._mock_sessions:
            base = settings.get("mock_session") if isinstance(settings.get("mock_session"), dict) else {}
            self._mock_sessions[session_key] = dict(base)
        return self._mock_sessions[session_key]

    def _session_key(self, *, config_result: ConfigResolutionResult, session_id: str) -> str:
        return f"{config_result.fingerprint}:{session_id}"

    def _cdp_tab(self, *, config_result: ConfigResolutionResult, settings: dict[str, Any], session_id: str, create: bool = True) -> dict[str, Any]:
        session_key = self._session_key(config_result=config_result, session_id=session_id)
        existing = self._cdp_sessions.get(session_key)
        if existing:
            return dict(existing)
        tabs = _json_request(_tab_url(settings, "/json"), timeout=_timeout(settings))
        tab = None
        if create:
            tab = self._create_cdp_tab(settings)
        if tab is None and bool(settings.get("reuse_existing_tab", False)) and isinstance(tabs, list):
            tab = next((item for item in tabs if isinstance(item, dict) and item.get("type") == "page"), None)
        if not isinstance(tab, dict):
            raise RuntimeError("no CDP page target is available; enable browser_tools.reuse_existing_tab or allow /json/new")
        if not tab.get("webSocketDebuggerUrl"):
            raise RuntimeError("CDP target is missing webSocketDebuggerUrl")
        self._cdp_sessions[session_key] = {"id": tab["id"], "webSocketDebuggerUrl": tab["webSocketDebuggerUrl"]}
        return dict(self._cdp_sessions[session_key])

    def _create_cdp_tab(self, settings: dict[str, Any]) -> dict[str, Any] | None:
        path = "/json/new?" + urllib.parse.quote("about:blank", safe="")
        for method in ("PUT", "GET"):
            try:
                created = _json_request(_tab_url(settings, path), method=method, timeout=_timeout(settings))
            except Exception:
                continue
            if isinstance(created, dict):
                return created
        return None

    def _cdp_call(self, *, settings: dict[str, Any], tab: dict[str, Any], method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._rpc_id += 1
        payload = {"id": self._rpc_id, "method": method, "params": params}
        result = _websocket_json_rpc(str(tab["webSocketDebuggerUrl"]), payload=payload, timeout=_timeout(settings))
        if result.get("error"):
            raise RuntimeError(_scrub_text(json.dumps(result["error"], ensure_ascii=False)))
        return result

    def _cdp_eval(self, *, settings: dict[str, Any], tab: dict[str, Any], expression: str, return_by_value: bool = True) -> Any:
        payload = self._cdp_call(
            settings=settings,
            tab=tab,
            method="Runtime.evaluate",
            params={"expression": expression, "returnByValue": return_by_value, "awaitPromise": True},
        )
        if payload.get("exceptionDetails"):
            return {"ok": False, "error": _scrub_text(json.dumps(payload["exceptionDetails"], ensure_ascii=False))}
        result = ((payload.get("result") or {}).get("result") or {})
        if "value" in result:
            return result["value"]
        return result.get("description") or result.get("type")

    def _install_console_capture(self, *, settings: dict[str, Any], tab: dict[str, Any]) -> None:
        script = (
            "(() => {"
            "if (window.__anvilConsoleInstalled) return true;"
            "window.__anvilConsoleInstalled = true;"
            "window.__anvilConsoleMessages = window.__anvilConsoleMessages || [];"
            "for (const level of ['log','info','warn','error']) {"
            "const original = console[level] && console[level].bind(console);"
            "console[level] = (...args) => {"
            "window.__anvilConsoleMessages.push({level, text: args.map(a => { try { return typeof a === 'string' ? a : JSON.stringify(a); } catch { return String(a); } }).join(' '), ts: Date.now()});"
            "if (original) original(...args);"
            "};"
            "}"
            "window.addEventListener('error', event => window.__anvilConsoleMessages.push({level:'exception', text: event.message || '', ts: Date.now()}));"
            "window.addEventListener('unhandledrejection', event => window.__anvilConsoleMessages.push({level:'exception', text: String(event.reason || ''), ts: Date.now()}));"
            "return true;"
            "})()"
        )
        try:
            self._cdp_eval(settings=settings, tab=tab, expression=script)
        except Exception:
            return

    def _cdp_snapshot(self, *, settings: dict[str, Any], tab: dict[str, Any]) -> str:
        js = (
            "(() => {"
            "const title = document.title || '';"
            "const url = location.href;"
            "const headingEls = [...document.querySelectorAll('h1,h2,h3')].slice(0,24);"
            "const headings = headingEls.map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean);"
            "const linkEls = [...document.querySelectorAll('a[href]')].slice(0,80);"
            "const links = linkEls.map((e,i) => { const id = `a${i + 1}`; e.setAttribute('data-anvil-ref', id); return `[@${id}] ${(e.innerText || e.textContent || e.href || '').trim()} -> ${e.href}`; });"
            "const interactiveEls = [...document.querySelectorAll('button,input,textarea,select,[role=button],[contenteditable=true]')].slice(0,100);"
            "const inputs = interactiveEls.map((e,i) => { const id = `e${i + 1}`; e.setAttribute('data-anvil-ref', id); const label = e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('name') || e.innerText || e.value || ''; return `[@${id}] ${e.tagName.toLowerCase()} ${String(label).trim()}`; });"
            "const body = (document.body && document.body.innerText || '').replace(/\\s+/g,' ').trim().slice(0,9000);"
            "const dialogs = [...document.querySelectorAll('[role=dialog],dialog')].slice(0,10).map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean);"
            "return JSON.stringify({title,url,headings,links,inputs,dialogs,body});"
            "})()"
        )
        raw = self._cdp_eval(settings=settings, tab=tab, expression=js)
        try:
            data = json.loads(raw if isinstance(raw, str) else "{}")
        except json.JSONDecodeError:
            data = {"body": str(raw or "")}
        lines = [f"Title: {data.get('title') or ''}", f"URL: {data.get('url') or ''}"]
        if data.get("headings"):
            lines.append("Headings:")
            lines.extend(f"- {item}" for item in data["headings"])
        if data.get("dialogs"):
            lines.append("Dialogs:")
            lines.extend(f"- {item}" for item in data["dialogs"])
        if data.get("inputs"):
            lines.append("Interactive elements:")
            lines.extend(str(item) for item in data["inputs"])
        if data.get("links"):
            lines.append("Links:")
            lines.extend(str(item) for item in data["links"])
        if data.get("body"):
            lines.append("Text:")
            lines.append(str(data["body"]))
        return _scrub_text("\n".join(lines))


def _browser_settings(config_result: ConfigResolutionResult) -> dict[str, Any]:
    raw = config_result.effective_config.additional_settings.get("browser_tools") or config_result.effective_config.additional_settings.get("browser") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _provider(settings: dict[str, Any]) -> str:
    provider = str(settings.get("provider") or settings.get("backend") or "").strip().lower().replace("-", "_")
    if provider:
        return {"cdp": "cdp_http", "chrome_devtools": "cdp_http", "devtools": "cdp_http"}.get(provider, provider)
    if settings.get("mock_session") is not None or settings.get("mock_pages") is not None:
        return "mock"
    if _resolve_secret(settings.get("cdp_url") or "$BROWSER_CDP_URL"):
        return "cdp_http"
    return "unavailable"


def _allow_private_urls(settings: dict[str, Any]) -> bool:
    return bool(settings.get("allow_private_urls", False))


def _validate_url(url: str, *, allow_private: bool) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must include http:// or https:// and a host")
    if SECRET_VALUE_RE.search(url):
        raise ValueError("URL appears to contain an API key or token")
    host = parsed.hostname or ""
    if not allow_private and PRIVATE_HOST_RE.match(host):
        raise ValueError("URL targets a private or internal address")
    return urllib.parse.urlunparse(parsed)


def _mock_page(settings: dict[str, Any], url: str) -> dict[str, Any]:
    pages = settings.get("mock_pages")
    if isinstance(pages, dict) and isinstance(pages.get(url), dict):
        return dict(pages[url])
    return {"title": _mock_title(url), "snapshot": _mock_snapshot(url)}


def _mock_title(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or "page"


def _mock_snapshot(url: str) -> str:
    return f"Title: {_mock_title(url)}\nURL: {url}\nText:\nMock browser page for {url}"


def _normalize_ref(ref: str) -> str:
    cleaned = str(ref or "").strip()
    if not cleaned:
        raise ValueError("ref is required")
    if cleaned.startswith(("css=", "#", ".", "[")):
        return cleaned
    return cleaned if cleaned.startswith("@") else f"@{cleaned}"


def _selector_from_ref(ref: str) -> str:
    if ref.startswith("css="):
        return ref[4:].strip()
    if ref.startswith(("#", ".", "[")):
        return ref
    return f'[data-anvil-ref="{ref.lstrip("@")}"]'


def _dom_action_script(selector: str, action: str, *, text: str = "") -> str:
    if action == "click":
        action_js = "el.click(); return {ok:true};"
    elif action == "type":
        action_js = (
            "el.focus();"
            f"if ('value' in el) {{ el.value = {json.dumps(text)}; }} else {{ el.textContent = {json.dumps(text)}; }}"
            "el.dispatchEvent(new Event('input', {bubbles:true}));"
            "el.dispatchEvent(new Event('change', {bubbles:true}));"
            "return {ok:true};"
        )
    else:
        action_js = "return {ok:false,error:'unknown action'};"
    return (
        "(() => {"
        f"const el = document.querySelector({json.dumps(selector)});"
        "if (!el) return {ok:false,error:'element not found'};"
        f"{action_js}"
        "})()"
    )


def _max_snapshot_chars(settings: dict[str, Any]) -> int:
    return _bounded_int(settings.get("max_snapshot_chars"), default=DEFAULT_MAX_SNAPSHOT_CHARS, minimum=1000, maximum=50000)


def _timeout(settings: dict[str, Any]) -> int:
    return _bounded_int(settings.get("timeout_seconds"), default=DEFAULT_TIMEOUT_SECONDS, minimum=1, maximum=120)


def _navigation_wait_ms(settings: dict[str, Any]) -> int:
    return _bounded_int(settings.get("navigation_wait_ms"), default=DEFAULT_NAVIGATION_WAIT_MS, minimum=0, maximum=5000)


def _sleep_after_navigation(settings: dict[str, Any]) -> None:
    wait_ms = _navigation_wait_ms(settings)
    if wait_ms:
        time.sleep(wait_ms / 1000)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _tab_url(settings: dict[str, Any], path: str) -> str:
    base = _resolve_secret(settings.get("cdp_url") or "$BROWSER_CDP_URL").strip() or "http://127.0.0.1:9222"
    return base.rstrip("/") + path


def _unsupported_provider(provider: str) -> dict[str, Any]:
    return {
        "success": False,
        "provider": provider or "unavailable",
        "error": "browser provider is unavailable; configure browser_tools.provider=mock for tests or browser_tools.cdp_url/BROWSER_CDP_URL for Chrome DevTools",
    }


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "AnvilBrowserTools/1.0"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {_scrub_text(body or exc.reason)}") from exc
    if not body.strip():
        return {}
    return json.loads(body)


def _websocket_json_rpc(url: str, *, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"ws", "wss"}:
        raise RuntimeError("CDP websocket URL must use ws:// or wss://")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    raw_sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock: socket.socket | ssl.SSLSocket
        if parsed.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host)
        else:
            sock = raw_sock
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = _recv_until(sock, b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("CDP websocket upgrade failed")
        target_id = payload.get("id")
        _ws_send_text(sock, json.dumps(payload, separators=(",", ":")))
        while True:
            message = _ws_recv_text(sock)
            if not message:
                continue
            data = json.loads(message)
            if data.get("id") == target_id:
                return data
    finally:
        try:
            raw_sock.close()
        except OSError:
            pass


def _recv_until(sock: socket.socket | ssl.SSLSocket, marker: bytes) -> bytes:
    chunks: list[bytes] = []
    data = b""
    while marker not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        data = b"".join(chunks)
    return data


def _ws_send_text(sock: socket.socket | ssl.SSLSocket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    length = len(payload)
    if length <= 125:
        header.append(0x80 | length)
    elif length <= 65535:
        header.extend([0x80 | 126, (length >> 8) & 0xFF, length & 0xFF])
    else:
        header.append(0x80 | 127)
        header.extend(length.to_bytes(8, "big"))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _ws_recv_text(sock: socket.socket | ssl.SSLSocket) -> str:
    while True:
        first = _recv_exact(sock, 2)
        if len(first) < 2:
            return ""
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = int.from_bytes(_recv_exact(sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(_recv_exact(sock, 8), "big")
        mask = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, length)
        if masked and mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x1:
            return payload.decode("utf-8", errors="replace")
        if opcode == 0x8:
            return ""
        if opcode == 0x9:
            _ws_send_pong(sock, payload)


def _ws_send_pong(sock: socket.socket | ssl.SSLSocket, payload: bytes) -> None:
    header = bytearray([0x8A])
    length = len(payload)
    if length <= 125:
        header.append(0x80 | length)
    else:
        payload = payload[:125]
        header.append(0x80 | len(payload))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _recv_exact(sock: socket.socket | ssl.SSLSocket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _normalize_screenshot_format(value: str) -> str:
    normalized = str(value or "png").strip().lower()
    if normalized == "jpg":
        normalized = "jpeg"
    if normalized not in SUPPORTED_SCREENSHOT_FORMATS:
        raise ValueError("format must be 'png' or 'jpeg'")
    return normalized


def _safe_output_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-_")
    return cleaned[:80] or "report"


def _mock_screenshot_bytes(*, session: dict[str, Any], image_format: str) -> bytes:
    if image_format == "jpeg":
        jpeg_base64 = str(session.get("screenshot_jpeg_base64") or "").strip()
        if jpeg_base64:
            try:
                return base64.b64decode(jpeg_base64)
            except Exception:
                pass
    screenshot_base64 = str(session.get("screenshot_base64") or "").strip()
    if screenshot_base64:
        try:
            return base64.b64decode(screenshot_base64)
        except Exception:
            return base64.b64decode(_mock_png_base64())
    return base64.b64decode(_mock_png_base64())


def _compare_screenshot_files(
    *,
    baseline_output_path: Path,
    candidate_output_path: Path,
    baseline_snapshot: str,
    candidate_snapshot: str,
    overlay_output_path: Path | None = None,
    overlay_output_virtual_path: str | None = None,
) -> dict[str, Any]:
    baseline_bytes = baseline_output_path.read_bytes()
    candidate_bytes = candidate_output_path.read_bytes()
    baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
    candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
    byte_delta = len(candidate_bytes) - len(baseline_bytes)
    snapshot_delta = _snapshot_delta(baseline_snapshot, candidate_snapshot)
    pixel_delta = _png_pixel_delta(
        baseline_bytes=baseline_bytes,
        candidate_bytes=candidate_bytes,
        overlay_output_path=overlay_output_path,
        overlay_output_virtual_path=overlay_output_virtual_path,
    )
    return {
        "bytes_changed": baseline_hash != candidate_hash,
        "byte_delta": byte_delta,
        "baseline": {
            "sha256": baseline_hash,
            "bytes": len(baseline_bytes),
        },
        "candidate": {
            "sha256": candidate_hash,
            "bytes": len(candidate_bytes),
        },
        "snapshot_changed": snapshot_delta["changed"],
        "snapshot_delta": snapshot_delta,
        "pixel_delta": pixel_delta,
        "pixels_changed": bool(pixel_delta.get("changed", False)),
    }


def _png_pixel_delta(
    *,
    baseline_bytes: bytes,
    candidate_bytes: bytes,
    overlay_output_path: Path | None,
    overlay_output_virtual_path: str | None,
) -> dict[str, Any]:
    try:
        baseline_image = _read_png_rgba(baseline_bytes)
        candidate_image = _read_png_rgba(candidate_bytes)
    except ValueError as exc:
        return {"available": False, "changed": False, "reason": str(exc)}
    if baseline_image["width"] != candidate_image["width"] or baseline_image["height"] != candidate_image["height"]:
        return {
            "available": True,
            "changed": True,
            "reason": "image_dimensions_differ",
            "baseline": {"width": baseline_image["width"], "height": baseline_image["height"]},
            "candidate": {"width": candidate_image["width"], "height": candidate_image["height"]},
        }
    width = int(baseline_image["width"])
    height = int(baseline_image["height"])
    total_pixels = width * height
    if total_pixels > PIXEL_DIFF_MAX_PIXELS:
        return {
            "available": False,
            "changed": False,
            "reason": "image_too_large_for_builtin_pixel_diff",
            "width": width,
            "height": height,
            "max_pixels": PIXEL_DIFF_MAX_PIXELS,
        }
    grid_columns = min(PIXEL_DIFF_GRID_COLUMNS, max(1, width))
    grid_rows = min(PIXEL_DIFF_GRID_ROWS, max(1, height))
    cell_count = grid_columns * grid_rows
    changed_by_cell = [0] * cell_count
    intensity_by_cell = [0] * cell_count
    changed_pixels = 0
    total_intensity = 0
    max_intensity = 0
    overlay_pixels: list[tuple[int, int, int, int]] | None = [] if overlay_output_path and overlay_output_virtual_path else None
    baseline_pixels = baseline_image["pixels"]
    candidate_pixels = candidate_image["pixels"]
    for index, (left, right) in enumerate(zip(baseline_pixels, candidate_pixels)):
        channel_delta = (
            abs(left[0] - right[0])
            + abs(left[1] - right[1])
            + abs(left[2] - right[2])
            + abs(left[3] - right[3])
        )
        if channel_delta > PIXEL_DIFF_CHANNEL_THRESHOLD:
            changed_pixels += 1
            total_intensity += channel_delta
            max_intensity = max(max_intensity, channel_delta)
            y, x = divmod(index, width)
            cell_x = min(grid_columns - 1, int(x * grid_columns / width))
            cell_y = min(grid_rows - 1, int(y * grid_rows / height))
            cell_index = cell_y * grid_columns + cell_x
            changed_by_cell[cell_index] += 1
            intensity_by_cell[cell_index] += channel_delta
            if overlay_pixels is not None:
                overlay_pixels.append((255, 47, 83, 220))
        elif overlay_pixels is not None:
            blended = (
                (left[0] + right[0]) // 2,
                (left[1] + right[1]) // 2,
                (left[2] + right[2]) // 2,
                255,
            )
            overlay_pixels.append(blended)
    top_cells = _top_pixel_diff_cells(
        changed_by_cell=changed_by_cell,
        intensity_by_cell=intensity_by_cell,
        grid_columns=grid_columns,
        grid_rows=grid_rows,
        width=width,
        height=height,
    )
    overlay_path: str | None = None
    if overlay_pixels is not None and changed_pixels > 0:
        overlay_output_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_output_path.write_bytes(_write_png_rgba(width=width, height=height, pixels=overlay_pixels))
        overlay_path = overlay_output_virtual_path
    return {
        "available": True,
        "changed": changed_pixels > 0,
        "width": width,
        "height": height,
        "grid": {"columns": grid_columns, "rows": grid_rows},
        "changed_pixels": changed_pixels,
        "total_pixels": total_pixels,
        "changed_ratio": round(changed_pixels / total_pixels, 6) if total_pixels else 0.0,
        "mean_intensity": round(total_intensity / changed_pixels, 3) if changed_pixels else 0.0,
        "max_intensity": max_intensity,
        "top_cells": top_cells,
        "overlay_path": overlay_path,
    }


def _top_pixel_diff_cells(
    *,
    changed_by_cell: list[int],
    intensity_by_cell: list[int],
    grid_columns: int,
    grid_rows: int,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for cell_index, changed_pixels in enumerate(changed_by_cell):
        if changed_pixels <= 0:
            continue
        cell_y, cell_x = divmod(cell_index, grid_columns)
        x0 = int(cell_x * width / grid_columns)
        x1 = int((cell_x + 1) * width / grid_columns)
        y0 = int(cell_y * height / grid_rows)
        y1 = int((cell_y + 1) * height / grid_rows)
        cell_pixels = max(1, (x1 - x0) * (y1 - y0))
        cells.append(
            {
                "cell": [cell_x, cell_y],
                "bounds": {"x": x0, "y": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)},
                "changed_pixels": changed_pixels,
                "changed_ratio": round(changed_pixels / cell_pixels, 6),
                "mean_intensity": round(intensity_by_cell[cell_index] / changed_pixels, 3),
            }
        )
    cells.sort(key=lambda item: (item["changed_pixels"], item["mean_intensity"]), reverse=True)
    return cells[:PIXEL_DIFF_TOP_CELL_LIMIT]


def _read_png_rgba(data: bytes) -> dict[str, Any]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("pixel diff only supports PNG screenshots")
    offset = 8
    width = height = bit_depth = color_type = None
    idat_chunks: list[bytes] = []
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if len(chunk_data) != length:
            raise ValueError("truncated PNG chunk")
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, filter_method, interlace = struct.unpack(">IIBBBBB", chunk_data)
            if bit_depth != 8:
                raise ValueError("pixel diff only supports 8-bit PNG screenshots")
            if color_type not in {2, 6}:
                raise ValueError("pixel diff only supports RGB/RGBA PNG screenshots")
            if filter_method != 0 or interlace != 0:
                raise ValueError("pixel diff does not support interlaced PNG screenshots")
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break
    if not width or not height or bit_depth is None or color_type is None:
        raise ValueError("invalid PNG screenshot")
    channels = 4 if color_type == 6 else 3
    stride = width * channels
    try:
        raw = zlib.decompress(b"".join(idat_chunks))
    except zlib.error as exc:
        raise ValueError("invalid PNG compressed data") from exc
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise ValueError("truncated PNG pixel data")
    rows: list[bytes] = []
    previous = bytes(stride)
    cursor = 0
    for _row in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row_data = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        row = _png_unfilter_row(row_data, previous, filter_type, channels)
        rows.append(row)
        previous = row
    pixels: list[tuple[int, int, int, int]] = []
    for row in rows:
        for index in range(0, len(row), channels):
            r = row[index]
            g = row[index + 1]
            b = row[index + 2]
            a = row[index + 3] if channels == 4 else 255
            pixels.append((r, g, b, a))
    return {"width": width, "height": height, "pixels": pixels}


def _png_unfilter_row(row: bytearray, previous: bytes, filter_type: int, bytes_per_pixel: int) -> bytes:
    if filter_type == 0:
        return bytes(row)
    for index in range(len(row)):
        left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        up = previous[index] if previous else 0
        upper_left = previous[index - bytes_per_pixel] if previous and index >= bytes_per_pixel else 0
        if filter_type == 1:
            row[index] = (row[index] + left) & 0xFF
        elif filter_type == 2:
            row[index] = (row[index] + up) & 0xFF
        elif filter_type == 3:
            row[index] = (row[index] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            row[index] = (row[index] + _paeth_predictor(left, up, upper_left)) & 0xFF
        else:
            raise ValueError("unsupported PNG filter type")
    return bytes(row)


def _paeth_predictor(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def _write_png_rgba(*, width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    if len(pixels) != width * height:
        raise ValueError("pixel count does not match image dimensions")
    raw = bytearray()
    cursor = 0
    for _y in range(height):
        raw.append(0)
        for _x in range(width):
            raw.extend(bytes(pixels[cursor]))
            cursor += 1
    def chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        body = chunk_type + chunk_data
        checksum = binascii.crc32(body) & 0xFFFFFFFF
        return len(chunk_data).to_bytes(4, "big") + body + checksum.to_bytes(4, "big")

    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(raw), level=6)),
            chunk(b"IEND", b""),
        ]
    )


def _snapshot_delta(baseline_snapshot: str, candidate_snapshot: str) -> dict[str, Any]:
    baseline_lines = [line.strip() for line in baseline_snapshot.splitlines() if line.strip()]
    candidate_lines = [line.strip() for line in candidate_snapshot.splitlines() if line.strip()]
    baseline_set = set(baseline_lines)
    candidate_set = set(candidate_lines)
    added = sorted(candidate_set - baseline_set)[:20]
    removed = sorted(baseline_set - candidate_set)[:20]
    return {
        "changed": bool(added or removed),
        "baseline_line_count": len(baseline_lines),
        "candidate_line_count": len(candidate_lines),
        "added_lines": added,
        "removed_lines": removed,
        "truncated": len(candidate_set - baseline_set) > len(added) or len(baseline_set - candidate_set) > len(removed),
    }


def _limit_text(value: str, max_chars: int) -> str:
    return value[:max_chars]


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


def _mock_png_base64() -> str:
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    )
