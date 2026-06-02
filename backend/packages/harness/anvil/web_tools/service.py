from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from anvil.config import ConfigResolutionResult


ANCHOR_TAG_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
RESULT_SNIPPET_RE = re.compile(
    r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>|<div[^>]*class="result__snippet"[^>]*>(?P<divsnippet>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
HTML_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TITLE_RE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESCRIPTION_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](?P<content>[^"\']*)["\'][^>]*>',
    re.IGNORECASE | re.DOTALL,
)
ANCHOR_HREF_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
IMG_RE = re.compile(r"<img\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(r"(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)", re.DOTALL)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})")


class WebToolsService:
    """Provider-adapted web tools with small, JSON-safe runtime contracts."""

    def search(self, *, config_result: ConfigResolutionResult, query: str, max_results: int = 5) -> dict[str, Any]:
        settings = _web_tools_settings(config_result)
        max_results = _bounded_int(max_results, default=5, minimum=1, maximum=int(settings.get("max_results", 10) or 10))
        mock_results = settings.get("mock_search_results", {})
        if isinstance(mock_results, dict) and query in mock_results:
            return _result_payload(provider="mock", query=query, results=list(mock_results[query])[:max_results])

        provider_order = _provider_order(settings, "search")
        errors: list[dict[str, str]] = []
        for provider in provider_order:
            try:
                if provider == "tavily":
                    return _with_provider_failures(self._search_tavily(settings=settings, query=query, max_results=max_results), errors)
                if provider == "exa":
                    return _with_provider_failures(self._search_exa(settings=settings, query=query, max_results=max_results), errors)
                if provider == "searxng":
                    return _with_provider_failures(self._search_searxng(settings=settings, query=query, max_results=max_results), errors)
                if provider in {"duckduckgo", "duckduckgo_html", "html"}:
                    return _with_provider_failures(self._search_duckduckgo(settings=settings, query=query, max_results=max_results), errors)
            except Exception as exc:
                errors.append(_error_payload(provider, exc))
        return _result_payload(provider="unavailable", query=query, results=[], errors=errors)

    def fetch(
        self,
        *,
        config_result: ConfigResolutionResult,
        url: str,
        timeout_seconds: int = 20,
        max_chars: int = 8000,
    ) -> dict[str, Any]:
        settings = _web_tools_settings(config_result)
        timeout_seconds = _bounded_int(timeout_seconds, default=int(settings.get("timeout_seconds", 20) or 20), minimum=1, maximum=60)
        max_chars = _bounded_int(max_chars, default=int(settings.get("max_fetch_chars", 8000) or 8000), minimum=100, maximum=40000)
        normalized_url = _validate_url(url)
        mock_fetch = settings.get("mock_fetch_results", {})
        if isinstance(mock_fetch, dict) and normalized_url in mock_fetch:
            payload = dict(mock_fetch[normalized_url])
            payload.setdefault("provider", "mock")
            payload.setdefault("url", normalized_url)
            payload.setdefault("truncated", False)
            return payload

        provider_order = _provider_order(settings, "fetch")
        errors: list[dict[str, str]] = []
        for provider in provider_order:
            try:
                if provider == "tavily":
                    return _with_provider_failures(self._fetch_tavily(settings=settings, url=normalized_url, timeout_seconds=timeout_seconds, max_chars=max_chars), errors)
                if provider == "jina":
                    return _with_provider_failures(self._fetch_jina(settings=settings, url=normalized_url, timeout_seconds=timeout_seconds, max_chars=max_chars), errors)
                if provider == "firecrawl":
                    return _with_provider_failures(self._fetch_firecrawl(settings=settings, url=normalized_url, timeout_seconds=timeout_seconds, max_chars=max_chars), errors)
                if provider in {"html", "direct"}:
                    return _with_provider_failures(self._fetch_direct(settings=settings, url=normalized_url, timeout_seconds=timeout_seconds, max_chars=max_chars), errors)
            except Exception as exc:
                errors.append(_error_payload(provider, exc))
        return {"provider": "unavailable", "url": normalized_url, "content": "", "truncated": False, "errors": errors}

    def extract(
        self,
        *,
        config_result: ConfigResolutionResult,
        url: str | None = None,
        urls: list[str] | None = None,
        format: str = "markdown",
        timeout_seconds: int = 20,
        max_chars: int = 12000,
        include_links: bool = False,
        include_images: bool = False,
    ) -> dict[str, Any]:
        settings = _web_tools_settings(config_result)
        requested_urls = _normalize_extract_urls(url=url, urls=urls)
        max_urls = _bounded_int(settings.get("max_extract_urls", 5), default=5, minimum=1, maximum=20)
        requested_urls = requested_urls[:max_urls]
        timeout_seconds = _bounded_int(timeout_seconds, default=int(settings.get("timeout_seconds", 20) or 20), minimum=1, maximum=60)
        max_chars = _bounded_int(max_chars, default=int(settings.get("max_extract_chars", settings.get("max_fetch_chars", 12000)) or 12000), minimum=100, maximum=60000)
        output_format = str(format or "markdown").strip().lower()
        if output_format not in {"markdown", "text", "json"}:
            output_format = "markdown"

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        provider = "unavailable"
        per_item_budget = max(100, max_chars // max(len(requested_urls), 1))
        for target_url in requested_urls:
            try:
                fetched = self.fetch(
                    config_result=config_result,
                    url=target_url,
                    timeout_seconds=timeout_seconds,
                    max_chars=per_item_budget,
                )
                provider = str(fetched.get("provider") or provider)
                items.append(
                    _extract_item_from_fetch(
                        fetched,
                        source_url=target_url,
                        output_format=output_format,
                        max_chars=per_item_budget,
                        include_links=include_links,
                        include_images=include_images,
                    )
                )
            except Exception as exc:
                errors.append(_error_payload("extract", exc))

        content = _limit_text(_render_extracted_content(items, output_format=output_format), max_chars)
        payload: dict[str, Any] = {
            "provider": provider if items else "unavailable",
            "url_count": len(items),
            "requested_urls": requested_urls,
            "format": output_format,
            "content": content,
            "items": items,
            "truncated": any(bool(item.get("truncated")) for item in items) or len(content) >= max_chars,
        }
        if errors:
            payload["errors"] = errors
        return _scrub_data(payload)

    def image_search(self, *, config_result: ConfigResolutionResult, query: str, max_results: int = 5) -> dict[str, Any]:
        settings = _web_tools_settings(config_result)
        max_results = _bounded_int(max_results, default=5, minimum=1, maximum=int(settings.get("max_results", 10) or 10))
        mock_results = settings.get("mock_image_results", {})
        if isinstance(mock_results, dict) and query in mock_results:
            return _result_payload(provider="mock", query=query, results=list(mock_results[query])[:max_results])

        provider_order = _provider_order(settings, "image")
        errors: list[dict[str, str]] = []
        for provider in provider_order:
            try:
                if provider == "searxng":
                    return _with_provider_failures(self._search_searxng(settings=settings, query=query, max_results=max_results, image=True), errors)
                if provider in {"wikimedia", "commons"}:
                    return _with_provider_failures(self._image_search_wikimedia(settings=settings, query=query, max_results=max_results), errors)
            except Exception as exc:
                errors.append(_error_payload(provider, exc))
        return _result_payload(provider="unavailable", query=query, results=[], errors=errors)

    def crawl(
        self,
        *,
        config_result: ConfigResolutionResult,
        url: str,
        instructions: str = "",
        max_pages: int = 5,
        max_chars: int = 20000,
        timeout_seconds: int = 20,
    ) -> dict[str, Any]:
        settings = _web_tools_settings(config_result)
        normalized_url = _validate_url(url)
        max_pages = _bounded_int(max_pages, default=5, minimum=1, maximum=int(settings.get("max_crawl_pages", 20) or 20))
        max_chars = _bounded_int(max_chars, default=int(settings.get("max_crawl_chars", 20000) or 20000), minimum=500, maximum=100000)
        timeout_seconds = _bounded_int(timeout_seconds, default=int(settings.get("timeout_seconds", 20) or 20), minimum=1, maximum=120)

        mock_crawl = settings.get("mock_crawl_results", {})
        if isinstance(mock_crawl, dict) and normalized_url in mock_crawl:
            return _crawl_payload(
                provider="mock",
                url=normalized_url,
                items=_filter_crawl_items(list(mock_crawl[normalized_url]), instructions=instructions, max_pages=max_pages),
                instructions=instructions,
                max_chars=max_chars,
            )

        provider_order = _provider_order(settings, "crawl")
        errors: list[dict[str, str]] = []
        for provider in provider_order:
            try:
                if provider == "tavily":
                    return _with_provider_failures(self._crawl_tavily(settings=settings, url=normalized_url, instructions=instructions, max_pages=max_pages, max_chars=max_chars, timeout_seconds=timeout_seconds), errors)
                if provider == "firecrawl":
                    return _with_provider_failures(self._crawl_firecrawl(settings=settings, url=normalized_url, instructions=instructions, max_pages=max_pages, max_chars=max_chars, timeout_seconds=timeout_seconds), errors)
                if provider in {"direct", "html"}:
                    return _with_provider_failures(self._crawl_direct(config_result=config_result, url=normalized_url, instructions=instructions, max_pages=max_pages, max_chars=max_chars, timeout_seconds=timeout_seconds), errors)
            except Exception as exc:
                errors.append(_error_payload(provider, exc))
        return _crawl_payload(provider="unavailable", url=normalized_url, items=[], instructions=instructions, max_chars=max_chars, errors=errors)

    def _search_tavily(self, *, settings: dict[str, Any], query: str, max_results: int) -> dict[str, Any]:
        api_key = _resolve_secret(settings.get("tavily_api_key") or settings.get("api_key") or "$TAVILY_API_KEY")
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not configured")
        payload = {
            "query": query,
            "max_results": max_results,
            "include_answer": bool(settings.get("tavily_include_answer", False)),
        }
        response = _json_request(
            "https://api.tavily.com/search",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=int(settings.get("timeout_seconds", 20) or 20),
        )
        results = []
        for index, item in enumerate(response.get("results") or []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "snippet": item.get("content") or item.get("snippet") or "",
                    "score": item.get("score"),
                    "position": index + 1,
                }
            )
        payload_out = _result_payload(provider="tavily", query=query, results=results[:max_results])
        if response.get("answer"):
            payload_out["answer"] = _scrub_text(str(response["answer"]))
        return payload_out

    def _search_exa(self, *, settings: dict[str, Any], query: str, max_results: int) -> dict[str, Any]:
        api_key = _resolve_secret(settings.get("exa_api_key") or "$EXA_API_KEY")
        if not api_key:
            raise ValueError("EXA_API_KEY is not configured")
        payload = {"query": query, "numResults": max_results}
        response = _json_request(
            "https://api.exa.ai/search",
            method="POST",
            payload=payload,
            headers={"x-api-key": api_key},
            timeout=int(settings.get("timeout_seconds", 20) or 20),
        )
        results = []
        for index, item in enumerate(response.get("results") or []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "snippet": item.get("text") or item.get("highlights") or "",
                    "score": item.get("score"),
                    "position": index + 1,
                }
            )
        return _result_payload(provider="exa", query=query, results=results[:max_results])

    def _search_searxng(self, *, settings: dict[str, Any], query: str, max_results: int, image: bool = False) -> dict[str, Any]:
        base_url = _resolve_optional_url(settings.get("searxng_base_url"), env_name="SEARXNG_BASE_URL").rstrip("/")
        if not base_url:
            raise ValueError("SEARXNG_BASE_URL is not configured")
        params = {
            "q": query,
            "format": "json",
            "language": str(settings.get("language") or "all"),
            "safesearch": str(settings.get("safesearch") or "1"),
        }
        if image:
            params["categories"] = "images"
        response = _json_request(f"{base_url}/search?{urllib.parse.urlencode(params)}", timeout=int(settings.get("timeout_seconds", 20) or 20))
        results = []
        for index, item in enumerate(response.get("results") or []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": item.get("title") or "",
                    "url": item.get("img_src") or item.get("url") or "",
                    "source_url": item.get("url"),
                    "snippet": item.get("content") or "",
                    "engine": item.get("engine"),
                    "position": index + 1,
                }
            )
            if len(results) >= max_results:
                break
        return _result_payload(provider="searxng", query=query, results=results)

    def _search_duckduckgo(self, *, settings: dict[str, Any], query: str, max_results: int) -> dict[str, Any]:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        body = _text_request(url, timeout=int(settings.get("timeout_seconds", 20) or 20))
        anchors = list(ANCHOR_TAG_RE.finditer(body))
        snippets = list(RESULT_SNIPPET_RE.finditer(body))
        results = []
        for index, match in enumerate(anchors):
            href = html_lib.unescape(match.group("href"))
            title = _html_to_text(match.group("title"))
            if not href or not title:
                continue
            snippet = ""
            if index < len(snippets):
                snippet = _html_to_text(snippets[index].group("snippet") or snippets[index].group("divsnippet") or "")
            results.append({"title": title, "url": href, "snippet": snippet, "position": len(results) + 1})
            if len(results) >= max_results:
                break
        return _result_payload(provider="duckduckgo_html", query=query, results=results)

    def _fetch_tavily(self, *, settings: dict[str, Any], url: str, timeout_seconds: int, max_chars: int) -> dict[str, Any]:
        api_key = _resolve_secret(settings.get("tavily_api_key") or settings.get("api_key") or "$TAVILY_API_KEY")
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not configured")
        response = _json_request(
            "https://api.tavily.com/extract",
            method="POST",
            payload={"urls": [url]},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        results = response.get("results") or []
        if not results:
            failures = response.get("failed_results") or response.get("failed_urls") or []
            raise ValueError(f"Tavily extract returned no content: {failures}")
        item = results[0]
        raw = str(item.get("raw_content") or item.get("content") or "")
        title = str(item.get("title") or "")
        content = _limit_text(_scrub_text(raw), max_chars)
        return {
            "provider": "tavily",
            "url": url,
            "final_url": item.get("url") or url,
            "status": 200,
            "content_type": "text/plain",
            "title": title,
            "content": content,
            "truncated": len(raw) > max_chars,
        }

    def _crawl_tavily(self, *, settings: dict[str, Any], url: str, instructions: str, max_pages: int, max_chars: int, timeout_seconds: int) -> dict[str, Any]:
        api_key = _resolve_secret(settings.get("tavily_api_key") or settings.get("api_key") or "$TAVILY_API_KEY")
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not configured")
        payload: dict[str, Any] = {
            "url": url,
            "limit": max_pages,
            "max_depth": int(settings.get("tavily_crawl_max_depth", 1) or 1),
            "format": str(settings.get("tavily_crawl_format") or "markdown"),
        }
        if instructions:
            payload["instructions"] = instructions
        response = _json_request(
            "https://api.tavily.com/crawl",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        items = _normalize_crawl_documents(response.get("results") or response.get("data") or [], provider="tavily", base_url=url)
        return _crawl_payload(provider="tavily", url=url, items=_filter_crawl_items(items, instructions=instructions, max_pages=max_pages), instructions=instructions, max_chars=max_chars)

    def _fetch_jina(self, *, settings: dict[str, Any], url: str, timeout_seconds: int, max_chars: int) -> dict[str, Any]:
        jina_url = "https://r.jina.ai/http://" + url.removeprefix("http://").removeprefix("https://")
        if url.startswith("https://"):
            jina_url = "https://r.jina.ai/http://" + url[len("https://") :]
        headers = {}
        token = _resolve_secret(settings.get("jina_api_key") or "$JINA_API_KEY")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        content = _text_request(jina_url, headers=headers, timeout=timeout_seconds)
        return {
            "provider": "jina",
            "url": url,
            "final_url": url,
            "status": 200,
            "content_type": "text/markdown",
            "title": "",
            "content": _limit_text(_scrub_text(content), max_chars),
            "truncated": len(content) > max_chars,
        }

    def _fetch_firecrawl(self, *, settings: dict[str, Any], url: str, timeout_seconds: int, max_chars: int) -> dict[str, Any]:
        api_key = _resolve_secret(settings.get("firecrawl_api_key") or "$FIRECRAWL_API_KEY")
        if not api_key:
            raise ValueError("FIRECRAWL_API_KEY is not configured")
        base_url = _resolve_optional_url(
            settings.get("firecrawl_base_url"),
            env_name="FIRECRAWL_API_URL",
            default="https://api.firecrawl.dev",
        ).rstrip("/")
        response = _json_request(
            f"{base_url}/v1/scrape",
            method="POST",
            payload={"url": url, "formats": ["markdown"]},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        data = response.get("data") if isinstance(response.get("data"), dict) else response
        raw = str(data.get("markdown") or data.get("content") or "")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return {
            "provider": "firecrawl",
            "url": url,
            "final_url": metadata.get("sourceURL") or url,
            "status": metadata.get("statusCode") or 200,
            "content_type": "text/markdown",
            "title": metadata.get("title") or "",
            "content": _limit_text(_scrub_text(raw), max_chars),
            "truncated": len(raw) > max_chars,
        }

    def _crawl_firecrawl(self, *, settings: dict[str, Any], url: str, instructions: str, max_pages: int, max_chars: int, timeout_seconds: int) -> dict[str, Any]:
        api_key = _resolve_secret(settings.get("firecrawl_api_key") or "$FIRECRAWL_API_KEY")
        if not api_key:
            raise ValueError("FIRECRAWL_API_KEY is not configured")
        base_url = _resolve_optional_url(
            settings.get("firecrawl_base_url"),
            env_name="FIRECRAWL_API_URL",
            default="https://api.firecrawl.dev",
        ).rstrip("/")
        payload = {"url": url, "limit": max_pages, "scrapeOptions": {"formats": ["markdown"]}}
        response = _json_request(
            f"{base_url}/v1/crawl",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        job_id = str(response.get("id") or response.get("jobId") or "")
        if job_id and not (response.get("data") or response.get("results")):
            response = _poll_firecrawl_crawl(
                base_url=base_url,
                job_id=job_id,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=_bounded_int(
                    settings.get("firecrawl_poll_interval_seconds", 2),
                    default=2,
                    minimum=1,
                    maximum=30,
                ),
            )
        items = _normalize_crawl_documents(response.get("data") or response.get("results") or [], provider="firecrawl", base_url=url)
        return _crawl_payload(provider="firecrawl", url=url, items=_filter_crawl_items(items, instructions=instructions, max_pages=max_pages), instructions=instructions, max_chars=max_chars)

    def _fetch_direct(self, *, settings: dict[str, Any], url: str, timeout_seconds: int, max_chars: int) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": str(settings.get("user_agent") or "AnvilWebTools/1.0")})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_bytes = response.read(int(settings.get("max_response_bytes", 2_000_000) or 2_000_000))
            charset = response.headers.get_content_charset() or "utf-8"
            body = raw_bytes.decode(charset, errors="replace")
            content_type = response.headers.get("Content-Type")
            final_url = response.geturl()
            status = getattr(response, "status", None)
        title = _extract_title(body)
        normalized = _html_to_text(body) if "html" in (content_type or "").lower() or "<html" in body[:1000].lower() else body
        normalized = _scrub_text(normalized)
        return {
            "provider": "direct",
            "url": url,
            "final_url": final_url,
            "status": status,
            "content_type": content_type,
            "title": title,
            "content": _limit_text(normalized, max_chars),
            "truncated": len(normalized) > max_chars,
        }

    def _crawl_direct(self, *, config_result: ConfigResolutionResult, url: str, instructions: str, max_pages: int, max_chars: int, timeout_seconds: int) -> dict[str, Any]:
        root = self.extract(
            config_result=config_result,
            url=url,
            format="text",
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
            include_links=True,
            include_images=False,
        )
        root_item = root.get("items", [{}])[0] if isinstance(root.get("items"), list) and root.get("items") else {}
        candidate_urls = []
        seen_urls = {str(root_item.get("final_url") or root_item.get("url") or url)}
        for link in root_item.get("links", []):
            if not isinstance(link, dict):
                continue
            candidate = str(link.get("url") or "")
            if not candidate or candidate in seen_urls or not _same_origin(url, candidate):
                continue
            seen_urls.add(candidate)
            candidate_urls.append(candidate)
        items = [root_item]
        for candidate in candidate_urls:
            if len(items) >= max_pages:
                break
            extracted = self.extract(
                config_result=config_result,
                url=candidate,
                format="text",
                timeout_seconds=timeout_seconds,
                max_chars=max(500, max_chars // max_pages),
            )
            if isinstance(extracted.get("items"), list) and extracted["items"]:
                items.append(extracted["items"][0])
        items = _filter_crawl_items(items, instructions=instructions, max_pages=max_pages)
        return _crawl_payload(provider="direct", url=url, items=items, instructions=instructions, max_chars=max_chars)

    def _image_search_wikimedia(self, *, settings: dict[str, Any], query: str, max_results: int) -> dict[str, Any]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",
            "gsrlimit": str(max_results),
            "prop": "imageinfo",
            "iiprop": "url|mime|size",
        }
        url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
        payload = _json_request(url, timeout=int(settings.get("timeout_seconds", 20) or 20))
        pages = ((payload.get("query") or {}).get("pages") or {}).values()
        results = []
        for page in pages:
            imageinfo = page.get("imageinfo") or []
            if not imageinfo:
                continue
            first = imageinfo[0]
            results.append(
                {
                    "title": page.get("title"),
                    "url": first.get("url"),
                    "description_url": first.get("descriptionurl"),
                    "mime": first.get("mime"),
                    "width": first.get("width"),
                    "height": first.get("height"),
                }
            )
            if len(results) >= max_results:
                break
        return _result_payload(provider="wikimedia", query=query, results=results)


def _web_tools_settings(config_result: ConfigResolutionResult) -> dict[str, Any]:
    raw = config_result.effective_config.additional_settings.get("web_tools") or config_result.effective_config.additional_settings.get("web") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _provider_order(settings: dict[str, Any], operation: str) -> list[str]:
    specific_key = f"{operation}_providers"
    raw = settings.get(specific_key)
    if raw is None and operation == "fetch":
        raw = settings.get("extract_providers")
    if raw is None:
        raw = settings.get("providers")
    if raw is None:
        provider = settings.get(f"{operation}_provider") or settings.get("provider") or settings.get("backend")
        if provider:
            raw = [provider]
    if raw is None:
        if operation == "search":
            raw = ["tavily", "exa", "searxng", "duckduckgo_html"]
        elif operation == "fetch":
            raw = ["tavily", "jina", "firecrawl", "direct"]
        elif operation == "crawl":
            raw = ["tavily", "firecrawl", "direct"]
        else:
            raw = ["searxng", "wikimedia"]
    if isinstance(raw, str):
        raw = [raw]
    values = [str(item).strip().lower().replace("-", "_") for item in raw if str(item).strip()]
    aliases = {
        "duckduckgo": "duckduckgo_html",
        "ddg": "duckduckgo_html",
        "html": "html",
        "jina_ai": "jina",
        "commons": "wikimedia",
    }
    return [aliases.get(value, value) for value in values]


def _result_payload(
    *,
    provider: str,
    query: str,
    results: list[Any],
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": provider,
        "query": query,
        "total_results": len(results),
        "results": _scrub_data(results),
    }
    if errors:
        payload["errors"] = errors
    return payload


def _normalize_extract_urls(*, url: str | None, urls: list[str] | None) -> list[str]:
    values: list[str] = []
    if isinstance(url, str) and url.strip():
        values.append(url)
    if isinstance(urls, list):
        values.extend(item for item in urls if isinstance(item, str) and item.strip())
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        parsed = _validate_url(value)
        if parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    if not normalized:
        raise ValueError("web_extract requires url or urls")
    return normalized


def _extract_item_from_fetch(
    fetched: dict[str, Any],
    *,
    source_url: str,
    output_format: str,
    max_chars: int,
    include_links: bool,
    include_images: bool,
) -> dict[str, Any]:
    final_url = str(fetched.get("final_url") or fetched.get("url") or source_url)
    html = fetched.get("html") if isinstance(fetched.get("html"), str) else ""
    content = str(fetched.get("content") or "")
    if html:
        extracted_text = _html_to_text(html)
        description = _extract_meta_description(html)
        links = _extract_links(html, final_url) if include_links else []
        images = _extract_images(html, final_url) if include_images else []
    else:
        extracted_text = content
        description = str(fetched.get("description") or "")
        links = list(fetched.get("links") or []) if include_links and isinstance(fetched.get("links"), list) else []
        images = list(fetched.get("images") or []) if include_images and isinstance(fetched.get("images"), list) else []
    extracted_text = _limit_text(_scrub_text(extracted_text), max_chars)
    rendered = _render_item_text(title=str(fetched.get("title") or _extract_title(html)), content=extracted_text, output_format=output_format)
    item: dict[str, Any] = {
        "url": source_url,
        "final_url": final_url,
        "title": str(fetched.get("title") or _extract_title(html)),
        "description": description,
        "content": extracted_text if output_format in {"text", "json"} else rendered,
        "provider": fetched.get("provider") or "unknown",
        "status": fetched.get("status"),
        "content_type": fetched.get("content_type"),
        "truncated": bool(fetched.get("truncated")) or len(extracted_text) >= max_chars,
    }
    if include_links:
        item["links"] = links[:50]
    if include_images:
        item["images"] = images[:50]
    return _drop_empty(item)


def _render_item_text(*, title: str, content: str, output_format: str) -> str:
    if output_format == "markdown" and title:
        return f"# {title}\n\n{content}".strip()
    return content


def _render_extracted_content(items: list[dict[str, Any]], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(items, ensure_ascii=False)
    chunks = []
    for item in items:
        title = str(item.get("title") or item.get("final_url") or item.get("url") or "")
        content = str(item.get("content") or "")
        if output_format == "markdown":
            chunks.append(content if content.startswith("#") else f"# {title}\n\n{content}".strip())
        else:
            chunks.append(content)
    return "\n\n---\n\n".join(chunk for chunk in chunks if chunk)


def _extract_meta_description(value: str) -> str:
    match = META_DESCRIPTION_RE.search(value)
    return html_lib.unescape(match.group("content")).strip() if match else ""


def _extract_links(value: str, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for match in ANCHOR_HREF_RE.finditer(value):
        attrs = _attrs_dict(match.group("attrs"))
        href = attrs.get("href", "").strip()
        text = _html_to_text(match.group("body"))
        if not href:
            continue
        links.append({"url": urllib.parse.urljoin(base_url, href), "text": text})
    return links


def _extract_images(value: str, base_url: str) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for match in IMG_RE.finditer(value):
        attrs = _attrs_dict(match.group("attrs"))
        src = attrs.get("src", "").strip()
        if not src:
            continue
        item: dict[str, Any] = {
            "url": urllib.parse.urljoin(base_url, src),
            "alt": attrs.get("alt", ""),
        }
        for key in ("width", "height"):
            if attrs.get(key, "").isdigit():
                item[key] = int(attrs[key])
        images.append(item)
    return images


def _attrs_dict(value: str) -> dict[str, str]:
    return {match.group("name").lower(): html_lib.unescape(match.group("value")) for match in ATTR_RE.finditer(value)}


def _normalize_crawl_documents(value: Any, *, provider: str, base_url: str) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("data") or value.get("results") or [value]
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        page_url = str(raw.get("url") or metadata.get("sourceURL") or metadata.get("url") or base_url)
        content = str(raw.get("markdown") or raw.get("content") or raw.get("raw_content") or raw.get("text") or "")
        title = str(raw.get("title") or metadata.get("title") or "")
        links = raw.get("links") if isinstance(raw.get("links"), list) else []
        items.append(
            _drop_empty(
                {
                    "url": page_url,
                    "final_url": page_url,
                    "title": title,
                    "content": _scrub_text(content),
                    "provider": provider,
                    "status": metadata.get("statusCode") or raw.get("status"),
                    "links": links,
                }
            )
        )
    return items


def _filter_crawl_items(items: list[Any], *, instructions: str, max_pages: int) -> list[dict[str, Any]]:
    normalized = [_normalize_crawl_item(item) for item in items if isinstance(item, dict)]
    terms = _instruction_terms(instructions)
    if terms:
        matched = [item for item in normalized if _crawl_item_matches(item, terms)]
        if matched:
            normalized = matched
    return normalized[:max_pages]


def _normalize_crawl_item(item: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "url": str(item.get("url") or item.get("final_url") or ""),
            "final_url": str(item.get("final_url") or item.get("url") or ""),
            "title": str(item.get("title") or ""),
            "content": _scrub_text(str(item.get("content") or item.get("markdown") or item.get("text") or "")),
            "description": str(item.get("description") or ""),
            "provider": item.get("provider"),
            "status": item.get("status"),
            "links": item.get("links") if isinstance(item.get("links"), list) else [],
        }
    )


def _instruction_terms(instructions: str) -> set[str]:
    stop = {"and", "the", "for", "with", "find", "about", "page", "pages", "site", "crawl", "extract"}
    return {term for term in re.findall(r"[A-Za-z0-9_]{3,}", instructions.lower()) if term not in stop}


def _crawl_item_matches(item: dict[str, Any], terms: set[str]) -> bool:
    haystack = " ".join(str(item.get(key) or "") for key in ("url", "title", "description", "content")).lower()
    return any(term in haystack for term in terms)


def _crawl_payload(
    *,
    provider: str,
    url: str,
    items: list[dict[str, Any]],
    instructions: str,
    max_chars: int,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    per_item_budget = max(500, max_chars // max(len(items), 1))
    bounded_items: list[dict[str, Any]] = []
    item_truncated = False
    for item in items:
        bounded = dict(item)
        content = str(bounded.get("content") or "")
        if len(content) > per_item_budget:
            bounded["content"] = _limit_text(content, per_item_budget)
            bounded["truncated"] = True
            item_truncated = True
        elif bounded.get("truncated"):
            item_truncated = True
        bounded_items.append(bounded)

    content = _limit_text(
        "\n\n---\n\n".join(
            f"# {item.get('title') or item.get('final_url') or item.get('url')}\n\n{item.get('content') or ''}".strip()
            for item in bounded_items
            if item.get("content")
        ),
        max_chars,
    )
    payload: dict[str, Any] = {
        "provider": provider,
        "url": url,
        "instructions": instructions,
        "pages_crawled": len(bounded_items),
        "content": content,
        "items": bounded_items,
        "truncated": len(content) >= max_chars or item_truncated,
    }
    if errors:
        payload["errors"] = errors
    return _scrub_data(payload)


def _same_origin(left: str, right: str) -> bool:
    left_parsed = urllib.parse.urlparse(left)
    right_parsed = urllib.parse.urlparse(right)
    return bool(left_parsed.netloc and left_parsed.scheme in {"http", "https"} and left_parsed.netloc == right_parsed.netloc)


def _poll_firecrawl_crawl(
    *,
    base_url: str,
    job_id: str,
    api_key: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, timeout_seconds)
    last_response: dict[str, Any] = {"id": job_id, "status": "started", "data": []}
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_seconds, remaining))
        last_response = _json_request(
            f"{base_url}/v1/crawl/{urllib.parse.quote(job_id, safe='')}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=max(1, int(min(timeout_seconds, remaining))),
        )
        status = str(last_response.get("status") or "").lower()
        if status in {"completed", "failed", "cancelled"}:
            break
        if last_response.get("data"):
            break
    return last_response


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _with_provider_failures(payload: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    if errors:
        return {**payload, "provider_failures": errors}
    return payload


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", "User-Agent": "AnvilWebTools/1.0", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(body or "{}")
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _text_request(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "AnvilWebTools/1.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must include http:// or https:// and a host")
    return urllib.parse.urlunparse(parsed)


def _html_to_text(value: str) -> str:
    without_blocks = HTML_SCRIPT_STYLE_RE.sub(" ", value)
    without_tags = HTML_TAG_RE.sub(" ", without_blocks)
    collapsed = re.sub(r"\s+", " ", html_lib.unescape(without_tags)).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", collapsed)


def _extract_title(value: str) -> str:
    match = TITLE_RE.search(value)
    return _html_to_text(match.group("title")) if match else ""


def _resolve_secret(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1], "")
    if text.startswith("$"):
        return os.getenv(text[1:], "")
    return text


def _resolve_optional_url(value: Any, *, env_name: str, default: str = "") -> str:
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith("${") and text.endswith("}"):
            return os.getenv(text[2:-1], "") or default
        if text.startswith("$"):
            return os.getenv(text[1:], "") or default
        return text
    return os.getenv(env_name, "") or default


def _error_payload(provider: str, exc: Exception) -> dict[str, str]:
    message = str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        message = f"HTTP {exc.code}: {exc.reason}"
    return {"provider": provider, "error": _scrub_text(message)[:500]}


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _limit_text(value: str, max_chars: int) -> str:
    return value[:max_chars]


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
