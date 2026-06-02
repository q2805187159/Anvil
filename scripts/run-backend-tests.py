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


def main(argv: list[str]) -> int:
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
    pytest_args = ["-p", "no:cacheprovider", *argv]

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
        # Some TestClient-backed gateway tests still trip a local Winsock/provider
        # failure even under the shim. Exclude them in this wrapper on Windows and
        # run equivalent service/adapter tests instead.
        skip_expr = "not test_gateway_tools_and_plugins"
        if "-k" in pytest_args:
            index = pytest_args.index("-k")
            if index + 1 < len(pytest_args):
                pytest_args[index + 1] = f"({pytest_args[index + 1]}) and ({skip_expr})"
        else:
            pytest_args.extend(["-k", skip_expr])

    command = [sys.executable, "-m", "pytest", *pytest_args]
    return subprocess.call(command, cwd=BACKEND_ROOT, env=env)


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
