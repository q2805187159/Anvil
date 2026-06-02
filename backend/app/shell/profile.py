from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from anvil.config import bootstrap_anvil_profile_home, get_anvil_home as resolve_anvil_home


PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ShellProfile:
    name: str
    home: Path
    config_path: Path
    cache_dir: Path
    log_dir: Path
    sessions_dir: Path


def get_anvil_home(*, anvil_home: Path | None = None) -> Path:
    if anvil_home is not None:
        return Path(anvil_home).resolve()
    return resolve_anvil_home()


def resolve_profile_home(profile_name: str = "default", *, anvil_home: Path | None = None) -> Path:
    validate_profile_name(profile_name)
    root = get_anvil_home(anvil_home=anvil_home)
    if profile_name == "default":
        return root
    return root / "profiles" / profile_name


def bootstrap_profile_home(profile_name: str = "default", *, anvil_home: Path | None = None) -> ShellProfile:
    profile_home = bootstrap_anvil_profile_home(profile_name, anvil_home=anvil_home)

    cache_dir = profile_home / "cache"
    log_dir = profile_home / "logs"
    sessions_dir = profile_home / "sessions"
    config_path = profile_home / "config.yaml"

    return ShellProfile(
        name=profile_name,
        home=profile_home,
        config_path=config_path,
        cache_dir=cache_dir,
        log_dir=log_dir,
        sessions_dir=sessions_dir,
    )


def read_active_profile(*, anvil_home: Path | None = None) -> str:
    path = _active_profile_path(anvil_home=anvil_home)
    if not path.exists():
        return "default"
    value = path.read_text(encoding="utf-8").strip()
    return value or "default"


def write_active_profile(profile_name: str, *, anvil_home: Path | None = None) -> None:
    validate_profile_name(profile_name)
    root = get_anvil_home(anvil_home=anvil_home)
    root.mkdir(parents=True, exist_ok=True)
    _active_profile_path(anvil_home=root).write_text(profile_name, encoding="utf-8")


def validate_profile_name(profile_name: str) -> None:
    if profile_name == "default":
        return
    if not PROFILE_RE.fullmatch(profile_name):
        raise ValueError(f"invalid profile name: {profile_name!r}")


def _active_profile_path(*, anvil_home: Path | None = None) -> Path:
    return get_anvil_home(anvil_home=anvil_home) / "active_profile"
