from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]

AGENT_WRITABLE_TARGET_PREFIXES = (
    "/mnt/",
    "/mnt/user-data",
    "/mnt/host-workspaces",
    "/app/.anvil",
    "/app/backend/.anvil",
)

SOURCE_TREE_BIND_PREFIXES = (
    ".",
    "./backend",
    "./frontend",
    "./docs",
    "./examples",
    "./skills",
    "./config.yaml",
)

ALLOWED_STATE_BIND_PREFIXES = (
    "./.anvil",
    "./.omx/reports",
)

DEFAULT_COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    "compose.yml",
    "compose.yaml",
    "compose.override.yml",
    "compose.override.yaml",
)


def discover_default_compose_files(repo_root: Path | None = None) -> list[Path]:
    root = repo_root or REPO_ROOT
    return [
        root / filename
        for filename in DEFAULT_COMPOSE_FILENAMES
        if (root / filename).exists()
    ]


def find_mount_safety_violations(compose_path: Path) -> list[str]:
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    if not isinstance(compose, dict):
        return [f"{compose_path}: expected a compose mapping root"]

    violations: list[str] = []
    services = compose.get("services", {})
    if not isinstance(services, dict):
        return violations

    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        volumes = service.get("volumes", [])
        if not isinstance(volumes, list):
            continue
        for volume in volumes:
            parsed = parse_compose_volume(volume)
            if parsed is None:
                continue
            source, target, mode = parsed
            if not is_agent_writable_target(target):
                continue
            if is_read_only(mode):
                continue
            if is_repo_source_tree_bind(source):
                violations.append(f"{service_name}: {source}:{target}:{mode or 'rw'}")

    return violations


def parse_compose_volume(volume: Any) -> tuple[str, str, str | None] | None:
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) < 2:
            return None
        mode = parts[2] if len(parts) >= 3 else None
        return parts[0], parts[1], mode
    if isinstance(volume, dict):
        source = volume.get("source")
        target = volume.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            return None
        mode = "ro" if volume.get("read_only") is True else None
        if isinstance(volume.get("mode"), str):
            mode = volume["mode"]
        return source, target, mode
    return None


def is_agent_writable_target(target: str) -> bool:
    normalized = _normalize_compose_path(target)
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(f"{prefix.rstrip('/')}/")
        for prefix in AGENT_WRITABLE_TARGET_PREFIXES
    )


def is_read_only(mode: str | None) -> bool:
    if mode is None:
        return False
    return "ro" in {item.strip().lower() for item in mode.split(",")}


def is_repo_source_tree_bind(source: str) -> bool:
    normalized = _normalize_compose_path(source)
    if not normalized.startswith("."):
        return False
    if any(
        normalized == prefix.rstrip("/") or normalized.startswith(f"{prefix.rstrip('/')}/")
        for prefix in ALLOWED_STATE_BIND_PREFIXES
    ):
        return False
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(f"{prefix.rstrip('/')}/")
        for prefix in SOURCE_TREE_BIND_PREFIXES
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if Docker compose mounts source-tree paths rw into agent-writable targets."
    )
    parser.add_argument(
        "compose_files",
        nargs="*",
        type=Path,
        help=(
            "Compose files to inspect. Defaults to existing docker-compose/compose "
            "base and override files in the repository root."
        ),
    )
    args = parser.parse_args(argv)
    compose_files = args.compose_files or discover_default_compose_files()
    if not compose_files:
        print("No Docker compose files found to inspect.")
        return 0

    violations: list[str] = []
    for compose_file in compose_files:
        violations.extend(find_mount_safety_violations(compose_file))

    if violations:
        print("Unsafe Docker mount(s) detected:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print("Docker mount safety check passed.")
    return 0


def _normalize_compose_path(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    return normalized or "."


if __name__ == "__main__":
    raise SystemExit(main())
