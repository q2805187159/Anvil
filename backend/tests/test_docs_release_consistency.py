from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase9_docs_and_examples_exist() -> None:
    required_paths = [
        REPO_ROOT / "README_zh.md",
        REPO_ROOT / "docs" / "adr" / "index.md",
        REPO_ROOT / "docs" / "guides" / "index.md",
        REPO_ROOT / "docs" / "guides" / "quickstart-and-startup-modes.md",
        REPO_ROOT / "docs" / "guides" / "deployment.md",
        REPO_ROOT / "docs" / "guides" / "model-provider-configuration.md",
        REPO_ROOT / "docs" / "guides" / "doctor-smoke-and-tracing.md",
        REPO_ROOT / "docs" / "guides" / "extensions-and-capability-surfaces.md",
        REPO_ROOT / "docs" / "guides" / "local-docker-workspace.md",
        REPO_ROOT / "docs" / "guides" / "release-verification.md",
        REPO_ROOT / "examples" / "README.md",
        REPO_ROOT / "examples" / "config" / "openai-compatible.config.yaml",
        REPO_ROOT / "examples" / "config" / "minimax-anthropic.config.yaml",
        REPO_ROOT / "examples" / "config" / "vllm-local.config.yaml",
        REPO_ROOT / "examples" / "tracing" / "langsmith.env.example",
        REPO_ROOT / "examples" / "skills" / "minimal-operator-skill" / "SKILL.md",
        REPO_ROOT / "skills" / "README.md",
        REPO_ROOT / "docs" / "assets" / "screenshots" / "home-page.png",
        REPO_ROOT / "docs" / "assets" / "screenshots" / "ops-console.png",
        REPO_ROOT / "docs" / "assets" / "screenshots" / "session-details.png",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    assert missing == [], f"missing Phase 9 docs or examples: {missing}"

    yaml_examples = [
        REPO_ROOT / "examples" / "config" / "openai-compatible.config.yaml",
        REPO_ROOT / "examples" / "config" / "minimax-anthropic.config.yaml",
        REPO_ROOT / "examples" / "config" / "vllm-local.config.yaml",
    ]
    for path in yaml_examples:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict), f"expected mapping root in {path}"


def test_readme_and_docs_index_link_phase9_surfaces() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (REPO_ROOT / "docs" / "index.md").read_text(encoding="utf-8")

    for needle in (
        "deployment.md",
        "guides/",
        "examples/README.md",
        "local-docker-workspace.md",
    ):
        assert needle in readme
        assert needle in docs_index


def test_release_checklists_include_mount_safety_gate() -> None:
    release_verification = (REPO_ROOT / "docs" / "guides" / "release-verification.md").read_text(encoding="utf-8")
    pr_template = (REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    ci_workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "make check-docker-mounts" in release_verification
    assert "python scripts/check-docker-mount-safety.py" in release_verification
    assert "`make check-docker-mounts`" in pr_template
    assert "Check Docker mount safety" in ci_workflow


def test_release_verification_documents_readiness_runner() -> None:
    release_verification = (REPO_ROOT / "docs" / "guides" / "release-verification.md").read_text(encoding="utf-8")
    testing_future = (REPO_ROOT / "docs" / "future" / "10-testing-evaluation-and-regression.md").read_text(
        encoding="utf-8"
    )
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "python scripts/run-release-readiness.py --profile quick" in release_verification
    assert "python scripts/run-release-readiness.py --profile full --dry-run --json" in release_verification
    assert "node scripts/frontend-process-preflight.cjs" in release_verification
    assert "make release-readiness" in release_verification
    assert "scripts/run-release-readiness.py" in testing_future
    assert "frontend-process-preflight" in testing_future
    assert "release-readiness:" in makefile
    assert "release-readiness-full:" in makefile


def test_stale_manual_smoke_helper_is_removed() -> None:
    assert not (REPO_ROOT / "backend" / "tests" / "manual_smoke_phase4_runtime.py").exists()
    assert list((REPO_ROOT / "docs").glob("*phased-build*.md")) == []
