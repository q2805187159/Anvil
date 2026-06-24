from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
HARNESS_ROOT = BACKEND_ROOT / "packages" / "harness"
BACKEND_TEST_TMP = BACKEND_ROOT / ".pytest_tmp"
BACKEND_TEST_TMP_ENV = "ANVIL_BACKEND_TEST_TMP"
BACKEND_TEST_SHIM_NAME = "pytest-shim"
BACKEND_TEST_SHIM = BACKEND_TEST_TMP / BACKEND_TEST_SHIM_NAME


class BackendTestArgs:
    def __init__(self, *, pytest_args: list[str], shard_index: int | None, shard_count: int | None) -> None:
        self.pytest_args = pytest_args
        self.shard_index = shard_index
        self.shard_count = shard_count


def main(argv: list[str]) -> int:
    try:
        parsed_args = _parse_backend_test_args(argv)
    except ValueError as exc:
        print(f"run-backend-tests argument error: {exc}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    pythonpath = [
        str(BACKEND_ROOT),
        str(HARNESS_ROOT),
        env.get("PYTHONPATH", ""),
    ]
    env["PYTHONPATH"] = os.pathsep.join(item for item in pythonpath if item)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    backend_test_tmp = _select_backend_test_tmp()
    env[BACKEND_TEST_TMP_ENV] = str(backend_test_tmp)
    env["TMP"] = str(backend_test_tmp)
    env["TEMP"] = str(backend_test_tmp)
    env["TMPDIR"] = str(backend_test_tmp)
    pytest_args = ["-p", "no:cacheprovider", *parsed_args.pytest_args]
    if parsed_args.shard_index is not None:
        selected_files = _select_backend_test_shard(
            index=parsed_args.shard_index,
            count=parsed_args.shard_count,
            files=_discover_backend_test_files(),
        )
        pytest_args = ["-p", "no:cacheprovider", *selected_files, *parsed_args.pytest_args]

    if os.name == "nt":
        shim = backend_test_tmp / BACKEND_TEST_SHIM_NAME
        shim.mkdir(parents=True, exist_ok=True)
        (shim / "readline.py").write_text("", encoding="utf-8")
        wrapper_flag = "ANVIL_PYTEST_PLATFORM_SHIM"
        tempfile_patch_flag = "ANVIL_BACKEND_TEST_TEMPFILE_PATCH"
        sitecustomize = (
            "from __future__ import annotations\n"
            "import os\n"
            "import sys\n"
            "import types\n"
            f"if os.environ.get('{tempfile_patch_flag}') == '1':\n"
            "    import shutil\n"
            "    import tempfile\n"
            "    from pathlib import Path\n"
            "    from uuid import uuid4\n"
            f"    _anvil_tmp_root = Path(os.environ.get('{BACKEND_TEST_TMP_ENV}', tempfile.gettempdir()))\n"
            "    _anvil_tmp_root.mkdir(parents=True, exist_ok=True)\n"
            "    tempfile.tempdir = str(_anvil_tmp_root)\n"
            "    def _anvil_mkdtemp(suffix=None, prefix=None, dir=None):\n"
            "        base = Path(dir or _anvil_tmp_root)\n"
            "        base.mkdir(parents=True, exist_ok=True)\n"
            "        suffix = '' if suffix is None else str(suffix)\n"
            "        prefix = 'tmp' if prefix is None else str(prefix)\n"
            "        for _ in range(100):\n"
            "            path = base / f'{prefix}{uuid4().hex}{suffix}'\n"
            "            try:\n"
            "                path.mkdir(parents=True, exist_ok=False)\n"
            "            except FileExistsError:\n"
            "                continue\n"
            "            return str(path)\n"
            "        raise FileExistsError('could not allocate an Anvil backend temp directory')\n"
            "    class _AnvilTemporaryDirectory:\n"
            "        def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=False, *, delete=True):\n"
            "            self.name = _anvil_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)\n"
            "            self._delete = delete\n"
            "        def __enter__(self):\n"
            "            return self.name\n"
            "        def __exit__(self, exc, value, tb):\n"
            "            self.cleanup()\n"
            "        def cleanup(self):\n"
            "            if self._delete:\n"
            "                shutil.rmtree(self.name, ignore_errors=True)\n"
            "    tempfile.mkdtemp = _anvil_mkdtemp\n"
            "    tempfile.TemporaryDirectory = _AnvilTemporaryDirectory\n"
            f"if os.name == 'nt' and os.environ.get('{wrapper_flag}') == '1':\n"
            "    # Avoid a local Windows asyncio/_overlapped initialization failure in tests.\n"
            "    sys.platform = 'linux'\n"
            "    sys.modules.setdefault('readline', types.ModuleType('readline'))\n"
        )
        (shim / "sitecustomize.py").write_text(
            sitecustomize + f"    os.environ.pop('{wrapper_flag}', None)\n",
            encoding="utf-8",
        )
        env["PYTHONPATH"] = os.pathsep.join([str(shim), env["PYTHONPATH"]])
        env[wrapper_flag] = "1"
        env[tempfile_patch_flag] = "1"
        # Child subprocesses such as `python -m pip install ...` must run with
        # the real Windows platform. sitecustomize only changes sys.platform in
        # the pytest parent process, then removes the flag for descendants.

    command = [sys.executable, "-m", "pytest", *pytest_args]
    return subprocess.call(command, cwd=BACKEND_ROOT, env=env)


def _parse_backend_test_args(argv: list[str]) -> BackendTestArgs:
    pytest_args: list[str] = []
    shard_index: int | None = None
    shard_count: int | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--backend-shard-index":
            if index + 1 >= len(argv):
                raise ValueError("--backend-shard-index requires a value")
            shard_index = _parse_positive_int("--backend-shard-index", argv[index + 1])
            index += 2
            continue
        if arg == "--backend-shard-count":
            if index + 1 >= len(argv):
                raise ValueError("--backend-shard-count requires a value")
            shard_count = _parse_positive_int("--backend-shard-count", argv[index + 1])
            index += 2
            continue
        pytest_args.append(arg)
        index += 1

    if shard_index is None and shard_count is None:
        return BackendTestArgs(pytest_args=pytest_args, shard_index=None, shard_count=None)
    if shard_index is None or shard_count is None:
        raise ValueError("--backend-shard-index and --backend-shard-count must be provided together")
    if shard_index > shard_count:
        raise ValueError("--backend-shard-index must be between 1 and --backend-shard-count")
    return BackendTestArgs(pytest_args=pytest_args, shard_index=shard_index, shard_count=shard_count)


def _parse_positive_int(name: str, raw_value: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _discover_backend_test_files() -> list[Path]:
    return sorted((BACKEND_ROOT / "tests").glob("test_*.py"), key=_backend_relative_posix)


def _select_backend_test_shard(*, index: int, count: int, files: list[Path]) -> list[str]:
    sorted_files = sorted(files, key=_backend_relative_posix)
    selected = [path for offset, path in enumerate(sorted_files) if offset % count == index - 1]
    return [_backend_relative_posix(path) for path in selected]


def _backend_relative_posix(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def _select_backend_test_tmp() -> Path:
    candidates = []
    env_value = os.environ.get(BACKEND_TEST_TMP_ENV)
    if env_value:
        candidates.append(Path(env_value))
    candidates.extend(
        [
            BACKEND_TEST_TMP,
            Path(tempfile.gettempdir()) / "anvil-backend-tests",
        ]
    )
    seen: set[str] = set()
    failures: list[str] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        marker = str(resolved).casefold()
        if marker in seen:
            continue
        seen.add(marker)
        try:
            _assert_test_tmp_usable(resolved)
        except (OSError, sqlite3.Error) as exc:
            failures.append(f"{resolved}: {exc}")
            continue
        return resolved
    raise RuntimeError("no usable backend test temp directory; " + "; ".join(failures))


def _assert_test_tmp_usable(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    file_probe = root / f".anvil-write-probe-{token}.txt"
    sqlite_probe = root / f".anvil-sqlite-probe-{token}.sqlite3"
    try:
        file_probe.write_text("ok", encoding="utf-8")
        connection = sqlite3.connect(sqlite_probe)
        try:
            connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO probe(value) VALUES (?)", ("ok",))
            connection.commit()
        finally:
            connection.close()
    finally:
        _unlink_probe(file_probe)
        _unlink_probe(sqlite_probe)
        _unlink_probe(sqlite_probe.with_suffix(sqlite_probe.suffix + "-journal"))
        _unlink_probe(sqlite_probe.with_suffix(sqlite_probe.suffix + "-wal"))
        _unlink_probe(sqlite_probe.with_suffix(sqlite_probe.suffix + "-shm"))


def _unlink_probe(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        # Some sandboxed Windows sessions allow creation but deny deletion.
        # Treat write+SQLite probes as the usability check and leave stale
        # probe files for the explicit cleanup script instead of falling back
        # to a less predictable system temp directory.
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
