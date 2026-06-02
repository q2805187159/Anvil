# Extensions, Skills, and Capability Surfaces

This guide explains how to think about Anvil's extensibility without confusing documentation surfaces with runtime truth.

## The Simple Rule

Everything becomes real only after the harness assembles it into the runtime capability bundle.

That means:

- a skill file is not runtime truth on its own
- an MCP server config is not runtime truth on its own
- a shell command is not runtime truth on its own
- a frontend panel is not runtime truth on its own

## Skills

Skills are repo-local or operator-supplied workflow content.

Use skills for:

- structured prompt guidance
- reusable operator knowledge
- workflow decomposition hints

Do not treat skills as a second adapter layer or as app-owned logic.

## MCP and Extensions

MCP servers are the main external capability input in v1.

They are appropriate when you want:

- external tools
- external content fetch/search surfaces
- external system integration through the harness layer

MCP sources still go through the same lifecycle:

- configured
- enabled
- materialized
- visible

## Subagents

Subagents are workers for bounded delegated execution.

They:

- run with an allowed capability subset
- return structured results and artifacts
- remain under the lead thread's authority

They do not:

- own user interaction
- own final presentation
- define parent capability truth

## Shell and Frontend

The shell and frontend are downstream surfaces.

They should:

- present current runtime state
- offer operator-friendly entrypoints
- avoid re-implementing runtime policy

They must not:

- invent tool availability
- define approval semantics
- override path/sandbox rules

## Plugins

Plugins are a shipped declarative packaging model.

They may contribute:

- skill roots
- inline tools
- resources
- prompts
- catalog metadata

They still must flow through:

- config
- discovery/materialization
- registry
- capability bundle

They may not inject middleware or become a second source of runtime truth.

## Where To Look Next

- operator workflow details: [Doctor, Smoke, and Tracing](./doctor-smoke-and-tracing.md)
- release-safe examples: [Examples](https://github.com/q2805187159/Anvil/blob/main/examples/README.md)
- decision records: [ADRs](../adr/index.md)

