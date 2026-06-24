# Open Source Release Checklist

This guide records what is prepared in the repository and what still must be
configured manually in GitHub settings.

## Included In The Repository

| Area | Status |
| --- | --- |
| README | `README.md` and `README_zh.md` are release-facing project entrances. |
| License | MIT license in `LICENSE`. |
| Changelog | `CHANGELOG.md` starts at `0.1.0`. |
| Contributing | `CONTRIBUTING.md` covers setup, verification, and boundaries. |
| Code of Conduct | `CODE_OF_CONDUCT.md` is included. |
| Security | `SECURITY.md` documents private vulnerability reporting and deployment posture. |
| Issue templates | Bug, feature, and question templates are included. |
| PR template | `.github/PULL_REQUEST_TEMPLATE.md` is included. |
| CODEOWNERS | Root and `.github/CODEOWNERS` point to the maintainer. |
| Funding | `.github/FUNDING.yml` is present with empty placeholders. |
| Dependabot | `.github/dependabot.yml` covers pip, npm, and GitHub Actions. |
| CI | `.github/workflows/ci.yml` runs backend, frontend, and docs checks. |
| CodeQL | `.github/workflows/codeql.yml` is included. |
| Docs workflow | `.github/workflows/docs.yml` builds docs. |
| Release workflow | `.github/workflows/release.yml` runs full release readiness on tags/manual dispatch. |

## Cleaned From Public Tracking

These paths are ignored or removed from the public Git index. Items removed
from the index may still exist in a maintainer's local checkout, but they are
not part of the public release surface.

| Surface | Public-release handling |
| --- | --- |
| Secrets and machine config | `.env`, `.env.*`, and `config.yaml` are ignored. `.env.example` stays tracked. |
| Local runtime state | `.anvil/`, `.omx/`, `.agents/`, `.playwright-mcp/`, `.claude/`, `host/`, and `%SystemDrive%/` are ignored. |
| Local assistant context files | Tool-specific local assistant notes are ignored when present in a workspace. The repository source of truth remains `AGENTS.md` and release docs. |
| Debug databases | `backend/tmp/` is ignored; tracked probe SQLite files and journals are removed from the public index. |
| Generated caches | `__pycache__/`, `.pytest_cache/`, `.pytest_tmp/`, `pytest_tmp/`, `pytest-cache-files-*/`, `_tmp_test/`, `_tmp-debug/`, `_tmp_debug/`, `.tmp/`, `.tmp-debug/`, `.tmp-hcms-import/`, and package temp dirs are ignored. |
| Frontend build output | `node_modules/`, `frontend/node_modules/`, `.next/`, `frontend/.next/`, release/probe `.next` folders, coverage, Playwright output, test results, and TypeScript build info are ignored. |
| Documentation build output | Generated site output `site/` is ignored. |
| Internal planning docs | `docs/internal/`, `docs/architecture/`, `docs/future/`, selected implementation todo/audit files, bootstrap prompts, and phased-build notes are ignored. |
| Debug media and screenshots | `docs/debug-pic/` and `docs/assets/screenshots/` are ignored; README no longer references development screenshots. |
| One-off reports/logs | `docs/FINAL-TESTING-REPORT.md`, `docs/OPTIMIZATION-SUMMARY.md`, `docs/PROJECT-COMPLETE.md`, `optimization-log.md`, `*.log`, `*.tmp`, and `*.bak` are ignored. |
| User-local skills | User-local Anvil Home skill packs and external skill caches are ignored. |

The root `skills/` directory is deliberately not filtered. It contains Anvil's
bundled starter skills and is part of the public release boundary described in
`AGENTS.md`.

## Uncertain Or Maintainer-Review Items

These are intentionally not auto-deleted and should be reviewed by maintainers
before the first public release tag:

- `skills/`: protected release content. Review source, license, and safety
  posture before accepting community changes, but do not ignore the directory.
- `docs/assets/logo.png`: kept as the public visual asset. Replace it before
  release if you want a different social preview or trademark posture.
- Provider names in config and tests: model-provider support may mention real
  provider families and model names. These are functional configuration
  examples, not project branding.
- Legacy migration helpers: memory migration code can retain source-system ids
  so existing users can import old memory exports. Do not expose those sources
  as marketing language unless the migration path is still supported.

## Manual GitHub Settings

Configure these after pushing the repository:

| Setting | Recommended value |
| --- | --- |
| Description | `Harness-first agent runtime with memory, tools, MCP extensions, and an operator workspace.` |
| Website | GitHub Pages URL or docs site URL after docs publishing is enabled. |
| Topics | `agent-runtime`, `ai-agents`, `langgraph`, `mcp`, `memory`, `tools`, `fastapi`, `nextjs`, `operator-workbench`, `automation` |
| Social preview | Upload a horizontal Anvil image. `docs/assets/logo.png` can be used if it renders well in GitHub preview. |
| Discussions | Enable for Q&A, ideas, RFCs, announcements, and showcase posts. |
| Projects | Optional roadmap board for milestones and release work. |
| Releases | Create `v0.1.0` after CI passes. |
| GitHub Pages | Publish `docs/` through a Pages workflow or a selected branch when ready. |
| Sponsors | Add real funding links to `.github/FUNDING.yml` if you want a Sponsor button. |

## Branch Protection / Rulesets

Recommended `main` rules:

- Require pull requests before merging.
- Require at least one approval.
- Require review from Code Owners.
- Require status checks to pass:
  - `Backend tests and coverage`
  - `Frontend tests and typecheck`
  - `Documentation build`
  - `CodeQL / Analyze`
- Require conversation resolution.
- Block force pushes.
- Block branch deletion.
- Require linear history if you want squash-only history.

## Pull Request Settings

Recommended repository settings:

- Enable squash merge and make it the default.
- Disable merge commits if you want a clean history.
- Enable automatically delete head branches.
- Enable auto-merge after CI is stable.

## Security And Analysis Settings

Enable these in GitHub settings when available:

- Dependency graph
- Dependabot alerts
- Dependabot security updates
- Code scanning alerts
- Secret scanning
- Push protection
- Private vulnerability reporting

## Labels

Recommended labels:

- `type: bug`
- `type: feature`
- `type: docs`
- `type: question`
- `type: refactor`
- `type: security`
- `priority: high`
- `priority: medium`
- `priority: low`
- `status: needs reproduction`
- `status: needs design`
- `status: good first issue`
- `status: help wanted`
- `status: wontfix`

## Items That Need Maintainer Input

- Final project description and tagline.
- Website/docs URL.
- Social preview image choice.
- Funding links, if any.
- Security contact channel if private advisories are not enough.
- Governance policy for accepting external skills/plugins.
- First public release notes for `v0.1.0`.
