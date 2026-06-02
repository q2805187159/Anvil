from __future__ import annotations

import argparse
import os

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Anvil gateway.")
    parser.add_argument("--host", default=os.getenv("ANVIL_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.getenv("ANVIL_GATEWAY_PORT", "18000")), type=int)
    parser.add_argument("--reload", action="store_true")
    return parser


def run_gateway(*, host: str = "127.0.0.1", port: int = 18000, reload: bool = False) -> None:
    uvicorn.run("app.gateway.app:app", host=host, port=port, reload=reload)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_gateway(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
