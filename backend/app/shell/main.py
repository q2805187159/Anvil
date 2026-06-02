from __future__ import annotations

from pathlib import Path
import argparse

from app.sdk import EmbeddedClientConfig

from .profile import bootstrap_profile_home, read_active_profile
from .tui import AnvilShell


def run_shell(
    *,
    profile_name: str | None = None,
    anvil_home: Path | None = None,
    client_config: EmbeddedClientConfig | None = None,
) -> None:
    resolved_profile = profile_name or read_active_profile(anvil_home=anvil_home)
    profile = bootstrap_profile_home(resolved_profile, anvil_home=anvil_home)
    shell = AnvilShell(profile=profile, client_config=client_config)
    try:
        shell.run_interactive()
    finally:
        shell.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Anvil shell.")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--anvil-home", default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_shell(
        profile_name=args.profile,
        anvil_home=Path(args.anvil_home).expanduser().resolve() if args.anvil_home else None,
    )


if __name__ == "__main__":
    main()
