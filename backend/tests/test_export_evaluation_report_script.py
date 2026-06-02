from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "export-evaluation-report.py"
    spec = importlib.util.spec_from_file_location("export_evaluation_report_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_evaluation_report_script_main_with_args(contract_tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_script_module()
    evaluator_path = contract_tmp_path / "evaluator.json"
    output_json = contract_tmp_path / "report.json"
    output_md = contract_tmp_path / "report.md"
    evaluator_path.write_text(
        json.dumps({"thread-1": {"evaluator": "terminal-bench", "score": 1.0, "passed": True}}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_post_json(url, body, *, timeout):
        captured["url"] = url
        captured["body"] = body
        captured["timeout"] = timeout
        return {
            "report_id": "batch-eval-test",
            "score": 1.0,
            "summary": {"thread_count": 1},
            "markdown_path": str(output_md),
            "markdown": "# Anvil Evaluation Report\n",
        }

    monkeypatch.setattr(module, "_post_json", fake_post_json)
    monkeypatch.setattr(
        "sys.argv",
        [
            "export-evaluation-report.py",
            "--gateway-url",
            "http://gateway.test",
            "--thread-id",
            "thread-1",
            "--evaluator-results",
            str(evaluator_path),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
            "--timeout",
            "5",
        ],
    )

    assert module.main() == 0

    assert captured["url"] == "http://gateway.test/threads/evaluation-report"
    body = captured["body"]
    assert body["thread_ids"] == ["thread-1"]
    assert body["options"] == {"include_markdown": True}
    assert body["evaluator_results"]["thread-1"]["evaluator"] == "terminal-bench"
    assert body["write_markdown"] is True
    assert body["output_path"] == str(output_md)
    assert output_json.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["report_id"] == "batch-eval-test"
    assert output_md.read_text(encoding="utf-8").startswith("# Anvil Evaluation Report")
    stdout = capsys.readouterr().out
    assert "batch-eval-test" in stdout
