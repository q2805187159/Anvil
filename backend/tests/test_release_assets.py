from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


def test_release_scripts_and_compose_assets_exist() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    expected = [
        repo_root / ".env.example",
        repo_root / ".github" / "workflows" / "ci.yml",
        repo_root / "Makefile",
        repo_root / "config.example.yaml",
        repo_root / "docker-compose.yml",
        repo_root / ".dockerignore",
        repo_root / "mkdocs.yml",
        repo_root / "scripts" / "init-config.ps1",
        repo_root / "scripts" / "init-config.sh",
        repo_root / "scripts" / "check-docker-mount-safety.py",
        repo_root / "scripts" / "start-backend.ps1",
        repo_root / "scripts" / "start-shell.ps1",
        repo_root / "scripts" / "start-fullstack.ps1",
        repo_root / "scripts" / "start-docker.ps1",
        repo_root / "scripts" / "stop-docker.ps1",
        repo_root / "scripts" / "status-docker.ps1",
        repo_root / "scripts" / "start-backend.sh",
        repo_root / "scripts" / "start-shell.sh",
        repo_root / "scripts" / "start-fullstack.sh",
        repo_root / "scripts" / "start-docker.sh",
        repo_root / "scripts" / "stop-docker.sh",
        repo_root / "scripts" / "status-docker.sh",
        repo_root / "backend" / "Dockerfile",
        repo_root / "backend" / ".dockerignore",
        repo_root / "frontend" / "Dockerfile",
        repo_root / "frontend" / ".dockerignore",
        repo_root / "docs" / "guides" / "deployment.md",
        repo_root / "docs" / "guides" / "local-docker-workspace.md",
    ]
    for path in expected:
        assert path.exists(), f"missing release asset: {path}"


def test_docker_compose_does_not_mount_source_tree_rw_into_agent_writable_paths() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    module = _load_mount_safety_script()

    assert module.find_mount_safety_violations(repo_root / "docker-compose.yml") == []


def test_dockerignore_excludes_local_build_and_test_caches() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    root_patterns = _dockerignore_patterns(repo_root / ".dockerignore")
    backend_patterns = _dockerignore_patterns(repo_root / "backend" / ".dockerignore")
    frontend_patterns = _dockerignore_patterns(repo_root / "frontend" / ".dockerignore")

    root_expected = {
        "frontend/node_modules",
        "frontend/.next",
        "backend/.venv",
        "backend/.venv/**",
        "backend/build",
        "**/__pycache__/",
        "**/*.py[cod]",
        "**/*.pyc.*",
        "**/.venv/",
        "**/.pytest_cache/",
        "**/.pytest_tmp/",
        "**/.anvil/",
    }
    backend_expected = {
        ".venv",
        ".venv/**",
        ".pytest_cache",
        ".pytest_tmp",
        "__pycache__/",
        "**/__pycache__/",
        "*.py[cod]",
        "*.pyc.*",
        "**/*.py[cod]",
        "**/*.pyc.*",
        "build",
        "dist",
        "test_run.log",
    }
    frontend_expected = {
        "node_modules",
        "node_modules/**",
        ".next",
        ".next/**",
        "coverage",
        "tsconfig.tsbuildinfo",
    }

    assert root_expected.issubset(root_patterns)
    assert backend_expected.issubset(backend_patterns)
    assert frontend_expected.issubset(frontend_patterns)


def test_compose_uses_service_scoped_build_contexts() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))

    backend_build = compose["services"]["backend"]["build"]
    frontend_build = compose["services"]["frontend"]["build"]

    assert backend_build["context"] == "./backend"
    assert backend_build["dockerfile"] == "Dockerfile"
    assert frontend_build["context"] == "./frontend"
    assert frontend_build["dockerfile"] == "Dockerfile"


def test_backend_compose_exposes_docker_smoke_inputs_and_artifacts() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))

    volumes = compose["services"]["backend"]["volumes"]

    assert "./config.yaml:/app/config.yaml:ro" in volumes
    assert "./.omx/reports:/app/.omx/reports:rw" in volumes


def test_backend_dockerfile_keeps_dependency_install_layer_source_independent() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "backend" / "Dockerfile").read_text(encoding="utf-8")

    pyproject_copy = dockerfile.index("COPY pyproject.toml /app/backend/pyproject.toml")
    requirements_generation = dockerfile.index("/tmp/anvil-runtime-requirements.txt")
    dependency_install = dockerfile.index("python -m pip install")
    source_copy = dockerfile.index("COPY . /app/backend")

    assert pyproject_copy < requirements_generation < dependency_install < source_copy
    assert "COPY backend" not in dockerfile
    assert "tomllib" in dockerfile
    assert ".[observability]" not in dockerfile


def test_frontend_dockerfile_uses_scoped_context() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    package_copy = dockerfile.index("COPY package*.json /app/frontend/")
    dependency_install = dockerfile.index("npm ci")
    source_copy = dockerfile.index("COPY . /app/frontend")
    build = dockerfile.index("npm run build")

    assert package_copy < dependency_install < source_copy < build
    assert "COPY frontend" not in dockerfile


def test_docker_mount_safety_script_flags_rw_source_tree_bind(contract_tmp_path) -> None:
    compose_path = contract_tmp_path / "unsafe-compose.yml"
    compose_path.write_text(
        "services:\n"
        "  backend:\n"
        "    volumes:\n"
        "      - .:/mnt/host-workspaces/harness:rw\n"
        "      - ./backend:/app/backend:ro\n"
        "      - ./.anvil/workspace:/mnt/host-workspaces/state:rw\n",
        encoding="utf-8",
    )
    module = _load_mount_safety_script()

    violations = module.find_mount_safety_violations(compose_path)

    assert violations == ["backend: .:/mnt/host-workspaces/harness:rw"]


def test_docker_mount_safety_discovers_existing_compose_overrides(contract_tmp_path) -> None:
    module = _load_mount_safety_script()
    repo_root = contract_tmp_path / "repo"
    repo_root.mkdir()
    base = repo_root / "docker-compose.yml"
    override = repo_root / "docker-compose.override.yml"
    base.write_text("services: {}\n", encoding="utf-8")
    override.write_text("services: {}\n", encoding="utf-8")

    discovered = module.discover_default_compose_files(repo_root)

    assert discovered == [base, override]


def test_docker_mount_safety_main_checks_discovered_overrides(contract_tmp_path, monkeypatch, capsys) -> None:
    module = _load_mount_safety_script()
    repo_root = contract_tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    volumes:\n"
        "      - ./.anvil/workspace:/mnt/host-workspaces/state:rw\n",
        encoding="utf-8",
    )
    (repo_root / "docker-compose.override.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    volumes:\n"
        "      - .:/mnt/host-workspaces/harness:rw\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REPO_ROOT", repo_root)

    assert module.main([]) == 1
    output = capsys.readouterr().out
    assert "Unsafe Docker mount(s) detected:" in output
    assert "backend: .:/mnt/host-workspaces/harness:rw" in output


def test_start_docker_powershell_generates_valid_host_path_override(contract_tmp_path) -> None:
    if shutil.which("powershell") is None:
        pytest.skip("PowerShell is not available")
    repo_root = Path(__file__).resolve().parents[2]
    anvil_home = contract_tmp_path / "home"
    workspace = anvil_home / "workspace"
    command = (
        "$content = Get-Content -Raw -LiteralPath 'scripts\\start-docker.ps1'; "
        "$start = $content.IndexOf('function New-AnvilBindMount'); "
        "$end = $content.IndexOf('function Get-PublishedEndpointOnce'); "
        "if ($start -lt 0 -or $end -lt 0 -or $end -le $start) { throw 'function bounds not found' }; "
        "Invoke-Expression $content.Substring($start, $end - $start); "
        "New-AnvilHostPathOverride -RepoRootPath (Resolve-Path '.').Path"
    )
    env = {
        **os.environ,
        "ANVIL_HOME_HOST": str(anvil_home),
        "ANVIL_WORKSPACE_HOST": str(workspace),
    }

    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    override_line = next(
        (line.strip() for line in completed.stdout.splitlines() if "docker-compose.host-paths." in line),
        "",
    )
    assert override_line, completed.stdout
    override = Path(override_line)
    assert override.parent.name == "anvil-docker"
    text = override.read_text(encoding="utf-8-sig")
    assert '"source":' in text
    assert '"source":\n' not in text
    parsed = yaml.safe_load(text)
    volumes = parsed["services"]["backend"]["volumes"]
    assert volumes[0]["source"] == str(anvil_home)
    assert volumes[1]["source"] == str(workspace)
    assert parsed["services"]["backend"]["environment"]["ANVIL_HOME"] == "/app/.anvil"


def _load_mount_safety_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check-docker-mount-safety.py"
    spec = importlib.util.spec_from_file_location("check_docker_mount_safety", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dockerignore_patterns(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
