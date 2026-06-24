from __future__ import annotations

import json

from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.sandbox import PathService, create_sandbox_provider
from anvil.web_tools import WebToolsService


def _config(web_tools: dict[str, object]):
    return ConfigService().resolve(
        [
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "gpt-5.4",
                        }
                    },
                    "web_tools": web_tools,
                },
            )
        ]
    )


def test_web_tools_use_mock_contracts_without_network() -> None:
    config_result = _config(
        {
            "mock_search_results": {
                "anvil": [
                    {"title": "Anvil", "url": "https://example.com/anvil", "snippet": "runtime"},
                    {"title": "Other", "url": "https://example.com/other"},
                ]
            },
            "mock_fetch_results": {
                "https://example.com/anvil": {"title": "Anvil page", "content": "hello"}
            },
            "mock_image_results": {
                "anvil": [{"title": "Anvil image", "url": "https://example.com/anvil.png"}]
            },
        }
    )
    service = WebToolsService()

    search = service.search(config_result=config_result, query="anvil", max_results=1)
    fetch = service.fetch(config_result=config_result, url="https://example.com/anvil")
    images = service.image_search(config_result=config_result, query="anvil")

    assert search == {
        "provider": "mock",
        "query": "anvil",
        "total_results": 1,
        "results": [{"title": "Anvil", "url": "https://example.com/anvil", "snippet": "runtime"}],
    }
    assert fetch["provider"] == "mock"
    assert fetch["content"] == "hello"
    assert images["results"][0]["url"] == "https://example.com/anvil.png"


def test_web_extract_builds_readable_payload_with_links_and_images() -> None:
    config_result = _config(
        {
            "mock_fetch_results": {
                "https://example.com/anvil": {
                    "title": "Anvil page",
                    "html": """
                    <html>
                      <head><meta name="description" content="Harness docs"></head>
                      <body>
                        <script>ignore()</script>
                        <h1>Anvil</h1>
                        <p>Hello <strong>runtime</strong>.</p>
                        <a href="/docs">Docs</a>
                        <img src="/hero.png" alt="Hero" width="640" height="360">
                      </body>
                    </html>
                    """,
                },
                "https://example.com/plain": {"content": "Plain text body"},
            }
        }
    )

    payload = WebToolsService().extract(
        config_result=config_result,
        urls=["https://example.com/anvil", "https://example.com/plain"],
        include_links=True,
        include_images=True,
        max_chars=4000,
    )

    assert payload["provider"] == "mock"
    assert payload["url_count"] == 2
    assert payload["items"][0]["title"] == "Anvil page"
    assert "Hello runtime." in payload["items"][0]["content"]
    assert "ignore()" not in payload["items"][0]["content"]
    assert payload["items"][0]["description"] == "Harness docs"
    assert payload["items"][0]["links"] == [{"url": "https://example.com/docs", "text": "Docs"}]
    assert payload["items"][0]["images"] == [
        {"url": "https://example.com/hero.png", "alt": "Hero", "width": 640, "height": 360}
    ]
    assert payload["items"][1]["content"] == "Plain text body"
    assert "Plain text body" in payload["content"]


def test_web_tools_fail_over_and_scrub_provider_errors(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    config_result = _config(
        {
            "search_providers": ["tavily", "duckduckgo_html"],
            "timeout_seconds": 5,
        }
    )

    def fake_text_request(url: str, *, headers=None, timeout: int = 20) -> str:
        return """
        <html><body>
          <a class="result__a" href="https://example.com">Example</a>
          <div class="result__snippet">hello sk-testsecretsecretsecret</div>
        </body></html>
        """

    monkeypatch.setattr("anvil.web_tools.service._text_request", fake_text_request)

    payload = WebToolsService().search(config_result=config_result, query="fallback")

    assert payload["provider"] == "duckduckgo_html"
    assert payload["results"][0]["snippet"] == "hello [REDACTED]"
    assert payload["provider_failures"][0]["provider"] == "tavily"
    assert "TAVILY_API_KEY" in payload["provider_failures"][0]["error"]


def test_web_tools_do_not_treat_unresolved_optional_url_env_refs_as_urls(monkeypatch) -> None:
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    config_result = _config(
        {
            "image_providers": ["searxng"],
            "searxng_base_url": "$SEARXNG_BASE_URL",
        }
    )

    payload = WebToolsService().image_search(config_result=config_result, query="anvil", max_results=1)

    assert payload["provider"] == "unavailable"
    assert payload["errors"] == [
        {"provider": "searxng", "error": "SEARXNG_BASE_URL is not configured"}
    ]


def test_web_tools_firecrawl_base_url_env_ref_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_URL", raising=False)
    config_result = _config(
        {
            "fetch_providers": ["firecrawl"],
            "firecrawl_api_key": "test-firecrawl-key",
            "firecrawl_base_url": "$FIRECRAWL_API_URL",
        }
    )
    captured: dict[str, object] = {}

    def fake_json_request(url: str, *, method: str = "GET", payload=None, headers=None, timeout: int = 20):
        captured["url"] = url
        captured["method"] = method
        captured["payload"] = payload
        captured["headers"] = headers
        return {"data": {"markdown": "Firecrawl body", "metadata": {"title": "Firecrawl title"}}}

    monkeypatch.setattr("anvil.web_tools.service._json_request", fake_json_request)

    payload = WebToolsService().fetch(config_result=config_result, url="https://example.com")

    assert captured["url"] == "https://api.firecrawl.dev/v1/scrape"
    assert payload["provider"] == "firecrawl"
    assert payload["title"] == "Firecrawl title"


def test_runtime_web_tool_handlers_are_visible_and_use_service_contract(contract_tmp_path) -> None:
    config_result = _config(
        {
            "mock_search_results": {"anvil": [{"title": "Anvil", "url": "https://example.com/anvil"}]},
            "mock_fetch_results": {"https://example.com/anvil": {"content": "Anvil page"}},
            "mock_image_results": {"anvil": [{"title": "Anvil image", "url": "https://example.com/anvil.png"}]},
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-web-tools", path_service=path_service)

    result = CapabilityAssemblyService().assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    assert {"web_search", "web_fetch", "web_extract", "web_crawl", "image_search", "js_repl"}.issubset(handlers)
    assert json.loads(handlers["web_search"].invoke({"query": "anvil"}))["provider"] == "mock"
    assert json.loads(handlers["web_fetch"].invoke({"url": "https://example.com/anvil"}))["content"] == "Anvil page"
    extract_payload = json.loads(handlers["web_extract"].invoke({"url": "https://example.com/anvil"}))
    assert "Anvil page" in extract_payload["content"]
    assert extract_payload["items"][0]["content"] == "Anvil page"


def test_runtime_web_extract_accepts_legacy_urls_list(contract_tmp_path) -> None:
    config_result = _config(
        {
            "mock_fetch_results": {
                "https://example.com/a": {"title": "A", "content": "Alpha"},
                "https://example.com/b": {"title": "B", "content": "Beta"},
            },
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-web-extract-list", path_service=path_service)

    result = CapabilityAssemblyService().assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    payload = json.loads(
        handlers["web_extract"].invoke(
            {"urls": ["https://example.com/a", "https://example.com/b"], "format": "text"}
        )
    )

    assert payload["url_count"] == 2
    assert [item["title"] for item in payload["items"]] == ["A", "B"]
    assert "Alpha" in payload["content"]
    assert "Beta" in payload["content"]


def test_web_crawl_uses_mock_pages_and_filters_by_instruction_terms() -> None:
    config_result = _config(
        {
            "mock_crawl_results": {
                "https://example.com": [
                    {
                        "url": "https://example.com",
                        "title": "Home",
                        "content": "Welcome. See docs and pricing.",
                        "links": [{"url": "https://example.com/docs", "text": "Docs"}],
                    },
                    {
                        "url": "https://example.com/docs",
                        "title": "Docs",
                        "content": "Install guide and API reference.",
                    },
                    {
                        "url": "https://example.com/pricing",
                        "title": "Pricing",
                        "content": "Plans and billing.",
                    },
                ]
            }
        }
    )

    payload = WebToolsService().crawl(
        config_result=config_result,
        url="https://example.com",
        instructions="Find docs and API reference",
        max_pages=5,
    )

    assert payload["provider"] == "mock"
    assert payload["pages_crawled"] == 2
    assert [item["title"] for item in payload["items"]] == ["Home", "Docs"]
    assert "Pricing" not in payload["content"]


def test_runtime_web_crawl_tool_is_visible_and_uses_service_contract(contract_tmp_path) -> None:
    config_result = _config(
        {
            "mock_crawl_results": {
                "https://example.com": [
                    {"url": "https://example.com", "title": "Home", "content": "Welcome"},
                ]
            }
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-web-crawl", path_service=path_service)

    result = CapabilityAssemblyService().assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    payload = json.loads(handlers["web_crawl"].invoke({"url": "https://example.com"}))

    assert payload["provider"] == "mock"
    assert payload["pages_crawled"] == 1
    assert payload["items"][0]["title"] == "Home"
