from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export an Anvil evaluation report from a running gateway.")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18000", help="Gateway base URL.")
    parser.add_argument("--thread-id", action="append", default=[], help="Thread id to include. Repeat for multiple threads.")
    parser.add_argument("--evaluator-results", default=None, help="JSON file mapping thread id to evaluator result.")
    parser.add_argument("--output-json", default=None, help="Write raw JSON response to this path.")
    parser.add_argument("--output-md", default=None, help="Ask gateway to write Markdown report to this path and also mirror Markdown locally when returned.")
    parser.add_argument("--include-markdown", action="store_true", help="Include Markdown in the JSON response.")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evaluator_results = _read_json_file(args.evaluator_results) if args.evaluator_results else {}
    body = {
        "thread_ids": list(dict.fromkeys(args.thread_id)),
        "options": {"include_markdown": bool(args.include_markdown or args.output_md)},
        "evaluator_results": evaluator_results,
        "write_markdown": bool(args.output_md),
        "output_path": args.output_md,
    }
    payload = _post_json(f"{args.gateway_url.rstrip('/')}/threads/evaluation-report", body, timeout=args.timeout)
    if args.output_json:
        _write_text(args.output_json, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if args.output_md and payload.get("markdown"):
        _write_text(args.output_md, str(payload["markdown"]))
    if not args.output_json and not args.output_md:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({
            "report_id": payload.get("report_id"),
            "thread_count": payload.get("summary", {}).get("thread_count"),
            "score": payload.get("score"),
            "markdown_path": payload.get("markdown_path") or args.output_md,
        }, ensure_ascii=False, sort_keys=True))
    return 0


def _read_json_file(path: str) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"evaluator results must be a JSON object: {path}")
    return payload


def _post_json(url: str, body: dict[str, object], *, timeout: float) -> dict[str, object]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"gateway returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"gateway request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("gateway returned a non-object JSON payload")
    return payload


def _write_text(path: str, text: str) -> None:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
