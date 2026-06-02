# Document Pipeline

This guide describes the Anvil document ingestion and delivery contract.

## Asset Layers

Each uploaded document now has distinct asset roles:

- Original upload
  - The source file stored under `/mnt/user-data/uploads`
- Analysis companions
  - Companion artifacts generated from the source, typically Markdown
  - Used by `read_file` / `extract_document` and surfaced as analysis assets in the UI
- Final outputs
  - Deliverables created under `/mnt/user-data/outputs`
  - Only files in `outputs/` are treated as final downloadable artifacts

Scratch files are internal working files under the thread workspace scratch directory and are not treated as final outputs.

## Runtime Tooling

Document-first tools:

- `extract_document(path, prefer_companion=true)`
  - Returns normalized readable content plus provider diagnostics
- `export_document(...)`
  - Produces stable `.docx` output in `outputs/`

Shell fallback remains available through `run_command`, but document workflows should prefer `extract_document` and `export_document`.

## Path Semantics

Frontend and stream surfaces display runtime paths, not container-local absolute paths:

- `/mnt/user-data/workspace`
- `/mnt/user-data/uploads`
- `/mnt/user-data/outputs`

`run_command` translates those runtime paths to actual host/container paths before execution and injects:

- `ANVIL_WORKSPACE`
- `ANVIL_UPLOADS`
- `ANVIL_OUTPUTS`
- `ANVIL_SCRATCH`

## Output Registration

At the end of each run, Anvil scans `outputs/` and automatically registers newly created deliverables as output artifacts.

That means files created by either:

- `export_document`
- `write_file`
- `run_command`

will appear in thread detail and the frontend output panel as long as they land in `/mnt/user-data/outputs`.
