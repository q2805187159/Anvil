from __future__ import annotations

import builtins
import io
import json
import os
import pathlib
import re
import subprocess
from collections.abc import Iterable
from typing import Any


def _load_mapping() -> list[tuple[str, str]]:
    raw = os.environ.get("ANVIL_VIRTUAL_PATH_MAP")
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    mappings: list[tuple[str, str]] = []
    for virtual_root, actual_root in payload.items():
        if not isinstance(virtual_root, str) or not isinstance(actual_root, str):
            continue
        virtual = virtual_root.rstrip("/")
        actual = actual_root.rstrip("\\/")
        if virtual and actual:
            mappings.append((virtual, actual))
    return sorted(mappings, key=lambda item: len(item[0]), reverse=True)


_MAPPINGS = _load_mapping()


def _virtual_to_actual(value: str) -> str:
    normalized = value
    if normalized.startswith("\\mnt\\"):
        normalized = normalized.replace("\\", "/")
    for virtual_root, actual_root in _MAPPINGS:
        if normalized == virtual_root:
            return actual_root
        if normalized.startswith(f"{virtual_root}/"):
            remainder = normalized[len(virtual_root) + 1 :]
            return os.path.join(actual_root, *remainder.split("/"))
    return value


def _translate_pathlike(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        if isinstance(value, bytes):
            try:
                return os.fsencode(_virtual_to_actual(os.fsdecode(value)))
            except Exception:
                return value
        return _virtual_to_actual(value)
    if isinstance(value, os.PathLike):
        translated = _virtual_to_actual(os.fspath(value))
        return translated
    return value


_EMBEDDED_PATTERN = re.compile(r"/mnt/(?:user-data|worker-data)/[^\s\"'<>|;&)]+")


def _translate_embedded_text(value: str) -> str:
    return _EMBEDDED_PATTERN.sub(lambda match: _virtual_to_actual(match.group(0)), value)


def _translate_command_arg(value: Any) -> Any:
    if isinstance(value, str):
        return _translate_embedded_text(value)
    if isinstance(value, os.PathLike):
        return _translate_pathlike(value)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, str)):
        translated = [_translate_command_arg(item) for item in value]
        if isinstance(value, tuple):
            return tuple(translated)
        if isinstance(value, list):
            return translated
    return value


if _MAPPINGS:
    _original_open = builtins.open
    _original_io_open = io.open
    _original_os_open = os.open
    _original_stat = os.stat
    _original_lstat = os.lstat
    _original_listdir = os.listdir
    _original_scandir = os.scandir
    _original_mkdir = os.mkdir
    _original_makedirs = os.makedirs
    _original_remove = os.remove
    _original_unlink = os.unlink
    _original_rmdir = os.rmdir
    _original_rename = os.rename
    _original_replace = os.replace
    _original_system = os.system
    _original_popen = os.popen
    _original_popen_init = subprocess.Popen.__init__
    _original_fspath = pathlib.PurePath.__fspath__

    def _patched_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _original_open(_translate_pathlike(file), *args, **kwargs)

    def _patched_io_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _original_io_open(_translate_pathlike(file), *args, **kwargs)

    def _patched_os_open(path, flags, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
        return _original_os_open(_translate_pathlike(path), flags, mode, dir_fd=dir_fd)

    def _patched_stat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _original_stat(_translate_pathlike(path), *args, **kwargs)

    def _patched_lstat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _original_lstat(_translate_pathlike(path), *args, **kwargs)

    def _patched_listdir(path="."):  # type: ignore[no-untyped-def]
        return _original_listdir(_translate_pathlike(path))

    def _patched_scandir(path="."):  # type: ignore[no-untyped-def]
        return _original_scandir(_translate_pathlike(path))

    def _patched_mkdir(path, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
        return _original_mkdir(_translate_pathlike(path), mode, dir_fd=dir_fd)

    def _patched_makedirs(name, mode=0o777, exist_ok=False):  # type: ignore[no-untyped-def]
        return _original_makedirs(_translate_pathlike(name), mode=mode, exist_ok=exist_ok)

    def _patched_remove(path, *, dir_fd=None):  # type: ignore[no-untyped-def]
        return _original_remove(_translate_pathlike(path), dir_fd=dir_fd)

    def _patched_unlink(path, *, dir_fd=None):  # type: ignore[no-untyped-def]
        return _original_unlink(_translate_pathlike(path), dir_fd=dir_fd)

    def _patched_rmdir(path, *, dir_fd=None):  # type: ignore[no-untyped-def]
        return _original_rmdir(_translate_pathlike(path), dir_fd=dir_fd)

    def _patched_rename(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _original_rename(_translate_pathlike(src), _translate_pathlike(dst), *args, **kwargs)

    def _patched_replace(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _original_replace(_translate_pathlike(src), _translate_pathlike(dst), *args, **kwargs)

    def _patched_system(command):  # type: ignore[no-untyped-def]
        return _original_system(_translate_embedded_text(command))

    def _patched_popen(command, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _original_popen(_translate_embedded_text(command), *args, **kwargs)

    def _patched_popen_init(self, args, *pargs, **kwargs):  # type: ignore[no-untyped-def]
        if "cwd" in kwargs:
            kwargs["cwd"] = _translate_pathlike(kwargs["cwd"])
        return _original_popen_init(self, _translate_command_arg(args), *pargs, **kwargs)

    def _patched_fspath(self):  # type: ignore[no-untyped-def]
        return _translate_pathlike(_original_fspath(self))

    builtins.open = _patched_open
    io.open = _patched_io_open
    os.open = _patched_os_open
    os.stat = _patched_stat
    os.lstat = _patched_lstat
    os.listdir = _patched_listdir
    os.scandir = _patched_scandir
    os.mkdir = _patched_mkdir
    os.makedirs = _patched_makedirs
    os.remove = _patched_remove
    os.unlink = _patched_unlink
    os.rmdir = _patched_rmdir
    os.rename = _patched_rename
    os.replace = _patched_replace
    os.system = _patched_system
    os.popen = _patched_popen
    subprocess.Popen.__init__ = _patched_popen_init
    pathlib.PurePath.__fspath__ = _patched_fspath
