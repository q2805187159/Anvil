from __future__ import annotations

import json

from anvil.browser_tools import BrowserToolsService
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.sandbox import PathService, create_sandbox_provider

RED_PIXEL_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
BLUE_PIXEL_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/5ncLrgAAAABJRU5ErkJggg=="


def _config(browser_tools: dict[str, object]):
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
                    "browser_tools": browser_tools,
                },
            )
        ]
    )


def test_browser_tools_use_mock_contracts_without_network(contract_tmp_path) -> None:
    config_result = _config(
        {
            "provider": "mock",
            "allow_private_urls": True,
            "mock_pages": {
                "https://example.com/app": {
                    "title": "Example App",
                    "snapshot": "Title: Example App\nInteractive elements:\n[@e1] input Search",
                    "images": [{"src": "https://example.com/logo.png", "alt": "logo", "width": 64, "height": 64}],
                    "console_messages": [{"level": "log", "text": "ready"}],
                    "eval_results": {"document.title": "Example App"},
                }
            },
        }
    )
    service = BrowserToolsService()
    output_path = contract_tmp_path / "threads" / "thread-browser" / "outputs" / "browser" / "shot.png"

    nav = service.navigate(config_result=config_result, session_id="default", url="https://example.com/app")
    typed = service.type_text(config_result=config_result, session_id="default", ref="@e1", text="hello")
    clicked = service.click(config_result=config_result, session_id="default", ref="@e1")
    console = service.console(config_result=config_result, session_id="default", expression="document.title")
    images = service.get_images(config_result=config_result, session_id="default")
    screenshot = service.screenshot(
        config_result=config_result,
        session_id="default",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/browser/shot.png",
    )

    assert nav["success"] is True
    assert nav["title"] == "Example App"
    assert typed["typed"] == "hello"
    assert clicked["clicked"] == "@e1"
    assert console["result"] == "Example App"
    assert images["count"] == 1
    assert screenshot["success"] is True
    assert screenshot["bytes"] > 0
    assert output_path.exists()


def test_browser_tools_report_snapshot_captures_report_evidence(contract_tmp_path) -> None:
    config_result = _config(
        {
            "provider": "mock",
            "allow_private_urls": True,
            "mock_pages": {
                "https://example.com/reports/launch-review/index.html": {
                    "title": "Launch Review",
                    "snapshot": "Title: Launch Review\nText:\nStatus: warning\nIssue: text_density_high",
                }
            },
        }
    )
    service = BrowserToolsService()
    output_path = contract_tmp_path / "threads" / "thread-browser" / "outputs" / "presentation-browser-evidence" / "launch-review" / "screenshot.png"

    report = service.report_snapshot(
        config_result=config_result,
        session_id="report",
        report_url="https://example.com/reports/launch-review/index.html",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/presentation-browser-evidence/launch-review/screenshot.png",
    )

    assert report["success"] is True
    assert report["report_url"] == "https://example.com/reports/launch-review/index.html"
    assert report["navigation"]["title"] == "Launch Review"
    assert "text_density_high" in report["snapshot"]
    assert report["bytes"] > 0
    assert output_path.exists()


def test_browser_tools_compare_report_snapshots_detects_image_and_snapshot_changes(contract_tmp_path) -> None:
    config_result = _config(
        {
            "provider": "mock",
            "allow_private_urls": True,
            "mock_pages": {
                "https://example.com/reports/baseline/index.html": {
                    "title": "Baseline Report",
                    "snapshot": "Title: Baseline Report\nText:\nStatus: passed",
                    "screenshot_base64": RED_PIXEL_PNG_BASE64,
                },
                "https://example.com/reports/candidate/index.html": {
                    "title": "Candidate Report",
                    "snapshot": "Title: Candidate Report\nText:\nStatus: warning\nIssue: render_layout_shift",
                    "screenshot_base64": BLUE_PIXEL_PNG_BASE64,
                },
            },
        }
    )
    service = BrowserToolsService()
    baseline_path = contract_tmp_path / "threads" / "thread-browser" / "outputs" / "presentation-browser-diffs" / "launch" / "baseline.png"
    candidate_path = contract_tmp_path / "threads" / "thread-browser" / "outputs" / "presentation-browser-diffs" / "launch" / "candidate.png"
    overlay_path = contract_tmp_path / "threads" / "thread-browser" / "outputs" / "presentation-browser-diffs" / "launch" / "overlay.png"

    diff = service.compare_report_snapshots(
        config_result=config_result,
        baseline_session_id="baseline",
        candidate_session_id="candidate",
        baseline_url="https://example.com/reports/baseline/index.html",
        candidate_url="https://example.com/reports/candidate/index.html",
        baseline_output_path=baseline_path,
        baseline_output_virtual_path="/mnt/user-data/outputs/presentation-browser-diffs/launch/baseline.png",
        candidate_output_path=candidate_path,
        candidate_output_virtual_path="/mnt/user-data/outputs/presentation-browser-diffs/launch/candidate.png",
        overlay_output_path=overlay_path,
        overlay_output_virtual_path="/mnt/user-data/outputs/presentation-browser-diffs/launch/overlay.png",
    )

    assert diff["success"] is True
    assert diff["status"] == "changed"
    assert diff["comparison"]["bytes_changed"] is True
    assert diff["comparison"]["pixels_changed"] is True
    assert diff["comparison"]["pixel_delta"]["available"] is True
    assert diff["comparison"]["pixel_delta"]["changed_pixels"] == 1
    assert diff["comparison"]["pixel_delta"]["top_cells"][0]["changed_pixels"] == 1
    assert diff["comparison"]["pixel_delta"]["overlay_path"] == "/mnt/user-data/outputs/presentation-browser-diffs/launch/overlay.png"
    assert diff["comparison"]["snapshot_changed"] is True
    assert "Issue: render_layout_shift" in diff["comparison"]["snapshot_delta"]["added_lines"]
    assert baseline_path.exists()
    assert candidate_path.exists()
    assert overlay_path.exists()


def test_runtime_browser_tool_handlers_are_visible_and_use_virtual_paths(contract_tmp_path) -> None:
    config_result = _config(
        {
            "provider": "mock",
            "allow_private_urls": True,
            "gateway_base_url": "http://127.0.0.1:18000",
            "mock_pages": {
                "https://example.com/app": {
                    "title": "Example App",
                    "snapshot": "Title: Example App\nInteractive elements:\n[@e1] input Search",
                },
                "http://127.0.0.1:18000/threads/thread-browser-tools/artifacts/outputs/presentation-review-reports/launch-review/index.html": {
                    "title": "Launch Review",
                    "snapshot": "Title: Launch Review\nText:\nStatus: warning\nIssue: text_density_high",
                    "screenshot_base64": RED_PIXEL_PNG_BASE64,
                },
                "http://127.0.0.1:18000/threads/thread-browser-tools/artifacts/outputs/presentation-review-reports/launch-candidate/index.html": {
                    "title": "Launch Candidate",
                    "snapshot": "Title: Launch Candidate\nText:\nStatus: warning\nIssue: render_layout_shift",
                    "screenshot_base64": BLUE_PIXEL_PNG_BASE64,
                }
            },
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-browser-tools", path_service=path_service)

    result = CapabilityAssemblyService().assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    expected = {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_back",
        "browser_press",
        "browser_console",
        "browser_get_images",
        "browser_screenshot",
        "browser_vision",
        "browser_cdp",
        "browser_dialog",
        "browser_close",
    }
    assert expected.issubset(handlers)
    assert "presentation_browser_snapshot_report" not in handlers
    assert "presentation_browser_diff_report" not in handlers
    nav = json.loads(handlers["browser_navigate"].invoke({"url": "https://example.com/app"}))
    shot = json.loads(
        handlers["browser_screenshot"].invoke(
            {
                "output_path": "/mnt/user-data/outputs/browser/shot.png",
            }
        )
    )
    vision = json.loads(handlers["browser_vision"].invoke({"question": "what is visible?"}))

    assert nav["success"] is True
    assert "Example App" in nav["snapshot"]
    assert shot["success"] is True
    assert shot["artifact_url"].endswith("/threads/thread-browser-tools/artifacts/outputs/browser/shot.png")
    assert path_service.thread_outputs_dir("thread-browser-tools").joinpath("browser", "shot.png").exists()
    assert vision["success"] is True
    assert vision["artifact_url"].endswith(".png")


def test_browser_tools_reject_secret_or_private_urls_by_default() -> None:
    config_result = _config({"provider": "mock"})
    service = BrowserToolsService()

    secret_payload = service.navigate(
        config_result=config_result,
        session_id="default",
        url="https://example.com/?api_key=sk-testsecretsecretsecret",
    )
    private_payload = service.navigate(
        config_result=config_result,
        session_id="default",
        url="http://127.0.0.1:3000",
    )

    assert secret_payload["success"] is False
    assert "[REDACTED]" not in json.dumps(secret_payload)
    assert "API key" in secret_payload["error"]
    assert private_payload["success"] is False
    assert "private" in private_payload["error"]
