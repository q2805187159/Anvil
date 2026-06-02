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

These paths are ignored or removed from the public Git index:

- `.env`, `.env.*`, and `config.yaml`
- `.anvil/`, `.omx/`, `.agents/`, `.playwright-mcp/`, and other local agent-state directories
- `_tmp_debug/`, `_tmp-debug/`, `.tmp-debug/`, `.pytest_tmp/`, `pytest_tmp/`
- SQLite runtime databases and journals under debug folders
- generated logs such as `*.log`
- generated docs site output `site/`
- internal future/planning docs under `docs/future/`
- one-off completion and optimization logs under `docs/FINAL-TESTING-REPORT.md`,
  `docs/OPTIMIZATION-SUMMARY.md`, `docs/PROJECT-COMPLETE.md`, and
  `optimization-log.md`
- unreviewed local skill packs under `skills/`

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
