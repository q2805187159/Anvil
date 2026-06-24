import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/src/components/ui/tooltip";
import { I18nProvider } from "@/src/core/i18n";
import { ThemeProvider } from "@/src/core/theme/provider";

const createThreadMock = vi.fn().mockResolvedValue({ thread_id: "thread-b" });
const deleteThreadMock = vi.fn().mockResolvedValue({ thread_id: "thread-a", deleted: true });
const cancelApprovalMock = vi.fn().mockResolvedValue({});
const waitSubagentTaskMock = vi.fn().mockResolvedValue({});
const cancelSubagentTaskMock = vi.fn().mockResolvedValue({});
const waitProcessSessionMock = vi.fn().mockResolvedValue({});
const killProcessSessionMock = vi.fn().mockResolvedValue({});
const writeProcessStdinMock = vi.fn().mockResolvedValue({});
const closeProcessStdinMock = vi.fn().mockResolvedValue({});
const interruptProcessSessionMock = vi.fn().mockResolvedValue({});
const resizeProcessSessionMock = vi.fn().mockResolvedValue({});
const startRunMock = vi.fn().mockResolvedValue(undefined);
const resumeApprovalMock = vi.fn().mockResolvedValue(undefined);
const editLatestAndResendMock = vi.fn().mockResolvedValue(undefined);
const enqueueFollowupMock = vi.fn().mockResolvedValue({ queue_id: "queue-1", mode: "followup" });
const updateFollowupMock = vi.fn().mockResolvedValue({ queue_id: "queue-1", mode: "followup" });
const deleteFollowupMock = vi.fn().mockResolvedValue({ deleted: true });
const popNextFollowupMock = vi.fn().mockResolvedValue(null);
const runReflectionJobMock = vi.fn().mockResolvedValue({});
const pauseReflectionJobMock = vi.fn().mockResolvedValue({});
const resumeReflectionJobMock = vi.fn().mockResolvedValue({});
const removeReflectionJobMock = vi.fn().mockResolvedValue({});
const createMemoryEntryMock = vi.fn().mockResolvedValue({});
const updateMemoryEntryMock = vi.fn().mockResolvedValue({});
const deleteMemoryEntryMock = vi.fn().mockResolvedValue({});
const createMemoryLayerEntryMock = vi.fn().mockResolvedValue({});
const updateMemoryLayerEntryMock = vi.fn().mockResolvedValue({});
const deleteMemoryLayerEntryMock = vi.fn().mockResolvedValue({});
const updateThreadSettingsMock = vi.fn().mockResolvedValue({});
const manageSkillMock = vi.fn().mockResolvedValue({ skill_id: "minimal-operator-skill", action: "disable", ok: true });
const reloadSkillsMock = vi.fn().mockResolvedValue({ reloaded: true });
const promoteSkillProcedureMock = vi.fn().mockResolvedValue({ accepted: true, status: "promoted" });
const rejectSkillProcedureMock = vi.fn().mockResolvedValue({ accepted: true, status: "rejected" });
const restoreSkillProcedureMock = vi.fn().mockResolvedValue({ accepted: true, status: "restored" });
const runSkillCuratorMaintenanceMock = vi.fn().mockResolvedValue({ status: "completed" });
const runSkillCuratorAutomationMock = vi.fn().mockResolvedValue({ queued: true });
const installPluginMock = vi.fn().mockResolvedValue({ plugin_id: "memory-http-integration-notes", installed: true, enabled: true });
const upsertPluginRegistryMock = vi.fn().mockResolvedValue({ status: "updated" });
const refreshPluginRegistryMock = vi.fn().mockResolvedValue({ status: "refreshed" });
const deletePluginRegistryMock = vi.fn().mockResolvedValue({ status: "deleted" });
const refreshMcpServerMock = vi.fn().mockResolvedValue({ server_id: "github", status: "ready" });
const reconnectMcpServerMock = vi.fn().mockResolvedValue({ status: "reconnected" });
const renderMcpPromptMock = vi.fn().mockResolvedValue({
  server_id: "github",
  prompt_id: "repo-summary",
  title: "Repo summary",
  description: "Render repo summary prompt",
  arguments: ["repo"],
  metadata: { surface: "summary" },
  rendered: "Summary for repo",
});
const uploadFilesMock = vi.fn().mockResolvedValue({
  thread_id: "thread-a",
  files: [{ filename: "brief.pdf", kind: "uploads", virtual_path: "/mnt/user-data/uploads/brief.pdf", artifact_url: "/threads/thread-a/artifacts/uploads/brief.pdf" }],
});
const searchArchiveMock = vi.fn().mockResolvedValue({
  query: "Northstar",
  hits: [{ archive_id: "a1", thread_id: "thread-a", excerpt: "Northstar context", score: 1 }],
  engine_notes: [],
});
const sessionSearchResultFixture = {
  query: "Northstar",
  thread_id: "thread-a",
  scope: "exclude_current",
  engine_notes: ["HCMS found a prior thread."],
  current_thread_snapshot: { snapshot_id: "snap-current", prompt_hash: "hash-current", skills_fingerprint: null, memory_fingerprint: "mem-current", config_fingerprint: "cfg-current", created_at: "" },
  groups: [
    {
      thread_id: "thread-b",
      hit_count: 1,
      summary: "Focused summary: Northstar was discussed in Forge with a rollout decision.",
      excerpts: ["Northstar was discussed in Forge."],
      latest_created_at: "",
      latest_prompt_snapshot: { snapshot_id: "snap-b", prompt_hash: "hash-b", skills_fingerprint: null, memory_fingerprint: "mem-b", config_fingerprint: "cfg-b", created_at: "" },
      hits: [{ archive_id: "a1", thread_id: "thread-b", excerpt: "Northstar was discussed in Forge.", score: 1, created_at: "" }],
      evidence: [{ evidence_id: "ev-session-1", source_kind: "session_archive", source_id: "archive", archive_id: "a1", thread_id: "thread-b", score: 1, reason: "session_search summarize evidence", excerpt: "Northstar was discussed in Forge." }],
    },
  ],
};
const searchSessionsMock = vi.fn().mockResolvedValue(sessionSearchResultFixture);
const toolCatalogFixture = [
  {
    capability_id: "cap.write_file",
    name: "write_file",
    display_name: "Write File",
    summary: "Writes a file to the workspace.",
    source_kind: "builtin",
    source_id: "core",
    capability_group: "filesystem",
    visibility: "visible",
    deferred: false,
    stability: "stable",
    risk_category: "filesystem_write",
    approval: { kind: "filesystem_write" },
    resources: [],
    prompts: [],
    dependencies: [],
    provenance: { layer: "runtime" },
    health: { status: "ok" },
  },
  {
    capability_id: "cap.patch_file",
    name: "patch_file",
    display_name: "Patch File",
    summary: "Applies an incremental patch inside a file.",
    source_kind: "builtin",
    source_id: "core",
    capability_group: "filesystem",
    visibility: "visible",
    deferred: false,
    stability: "stable",
    risk_category: "filesystem_write",
    approval: { kind: "filesystem_write" },
    resources: [],
    prompts: [],
    dependencies: [],
    provenance: { layer: "runtime" },
    health: { status: "ok" },
  },
  {
    capability_id: "cap.read_file",
    name: "read_file",
    display_name: "Read File",
    summary: "Reads a file from the workspace.",
    source_kind: "builtin",
    source_id: "core",
    capability_group: "filesystem",
    visibility: "visible",
    deferred: false,
    stability: "stable",
    risk_category: null,
    approval: null,
    resources: [],
    prompts: [],
    dependencies: [],
    provenance: { layer: "runtime" },
    health: { status: "ok" },
  },
];
const skillFixture = {
  skill_id: "minimal-operator-skill",
  title: "Minimal Operator Skill",
  summary: "demo",
  allowed_tools: ["read_file"],
  tags: ["ops"],
  enabled: true,
  path: "/skills/minimal",
  trust: "trusted",
  version: "1.0.0",
  dependencies: [{ kind: "tool", name: "read_file" }],
  readiness: { ready: true },
  config: { mode: "demo" },
  platforms: ["windows"],
  asset_paths: ["assets/guide.md"],
  template_paths: ["templates/report.md"],
  script_paths: ["scripts/bootstrap.ps1"],
  reference_paths: ["references/ops.md"],
  package: { checksum: "abc123" },
};
const skillContentFixture = {
  skill_id: "minimal-operator-skill",
  title: "Minimal Operator Skill",
  path: "/skills/minimal",
  source_root: "/skills",
  body: "# Minimal Operator Skill\n\ndemo",
  body_preview: "demo",
  file_count: 2,
};
const skillFilesFixture = {
  skill_id: "minimal-operator-skill",
  path: "/skills/minimal",
  source_root: "/skills",
  files: [
    { path: "SKILL.md", kind: "manifest", size_bytes: 32, is_binary: false },
    { path: "references/ops.md", kind: "reference", size_bytes: 24, is_binary: false },
  ],
};
const skillFileFixture = {
  skill_id: "minimal-operator-skill",
  relative_path: "references/ops.md",
  path: "/skills/minimal/references/ops.md",
  source_root: "/skills",
  kind: "reference",
  is_binary: false,
  encoding: "utf-8",
  content: "demo reference",
  truncated: false,
  size_bytes: 24,
};
const skillProceduresFixture = {
  accepted: true,
  mode: "curator_procedures",
  counts: { total: 1, returned: 1, promotable: 1, promoted: 0 },
  items: [
    {
      procedure_id: "proc-edit-verify",
      title: "Edit and verify code changes",
      trigger: "When a task changes code and needs local evidence before handoff.",
      expected_outcome: "A scoped patch with the relevant regression command completed.",
      status: "candidate",
      strength: 0.82,
      confidence: 0.91,
      frequency: 3,
      steps: ["Inspect the local module boundary.", "Apply the focused edit.", "Run the narrow regression gate."],
      evidence_refs: ["run-edit-1", "run-edit-2"],
      allowed_tools: ["read_file", "apply_patch", "shell_command"],
    },
  ],
  truncated: false,
};
const pluginsFixture = [
  {
    plugin_id: "core-governance",
    enabled: true,
    source_path: "/plugins/core-governance",
    skill_roots: ["/plugins/core-governance/skills"],
    tool_count: 2,
    tool_names: ["governance.audit", "governance.reload"],
    resources: [{ resource_id: "plugin-doc", title: "Plugin Doc" }],
    prompts: [{ prompt_id: "plugin-prompt", title: "Plugin Prompt" }],
    catalog_metadata: { publisher: "forge-labs" },
  },
];
const pluginCatalogFixture = [
  {
    plugin_id: "memory-http-integration-notes",
    name: "Memory HTTP Integration Notes",
    description: "Documentation-only memory integration notes that keep HCMS as the active engine.",
    source: "/plugins/memory-http-integration-notes",
    source_kind: "local",
    version: "0.1.0",
    author: "Anvil",
    homepage: null,
    tags: ["memory", "hcms", "integration"],
    trust_level: "curated",
    registry_id: "project-plugins",
    registry_name: "Anvil curated plugins",
    registry_source: "/plugins/catalog.json",
    registry_kind: "local_catalog",
    installed: false,
    enabled: false,
    installable: true,
    skill_count: 0,
    tool_count: 0,
    mcp_server_count: 0,
    resource_count: 0,
    prompt_count: 0,
    skill_roots: [],
    tool_names: [],
    mcp_servers: [],
    permissions: ["documentation only; does not activate an external memory engine"],
    catalog_metadata: { publisher: "Anvil" },
    discovery_source: "catalog",
  },
];
const pluginRegistriesFixture = [
  {
    registry_id: "project-plugins",
    name: "Anvil curated plugins",
    source: "/plugins/catalog.json",
    source_kind: "local_catalog",
    enabled: true,
    readonly: true,
    trust_level: "curated",
    entry_count: 1,
    cached: false,
    cache_path: null,
    error: null,
    diagnostics: [],
    config_path: null,
    last_checked_at: "",
  },
];
const mcpServersFixture = [
  {
    server_id: "github",
    status: "ready",
    transport_kind: "stdio",
    startup_policy: "lazy",
    refresh_policy: "dynamic",
    enabled: true,
    tool_count: 2,
    tool_names: ["repo_search", "issue_lookup"],
    error: null,
  },
];
const mcpResourcesFixture = [
  {
    resource_id: "readme",
    title: "README",
    description: "Repo readme",
    server_id: "github",
    path: "/README.md",
    metadata: { kind: "markdown" },
  },
];
const mcpPromptsFixture = [
  {
    prompt_id: "repo-summary",
    title: "Repo summary",
    description: "Render repo summary prompt",
    server_id: "github",
    arguments: ["repo"],
    metadata: { surface: "summary" },
  },
];
let runStreamFixture: {
  events: Array<Record<string, unknown>>;
  error: string | null;
  isStreaming: boolean;
  start: typeof startRunMock;
  resumeApproval: typeof resumeApprovalMock;
  editLatestAndResend: typeof editLatestAndResendMock;
  stop: ReturnType<typeof vi.fn>;
  threadStatePatch?: Record<string, unknown>;
} = {
  events: [
    { event: "run_started", data: { thread_id: "thread-a" } },
    { event: "run_completed", data: { assistant_message: "done" } },
  ],
  error: null,
  isStreaming: false,
  start: startRunMock,
  resumeApproval: resumeApprovalMock,
  editLatestAndResend: editLatestAndResendMock,
  stop: vi.fn(),
};
function createDefaultThreadsFixture() {
  return [
    {
      thread_id: "thread-a",
      title: "Northstar",
      status: "awaiting_approval",
      updated_at: "",
      last_user_message_preview: "Need approval",
      has_pending_approval: true,
      has_active_subagent_tasks: true,
    },
    {
      thread_id: "thread-b",
      title: "Forge",
      status: "completed",
      updated_at: "",
      last_user_message_preview: "done",
      has_pending_approval: false,
      has_active_subagent_tasks: false,
    },
  ];
}

let threadsFixture = createDefaultThreadsFixture();

function queuedFollowupFixture(patch: Record<string, unknown> = {}) {
  return {
    queue_id: "queue-1",
    thread_id: "thread-b",
    message: "Queued user turn",
    mode: "followup",
    status: "queued",
    execution_mode: "agent",
    selected_model: "minimax",
    profile: null,
    selected_reasoning_effort: "high",
    is_plan_mode: null,
    uploaded_filenames: [],
    uploaded_file_refs: [],
    upload_context: null,
    promoted_capabilities: [],
    dispatch_id: null,
    created_at: "2026-05-27T09:00:00Z",
    updated_at: "2026-05-27T09:00:00Z",
    ...patch,
  };
}

vi.mock("@/src/core/threads/hooks", () => ({
  THREAD_DETAIL_MESSAGE_WINDOW_PAGE_SIZE: 120,
  useThreads: () => ({
    data: threadsFixture,
  }),
  useCreateThread: () => ({
    mutateAsync: createThreadMock,
  }),
  useDeleteThread: () => ({
    mutateAsync: deleteThreadMock,
  }),
  useThreadState: (threadId: string | null) => {
    const data = threadId
      ? {
          thread_id: threadId,
          status: threadId === "thread-a" ? "awaiting_approval" : "completed",
          is_plan_mode: threadId === "thread-a",
          title: threadId === "thread-a" ? "Northstar" : "Forge",
          summary: "Thread summary",
          selected_model: threadId === "thread-a" ? "openai_compatible" : "minimax",
          selected_profile: threadId === "thread-a" ? "default" : "coder",
          selected_reasoning_effort: "high",
          effective_model: "openai_compatible",
          active_model: "openai_compatible",
          reasoning_effort: "xhigh",
          visible_tool_names: ["write_file", "patch_file", "read_file"],
          deferred_tool_names: ["capability_search"],
          enabled_skill_ids: ["minimal-operator-skill"],
          memory_namespace: "global/default",
          injected_memory_snapshot_id: "snapshot-1",
          has_pending_approval: threadId === "thread-a",
          pending_approval_reason: threadId === "thread-a" ? "filesystem_write required" : null,
          token_usage: { input_tokens: 120, output_tokens: 42, total_tokens: 162 },
          context_window_usage:
            threadId === "thread-compacted"
              ? {
                  model: "openai_compatible",
                  concrete_model: "gpt-5.4",
                  context_tokens: 980,
                  context_source: "provider+estimated",
                  input_tokens: 900,
                  output_tokens: 80,
                  total_tokens: 980,
                  context_window_tokens: 1000,
                  auto_compact_threshold_tokens: 800,
                  usage_ratio: 0.98,
                  compact_ratio: 1,
                  compact_status: "compacted",
                  summarization_triggered: true,
                  compaction_level: 2,
                  compaction_level_label: "recursive_summary",
                  compaction_reason: "token_threshold_exceeded",
                  compaction_input_tokens: 1800,
                  compaction_summary_tokens: 120,
                  compaction_savings_tokens: 820,
                  compaction_keep_recent_turns: 4,
                }
              : {
                  model: "openai_compatible",
                  concrete_model: "gpt-5.4",
                  context_tokens: 162,
                  context_source: "provider+estimated",
                  input_tokens: 120,
                  output_tokens: 42,
                  total_tokens: 162,
                  context_window_tokens: 1000,
                  auto_compact_threshold_tokens: 800,
                  usage_ratio: 0.162,
                  compact_ratio: 0.2025,
                  compact_status: "below_threshold",
                  summarization_triggered: false,
                },
          approval_policy_summary: "Agent mode allows runtime tool execution. Read-only filesystem actions like list_dir, read_file, and extract_document run without approval; writes, shell execution, and external or otherwise guarded actions still require explicit approval.",
          allowed_local_actions: ["conversation", "filesystem_tools"],
          requires_approval_actions: ["guarded_tool_calls", "network_or_external_capabilities"],
          restricted_actions: ["unguarded_full_access_shortcuts"],
          output_artifacts: [{ kind: "output", label: "report.md", artifact_url: "/threads/thread-a/artifacts/outputs/report.md", virtual_path: "/mnt/user-data/outputs/report.md" }],
          uploaded_files: [{ kind: "upload", label: "spec.txt", artifact_url: "/threads/thread-a/artifacts/uploads/spec.txt", virtual_path: "/mnt/user-data/uploads/spec.txt" }],
          presented_artifacts: [],
          active_subagent_task_ids: threadId === "thread-a" ? ["task-1"] : [],
          subagent_tasks:
            threadId === "thread-a"
              ? [
                  {
                    task_id: "task-1",
                    parent_thread_id: "thread-a",
                    status: "running",
                    assigned_profile: "general",
                    delegation_depth: 1,
                    cancel_requested: false,
                    started_at: "2026-04-20T09:00:00Z",
                    completed_at: null,
                    timeout_at: "2026-04-20T09:15:00Z",
                    error: null,
                    summary: null,
                    requested_tool_names: ["read_file"],
                    allowed_tool_names: ["read_file"],
                  },
                ]
              : [],
          process_sessions:
            threadId === "thread-a"
              ? [
                  {
                    session_id: "proc-1",
                    thread_id: "thread-a",
                    command: "python worker.py",
                    cwd: "/mnt/user-data/workspace",
                    pid: 123,
                    status: "running",
                    exit_code: null,
                    detached: false,
                    backend: "local",
                    backend_id: "local",
                    backend_label: "Local shell",
                    interactive: true,
                    pty: false,
                    log_cursor: 0,
                    stdin_closed: false,
                    last_stdin_at: null,
                    last_signal: null,
                    last_signal_at: null,
                    columns: 100,
                    rows: 30,
                    input_history: [{ text_preview: "seed", submitted: true, byte_count: 4, created_at: "2026-04-20T09:00:01Z" }],
                    started_at: "2026-04-20T09:00:00Z",
                    completed_at: null,
                    log_path: "/logs/proc-1.log",
                    last_output: "booting",
                  },
                ]
              : [],
          recent_approval_events: [
            {
              request_id: "req-1",
              decision: "approved",
              reason: "filesystem_write required",
              action_kind: "tool_call",
              requested_permissions: ["filesystem_write"],
              scope_options: ["turn", "session"],
              status: "resolved",
              execution_mode: "agent",
              created_at: "2026-04-19T09:00:00Z",
              resolved_at: "2026-04-19T09:00:05Z",
            },
          ],
          last_error: null,
          runtime_capabilities: {
            summarization_enabled: true,
            plan_mode_enabled: true,
            view_image_enabled: true,
            memory_enabled: true,
            skills_count: 1,
            mcp_servers_connected: 1,
            sandbox_mode: "host_isolated",
            supported_sandbox_modes: ["host_isolated"],
            isolated_sandbox_supported: false,
            guardrails_enabled: true,
          },
        }
      : undefined;
    return { data: data ? { ...data, ...(runStreamFixture.threadStatePatch ?? {}) } : undefined };
  },
  useThreadDetail: (threadId: string | null) => {
    const state = threadId
      ? {
            thread_id: threadId,
            status: threadId === "thread-a" ? "awaiting_approval" : "completed",
            is_plan_mode: threadId === "thread-a",
            title: threadId === "thread-a" ? "Northstar" : "Forge",
            summary: "Thread summary",
            selected_model: threadId === "thread-a" ? "openai_compatible" : "minimax",
            selected_profile: threadId === "thread-a" ? "default" : "coder",
            selected_reasoning_effort: "high",
            effective_model: "openai_compatible",
            active_model: "openai_compatible",
            reasoning_effort: "xhigh",
            execution_mode: threadId === "thread-a" ? "chat" : "agent",
            visible_tool_names: ["write_file", "patch_file", "read_file"],
            deferred_tool_names: ["capability_search"],
            enabled_skill_ids: ["minimal-operator-skill"],
            memory_namespace: "global/default",
            injected_memory_snapshot_id: "snapshot-1",
            has_pending_approval: threadId === "thread-a",
            pending_approval_reason: threadId === "thread-a" ? "filesystem_write required" : null,
            token_usage: { input_tokens: 120, output_tokens: 42, total_tokens: 162 },
            context_window_usage:
              threadId === "thread-compacted"
                ? {
                    model: "openai_compatible",
                    concrete_model: "gpt-5.4",
                    context_tokens: 980,
                    context_source: "provider+estimated",
                    input_tokens: 900,
                    output_tokens: 80,
                    total_tokens: 980,
                    context_window_tokens: 1000,
                    auto_compact_threshold_tokens: 800,
                    usage_ratio: 0.98,
                    compact_ratio: 1,
                    compact_status: "compacted",
                    summarization_triggered: true,
                  }
                : {
                    model: "openai_compatible",
                    concrete_model: "gpt-5.4",
                    context_tokens: 162,
                    estimated_context_tokens: 162,
                    context_source: "provider+estimated",
                    context_breakdown: {
                      messages: 44,
                      system: 58,
                      request_context: 12,
                      upload_context: 10,
                      approval_context: 8,
                      plan_context: 7,
                      memory_context: 11,
                      conversation_summary: 6,
                      todo_state: 4,
                      view_image_context: 2,
                    },
                    context_breakdown_percentages: {
                      messages: 0.2716,
                      system: 0.358,
                      request_context: 0.0741,
                      upload_context: 0.0617,
                      approval_context: 0.0494,
                      plan_context: 0.0432,
                      memory_context: 0.0679,
                      conversation_summary: 0.037,
                      todo_state: 0.0247,
                      view_image_context: 0.0123,
                    },
                    input_tokens: 120,
                    output_tokens: 42,
                    total_tokens: 162,
                    context_window_tokens: 1000,
                    auto_compact_threshold_tokens: 800,
                    usage_ratio: 0.162,
                    compact_ratio: 0.2025,
                    compact_status: "below_threshold",
                    summarization_triggered: false,
                  },
            approval_policy_summary: "Agent mode allows runtime tool execution. Read-only filesystem actions like list_dir, read_file, and extract_document run without approval; writes, shell execution, and external or otherwise guarded actions still require explicit approval.",
            allowed_local_actions: ["conversation", "filesystem_tools"],
            requires_approval_actions: ["guarded_tool_calls", "network_or_external_capabilities"],
            restricted_actions: ["unguarded_full_access_shortcuts"],
            output_artifacts: [{ kind: "output", label: "report.md", artifact_url: "/threads/thread-a/artifacts/outputs/report.md", virtual_path: "/mnt/user-data/outputs/report.md" }],
            uploaded_files: [{ kind: "upload", label: "spec.txt", artifact_url: "/threads/thread-a/artifacts/uploads/spec.txt", virtual_path: "/mnt/user-data/uploads/spec.txt" }],
            presented_artifacts: [],
            active_subagent_task_ids: threadId === "thread-a" ? ["task-1"] : [],
            subagent_tasks:
              threadId === "thread-a"
                ? [
                    {
                      task_id: "task-1",
                      parent_thread_id: "thread-a",
                      status: "running",
                      assigned_profile: "general",
                      delegation_depth: 1,
                      cancel_requested: false,
                      started_at: "2026-04-20T09:00:00Z",
                      completed_at: null,
                      timeout_at: "2026-04-20T09:15:00Z",
                      error: null,
                      summary: null,
                      requested_tool_names: ["read_file"],
                      allowed_tool_names: ["read_file"],
                    },
                  ]
                : [],
            process_sessions:
              threadId === "thread-a"
                ? [
                    {
                      session_id: "proc-1",
                      thread_id: "thread-a",
                      command: "python worker.py",
                      cwd: "/mnt/user-data/workspace",
                      pid: 123,
                      status: "running",
                      exit_code: null,
                      detached: false,
                      backend: "local",
                      backend_id: "local",
                      backend_label: "Local shell",
                      interactive: true,
                      pty: false,
                      log_cursor: 0,
                      stdin_closed: false,
                      last_stdin_at: null,
                      last_signal: null,
                      last_signal_at: null,
                      columns: 100,
                      rows: 30,
                      input_history: [{ text_preview: "seed", submitted: true, byte_count: 4, created_at: "2026-04-20T09:00:01Z" }],
                      started_at: "2026-04-20T09:00:00Z",
                      completed_at: null,
                      log_path: "/logs/proc-1.log",
                      last_output: "booting",
                    },
                  ]
                : [],
            recent_tool_activity: [
              {
                tool_call_id: "call-write-1",
                name: "write_file",
                status: threadId === "thread-a" ? "needs_approval" : "completed",
                started_at: "2026-04-19T09:00:00Z",
                completed_at: threadId === "thread-a" ? null : "2026-04-19T09:00:02Z",
                duration_ms: threadId === "thread-a" ? null : 210,
                args: { path: "/mnt/user-data/workspace/plan.md" },
                result_text: threadId === "thread-a" ? null : "WROTE:/mnt/user-data/workspace/plan.md",
              },
            ],
            recent_approval_events: [
              {
                request_id: "req-1",
                decision: "approved",
                reason: "filesystem_write required",
                action_kind: "tool_call",
                requested_permissions: ["filesystem_write"],
                scope_options: ["turn", "session"],
                status: "resolved",
                execution_mode: "agent",
                created_at: "2026-04-19T09:00:00Z",
                resolved_at: "2026-04-19T09:00:05Z",
              },
            ],
            last_error: null,
            runtime_capabilities: {
              summarization_enabled: true,
              plan_mode_enabled: true,
              view_image_enabled: true,
              memory_enabled: true,
              skills_count: 1,
              mcp_servers_connected: 1,
              sandbox_mode: "host_isolated",
              supported_sandbox_modes: ["host_isolated"],
              isolated_sandbox_supported: false,
              guardrails_enabled: true,
            },
          }
      : undefined;
    return {
      data: threadId
        ? {
          thread: {
            thread_id: threadId,
            title: threadId === "thread-a" ? "Northstar" : "Forge",
            status: threadId === "thread-a" ? "awaiting_approval" : "completed",
            updated_at: "",
            last_user_message_preview: threadId === "thread-a" ? "Need approval" : "done",
            has_pending_approval: threadId === "thread-a",
            has_active_subagent_tasks: threadId === "thread-a",
          },
          state: state ? { ...state, ...(runStreamFixture.threadStatePatch ?? {}) } : undefined,
          messages: [
            {
              message_id: "message-0",
              role: "human",
              content: "Need approval",
              steps: [],
              content_blocks: [],
              reasoning: null,
              tool_calls: [],
              tool_call_id: null,
              name: null,
              status: null,
              artifact_refs: [],
              approval: null,
            },
            {
              message_id: "message-1",
              role: "ai",
              content: threadId === "thread-a" ? "approval required" : "done",
              steps:
                threadId === "thread-a"
                  ? [
                      {
                        step_id: "message-1:call",
                        message_id: "message-1",
                        type: "call",
                        title: "已运行 List Directory",
                        status: "success",
                        duration: "2s",
                        duration_ms: 2000,
                        action: "{\"path\":\"/mnt/user-data/workspace\"}",
                        payload: '["src","tests"]',
                        language: "json",
                        tool_name: "list_dir",
                        tool_call_id: "call-list-1",
                        order: 0,
                      },
                      {
                        step_id: "message-1:content",
                        message_id: "message-1",
                        type: "content",
                        title: "最终回答",
                        status: "success",
                        payload: "approval required",
                        language: "markdown",
                        order: 1,
                      },
                    ]
                  : [
                      {
                        step_id: "message-1:content",
                        message_id: "message-1",
                        type: "content",
                        title: "最终回答",
                        status: "success",
                        payload: "done",
                        language: "markdown",
                        order: 0,
                      },
                    ],
              content_blocks: [],
              reasoning: null,
              tool_calls:
                threadId === "thread-a"
                  ? [
                      {
                        tool_call_id: "call-list-1",
                        name: "list_dir",
                        display_name: "List Directory",
                        source_kind: "builtin",
                        source_id: "core",
                        capability_group: "filesystem",
                        tool_execution_mode: "sync",
                        args: { path: "/mnt/user-data/workspace" },
                        status: "completed",
                        result_text: '["src","tests"]',
                        started_at: "2026-04-20T09:00:00Z",
                        completed_at: "2026-04-20T09:00:02Z",
                        duration_ms: 30,
                      },
                    ]
                  : [],
              tool_call_id: null,
              name: null,
              status: null,
              artifact_refs: [],
              approval:
                threadId === "thread-a"
                  ? {
                      decision: "needs_user_approval",
                      reason: "filesystem_write required",
                      action_kind: "tool_call",
                      request_id: "req-1",
                      requested_permissions: [],
                      scope_options: [],
                    }
                  : null,
            },
            ...(threadId === "thread-a"
              ? [
                  {
                    message_id: "message-1-tool",
                    role: "tool",
                    content: '["src","tests"]',
                    steps: [],
                    content_blocks: [],
                    reasoning: null,
                    tool_calls: [],
                    tool_call_id: "call-list-1",
                    name: "list_dir",
                    status: "completed",
                    artifact_refs: [],
                    approval: null,
                  },
                ]
              : []),
          ],
          pending_approval:
            threadId === "thread-a"
              ? {
                  decision: "needs_user_approval",
                  reason: "filesystem_write required",
                  action_kind: "tool_call",
                  request_id: "req-1",
                  requested_permissions: [],
                  scope_options: [],
                }
              : null,
          stream_capabilities: {
            supports_step_chain: true,
            supports_message_delta: false,
            supports_reasoning_delta: false,
            supports_structured_events: true,
          },
        }
      : undefined,
    };
  },
  useThreadMessageWindowLoader: () => vi.fn().mockResolvedValue({
    messages: [],
    message_window: {
      total: 0,
      offset: 0,
      limit: 120,
      returned: 0,
      has_more_before: false,
      has_more_after: false,
      truncated: false,
      start_message_id: null,
      end_message_id: null,
    },
    state: null,
  }),
  useThreadSettings: (threadId: string | null) => ({
    data: threadId
      ? {
          thread_id: threadId,
          execution_mode: threadId === "thread-a" ? "chat" : "agent",
          selected_model: threadId === "thread-a" ? "openai_compatible" : "minimax",
          selected_profile: threadId === "thread-a" ? "default" : "coder",
          selected_reasoning_effort: "high",
          is_plan_mode: threadId === "thread-a",
          workspace_root: threadId === "thread-a" ? "E:\\projects\\northstar" : null,
          workspace_mode: threadId === "thread-a" ? "external" : "thread",
          anvil_home: "C:\\Users\\tester\\.anvil",
          anvil_profile: threadId === "thread-a" ? "default" : "coder",
          anvil_profile_home: threadId === "thread-a" ? "C:\\Users\\tester\\.anvil" : "C:\\Users\\tester\\.anvil\\profiles\\coder",
          resolved_workspace_path: threadId === "thread-a" ? "E:\\projects\\northstar" : "E:\\python\\python学习\\harness\\Anvil\\.anvil\\threads\\thread-b\\workspace",
        }
      : undefined,
  }),
  useThreadRunStream: () => ({
    events: runStreamFixture.events,
    error: runStreamFixture.error,
    isStreaming: runStreamFixture.isStreaming,
    start: runStreamFixture.start,
    resumeApproval: runStreamFixture.resumeApproval,
    editLatestAndResend: runStreamFixture.editLatestAndResend,
    stop: runStreamFixture.stop,
  }),
  useThreadEvaluationReport: () => ({
    data: null,
    error: null,
    isFetching: false,
  }),
  useEnqueueThreadFollowup: () => ({
    mutateAsync: enqueueFollowupMock,
    isPending: false,
  }),
  useUpdateThreadFollowup: () => ({
    mutateAsync: updateFollowupMock,
    isPending: false,
  }),
  useDeleteThreadFollowup: () => ({
    mutateAsync: deleteFollowupMock,
    isPending: false,
  }),
  usePopNextThreadFollowup: () => ({
    mutateAsync: popNextFollowupMock,
    isPending: false,
  }),
  useCancelThreadApproval: () => ({
    mutateAsync: cancelApprovalMock,
    isPending: false,
  }),
  useUpdateThreadSettings: () => ({
    mutateAsync: updateThreadSettingsMock,
  }),
  useWaitSubagentTask: () => ({ mutateAsync: waitSubagentTaskMock }),
  useCancelSubagentTask: () => ({ mutateAsync: cancelSubagentTaskMock }),
  useWaitProcessSession: () => ({ mutateAsync: waitProcessSessionMock }),
  useKillProcessSession: () => ({ mutateAsync: killProcessSessionMock }),
  useWriteProcessStdin: () => ({ mutateAsync: writeProcessStdinMock }),
  useCloseProcessStdin: () => ({ mutateAsync: closeProcessStdinMock }),
  useInterruptProcessSession: () => ({ mutateAsync: interruptProcessSessionMock }),
  useResizeProcessSession: () => ({ mutateAsync: resizeProcessSessionMock }),
  useProcessLog: () => ({
    data: { session_id: "proc-1", status: "running", output: "booting", total_lines: 1, showing: "1 lines", next_offset: 1, start_offset: 0 },
    isFetching: false,
    refetch: vi.fn(),
  }),
  useProcessCapabilities: () => ({
    data: {
      backend_id: "local",
      label: "Local",
      kind: "local",
      configured: true,
      executable: true,
      isolated: false,
      remote: false,
      notes: [],
    },
  }),
  useScheduledTasks: () => ({
    data: { items: [], executions: [] },
    isFetching: false,
  }),
  useRunScheduledTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePauseScheduledTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResumeScheduledTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScheduledTaskAutomation: () => ({ data: null, isFetching: false, refetch: vi.fn() }),
  useRunScheduledTaskAutomation: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/src/core/uploads/hooks", () => ({
  useUploads: () => ({
    data: {
      thread_id: "thread-a",
      files: [{ filename: "spec.txt", kind: "uploads", virtual_path: "/mnt/user-data/uploads/spec.txt", artifact_url: "/threads/thread-a/artifacts/uploads/spec.txt" }],
    },
  }),
  useUploadFiles: () => ({ mutateAsync: uploadFilesMock }),
}));

vi.mock("@/src/core/models/hooks", () => ({
  useModels: () => ({ data: [{ name: "openai_compatible" }, { name: "minimax" }] }),
  useUpdateModelSelection: () => ({ mutate: vi.fn(), isPending: false, variables: null }),
}));

vi.mock("@/src/core/config/hooks", () => ({
  useConfigOverview: () => ({
    data: {
      status: "ok",
      config_fingerprint: "cfg-current",
      models: { total: 2, available: 2, source_counts: {}, enabled_source_counts: {} },
      tools: { total: toolCatalogFixture.length, enabled: 3, ready: 3, source_counts: {}, enabled_source_counts: {} },
      skills: { total: 1, enabled: 1, source_counts: { repo: 1 }, enabled_source_counts: { repo: 1 } },
      memory: { total: 1, enabled: 1, quality_score: 1, source_counts: {}, enabled_source_counts: {} },
      mcp: { total: 1, enabled: 1, ready: 1, source_counts: {}, enabled_source_counts: {} },
      plugins: { total: 1, enabled: 1, source_counts: {}, enabled_source_counts: {} },
      scheduled: { total: 0, enabled: 0, source_counts: {}, enabled_source_counts: {} },
    },
    isLoading: false,
    isFetching: false,
    isError: false,
  }),
}));

vi.mock("@/src/core/skills/hooks", () => ({
  useSkills: () => ({
    data: [skillFixture],
  }),
  useSkill: () => ({
    data: skillFixture,
  }),
  useSkillContent: () => ({
    data: skillContentFixture,
  }),
  useSkillFiles: () => ({
    data: skillFilesFixture,
  }),
  useSkillFile: () => ({
    data: skillFileFixture,
  }),
  useSkillProcedures: () => ({
    data: skillProceduresFixture,
    refetch: vi.fn(),
    isFetching: false,
  }),
  usePromoteSkillProcedure: () => ({
    mutateAsync: promoteSkillProcedureMock,
    isPending: false,
  }),
  useRejectSkillProcedure: () => ({
    mutateAsync: rejectSkillProcedureMock,
    isPending: false,
  }),
  useRestoreSkillProcedure: () => ({
    mutateAsync: restoreSkillProcedureMock,
    isPending: false,
  }),
  useSkillCuratorAutomation: () => ({
    data: null,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useRunSkillCuratorAutomation: () => ({
    mutateAsync: runSkillCuratorAutomationMock,
    isPending: false,
  }),
  useRunSkillCuratorMaintenance: () => ({
    mutateAsync: runSkillCuratorMaintenanceMock,
    isPending: false,
  }),
  useManageSkill: () => ({
    mutateAsync: manageSkillMock,
    isPending: false,
  }),
  useReloadSkills: () => ({
    mutateAsync: reloadSkillsMock,
    isPending: false,
  }),
}));

vi.mock("@/src/core/catalog/hooks", () => ({
  useToolCatalog: () => ({
    data: toolCatalogFixture,
  }),
  useCatalogTools: () => ({
    data: toolCatalogFixture,
  }),
  useToolCatalogEntry: (nameOrCapabilityId: string | null) => ({
    data: toolCatalogFixture.find(
      (entry) => entry.capability_id === nameOrCapabilityId || entry.name === nameOrCapabilityId,
    ) ?? toolCatalogFixture[0],
  }),
}));

vi.mock("@/src/core/plugins/hooks", () => ({
  usePlugins: () => ({
    data: pluginsFixture,
    isLoading: false,
  }),
  usePluginCatalog: () => ({
    data: pluginCatalogFixture,
    isLoading: false,
  }),
  usePluginRegistries: () => ({
    data: pluginRegistriesFixture,
    isLoading: false,
  }),
  useInstallPlugin: () => ({
    mutateAsync: installPluginMock,
    isPending: false,
  }),
  useUpsertPluginRegistry: () => ({
    mutateAsync: upsertPluginRegistryMock,
    isPending: false,
  }),
  useRefreshPluginRegistry: () => ({
    mutateAsync: refreshPluginRegistryMock,
    isPending: false,
  }),
  useDeletePluginRegistry: () => ({
    mutateAsync: deletePluginRegistryMock,
    isPending: false,
  }),
}));

vi.mock("@/src/core/mcp/hooks", () => ({
  useReloadMcpQueries: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useMcpConfigOverview: () => ({
    data: {
      config_path: "~/.anvil/config.yaml",
      server_count: mcpServersFixture.length,
      enabled_count: 1,
      ready_count: 1,
      auth_required_count: 0,
      disabled_count: 0,
      failed_count: 0,
      hidden_from_model_count: 0,
    },
  }),
  useMcpServers: () => ({
    data: mcpServersFixture,
  }),
  useMcpServerTools: () => ({
    data: { server_id: "github", tools: ["repo_search", "issue_lookup"], tool_count: 2, resource_count: 1, prompt_count: 1 },
  }),
  useMcpServerProvenance: () => ({
    data: { source: "plugin", config_source: "plugins/core-governance" },
  }),
  useMcpResources: () => ({
    data: mcpResourcesFixture,
  }),
  useMcpResource: (_serverId: string | null, resourceId: string | null) => ({
    data: resourceId
      ? {
          server_id: "github",
          resource_id: resourceId,
          title: "README",
          description: "Repo readme",
          path: "/README.md",
          metadata: { kind: "markdown" },
          content: "# README",
        }
      : undefined,
  }),
  useMcpPrompts: () => ({
    data: mcpPromptsFixture,
  }),
  useRenderMcpPrompt: () => ({
    mutateAsync: renderMcpPromptMock,
    isPending: false,
  }),
  useRefreshMcpServer: () => ({
    mutateAsync: refreshMcpServerMock,
    isPending: false,
  }),
  useReconnectMcpServer: () => ({
    mutateAsync: reconnectMcpServerMock,
    isPending: false,
  }),
  useUpsertMcpServers: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useDeleteMcpServer: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));

vi.mock("@/src/core/memory/hooks", () => ({
  useMemoryOverview: () => ({
    data: {
      active_engine_id: "hcms",
      runtime_mode: "hcms",
      capture_status: "native",
      migration_status: {},
      store_count: 2,
      archive_turn_count: 3,
      reflection_job_count: 4,
      stores: [
        { store_id: "hcms_workspace", display_name: "HCMS Workspace Layer", max_chars: 2800, injection_chars: 1400, max_tokens: 700, injection_tokens: 350, effective_max_tokens: 700, effective_injection_tokens: 350, budget_source: "hcms", actual_injection_tokens: 88, actual_injection_chars: 352, usage_chars: 320, usage_tokens: 80, entry_count: 2, summary: "workspace summary", summary_sections: { hcms_workspace: { recentMonths: "Northstar work is active." } }, snapshot_status: "frozen", updated_at: "" },
        { store_id: "hcms_user", display_name: "HCMS User Layer", max_chars: 1800, injection_chars: 1000, max_tokens: 450, injection_tokens: 250, effective_max_tokens: 450, effective_injection_tokens: 250, budget_source: "hcms", actual_injection_tokens: 45, actual_injection_chars: 180, usage_chars: 180, usage_tokens: 45, entry_count: 1, summary: "user summary", summary_sections: { hcms_user: { topOfMind: "Prefers concise updates." } }, snapshot_status: "frozen", updated_at: "" },
      ],
    },
    refetch: vi.fn(),
    isFetching: false,
  }),
  useMemoryLayers: () => ({
    data: [
      { layer_id: "session", display_name: "Session Memory", description: "Current thread history and prompt snapshots.", writable: false, entry_count: 0, store_id: null, summary: "Session recall is derived." },
      { layer_id: "user", display_name: "HCMS User Layer", description: "User preferences.", writable: true, entry_count: 1, store_id: "hcms_user", summary: "user summary" },
      { layer_id: "workspace", display_name: "HCMS Workspace Layer", description: "Global work context.", writable: true, entry_count: 2, store_id: "hcms_workspace", summary: "workspace summary" },
    ],
  }),
  useSessionMemory: () => ({
    data: {
      layer_id: "session",
      thread_id: "thread-a",
      memory_namespace: "global/default",
      injected_memory_snapshot_id: "snapshot-1",
      archive_turn_count: 2,
      session_summary: "Session summary for thread-a.",
      recent_turns: [
        { archive_id: "turn-1", thread_id: "thread-a", user_content: "Remember Northstar", assistant_content: "Stored it", status: "completed", created_at: "" },
      ],
      latest_prompt_snapshot: { snapshot_id: "snap-1", prompt_hash: "hash-1", skills_fingerprint: null, memory_fingerprint: "mem-1", config_fingerprint: "cfg-1", created_at: "" },
    },
  }),
  useMemoryStores: () => ({
    data: [
      { store_id: "hcms_workspace", display_name: "HCMS Workspace Layer", max_chars: 2800, injection_chars: 1400, max_tokens: 700, injection_tokens: 350, effective_max_tokens: 700, effective_injection_tokens: 350, budget_source: "hcms", actual_injection_tokens: 88, actual_injection_chars: 352, usage_chars: 320, usage_tokens: 80, entry_count: 2, summary: "workspace summary", summary_sections: { hcms_workspace: { recentMonths: "Northstar work is active." } }, snapshot_status: "frozen", updated_at: "" },
      { store_id: "hcms_user", display_name: "HCMS User Layer", max_chars: 1800, injection_chars: 1000, max_tokens: 450, injection_tokens: 250, effective_max_tokens: 450, effective_injection_tokens: 250, budget_source: "hcms", actual_injection_tokens: 45, actual_injection_chars: 180, usage_chars: 180, usage_tokens: 45, entry_count: 1, summary: "user summary", summary_sections: { hcms_user: { topOfMind: "Prefers concise updates." } }, snapshot_status: "frozen", updated_at: "" },
    ],
  }),
  useMemoryLayerEntries: (layerId: "user" | "workspace" | "session") => ({
    data:
      layerId === "user"
        ? [{ entry_id: "entry-profile", memory_id: "entry-profile", store_id: "hcms_user", layer_id: "user", content: "Prefers concise implementation updates.", category: "preference", source_kind: "turn_sync", priority: 0.8, confidence: 0.92, salience: 0.86, metadata: { profile_class: "style" }, last_accessed_at: "", evidence_refs: ["turn-profile"], supersedes: [], conflicts_with: [], expires_at: null, effective_score: 0.9, status: "active", created_at: "", updated_at: "" }]
        : [{ entry_id: "entry-1", memory_id: "entry-1", store_id: "hcms_workspace", layer_id: "workspace", content: "Northstar is active", category: "project_context", source_kind: "turn_sync", priority: 0.7, confidence: 0.92, salience: 0.7, metadata: {}, last_accessed_at: "", evidence_refs: ["turn-1"], supersedes: [], conflicts_with: [], expires_at: null, effective_score: 0.88, status: "active", created_at: "", updated_at: "" }],
    refetch: vi.fn(),
    isFetching: false,
  }),
  useMemoryStoreEntries: () => ({
    data: [{ entry_id: "entry-1", memory_id: "entry-1", store_id: "hcms_workspace", layer_id: "workspace", content: "Northstar is active", category: "project_context", source_kind: "turn_sync", priority: 0.7, confidence: 0.92, salience: 0.7, metadata: {}, last_accessed_at: "", evidence_refs: ["turn-1"], supersedes: [], conflicts_with: [], expires_at: null, effective_score: 0.88, status: "active", created_at: "", updated_at: "" }],
  }),
  useMemoryAdminAudit: () => ({
    data: { snapshot: {}, observation_queue_count: 1, conflict_count: 1, staleness_count: 1, health: {}, engines: [{ engine_id: "hcms", health: "ok" }] },
  }),
  useMemoryHealth: () => ({
    data: {
      status: "healthy",
      quality_score: 0.86,
      archive_turn_count: 3,
      observation_queue_count: 1,
      conflict_count: 1,
      stale_count: 1,
      engine_count: 1,
      engine_health: { hcms: "ok" },
      stores: [
        {
          store_id: "hcms_workspace",
          layer_id: "workspace",
          status: "warning",
          entry_count: 2,
          active_count: 2,
          inactive_count: 0,
          low_confidence_count: 1,
          low_salience_count: 0,
          missing_evidence_count: 1,
          duplicate_cluster_count: 0,
          conflict_count: 1,
          stale_count: 1,
          injection_token_pressure: 0.14,
          quality_score: 0.76,
          issues: [
            {
              issue_id: "issue-1",
              severity: "warning",
              kind: "missing_evidence",
              store_id: "hcms_workspace",
              layer_id: "workspace",
              memory_id: "entry-1",
              related_memory_ids: [],
              message: "Entry needs supporting evidence.",
              recommendation: "Review the original turn before promotion.",
              score: 0.6,
            },
          ],
        },
      ],
      issues: [
        {
          issue_id: "global-issue-1",
          severity: "warning",
          kind: "low_confidence",
          store_id: null,
          layer_id: null,
          memory_id: null,
          related_memory_ids: [],
          message: "HCMS confidence is below the publication target.",
          recommendation: "Reinforce or archive low-confidence memories during maintenance.",
          score: 0.5,
        },
      ],
      recommendations: ["Reinforce or archive low-confidence memories during maintenance."],
      generated_at: "2026-05-14T00:00:00Z",
    },
    refetch: vi.fn(),
    isFetching: false,
  }),
  useMemoryStaleness: () => ({
    data: [{ memory_id: "memory-stale", layer_id: "workspace", stale_score: 0.8, reason: "memory has not been accessed recently", last_accessed_at: "", expires_at: null }],
    refetch: vi.fn(),
    isFetching: false,
  }),
  useFlushMemory: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ thread_id: null, observations_processed: 0, entries_written: 0, quality_issues_created: 0, entries_skipped: 0, facts_removed: 0, errors: [], written_memory_ids: [], quality_issue_ids: [] }),
    isPending: false,
  }),
  useBatchGovernMemory: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ candidate_count: 0, executed_count: 0, errors: [], items: [] }),
    isPending: false,
    data: null,
  }),
  useRunMemoryMaintenance: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ status: "noop" }),
    isPending: false,
    data: null,
  }),
  useMemoryMaintenanceAutomation: () => ({
    data: null,
    refetch: vi.fn(),
    isFetching: false,
  }),
  useRunMemoryMaintenanceAutomation: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ ran: false, reason: "not_due" }),
    isPending: false,
    data: null,
  }),
  useRunMemoryBenchmark: () => ({
    mutateAsync: vi.fn().mockResolvedValue({
      suite_id: "ops-memory-smoke",
      passed: true,
      score: 1,
      case_count: 1,
      passed_count: 1,
      failed_count: 0,
      recall_hit_rate: 1,
      false_positive_rate: 0,
      average_evidence_count: 1,
      cases: [
        {
          case_id: "ops-entry-profile",
          query: "Prefers concise implementation updates.",
          passed: true,
          score: 1,
          recall_hits: 1,
          expected_count: 1,
          false_positive_count: 0,
          evidence_count: 1,
          top_evidence: [],
          missing_expectations: [],
          false_positives: [],
          summary: "Profile preference was recalled.",
        },
      ],
      recommendations: [],
      generated_at: "2026-05-14T00:00:00Z",
    }),
    data: null,
    isPending: false,
  }),
  useMemoryBenchmarkSuites: () => ({
    data: [
      {
        suite_id: "ops-memory-smoke",
        name: "Ops Memory Smoke",
        description: "Memory governance smoke benchmark.",
        case_count: 1,
      },
    ],
    refetch: vi.fn(),
    isFetching: false,
  }),
  useMemoryBenchmarkRuns: () => ({
    data: [],
    refetch: vi.fn(),
    isFetching: false,
  }),
  useRunMemoryBenchmarkSuite: () => ({
    mutateAsync: vi.fn().mockResolvedValue({
      suite_id: "ops-memory-smoke",
      passed: true,
      score: 1,
      case_count: 1,
      passed_count: 1,
      failed_count: 0,
      recall_hit_rate: 1,
      false_positive_rate: 0,
      average_evidence_count: 1,
      cases: [],
      recommendations: [],
      generated_at: "2026-05-14T00:00:00Z",
    }),
    isPending: false,
  }),
  useExportMemoryAdmin: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ hcms: {}, quality_issues: [], archive_turn_count: 0 }),
    isPending: false,
  }),
  useImportMemoryAdmin: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ memories_imported: 0, quality_issues_imported: 0, status: "ok" }),
    isPending: false,
  }),
  useCreateMemoryEntry: () => ({
    mutateAsync: createMemoryEntryMock,
  }),
  useCreateMemoryLayerEntry: () => ({
    mutateAsync: createMemoryLayerEntryMock,
  }),
  useUpdateMemoryEntry: () => ({
    mutateAsync: updateMemoryEntryMock,
  }),
  useUpdateMemoryLayerEntry: () => ({
    mutateAsync: updateMemoryLayerEntryMock,
  }),
  useDeleteMemoryEntry: () => ({
    mutateAsync: deleteMemoryEntryMock,
  }),
  useDeleteMemoryLayerEntry: () => ({
    mutateAsync: deleteMemoryLayerEntryMock,
  }),
  useMemoryArchiveSearch: () => ({
    mutateAsync: searchArchiveMock,
    data: null,
    isPending: false,
  }),
  useSessionSearch: () => ({
    mutateAsync: searchSessionsMock,
    data: sessionSearchResultFixture,
    isPending: false,
  }),
  useMemoryTrace: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ items: [] }),
    data: {
      items: [
        {
          trace_id: "trace-1",
          thread_id: "thread-a",
          query: "Northstar",
          trace_kind: "recall",
          target_id: null,
          engine_notes: ["HCMS surfaced evidence."],
          evidence: [
            {
              evidence_id: "ev-1",
              source_kind: "memory",
              source_id: "hcms_workspace",
              layer_id: "workspace",
              memory_id: "mem-1",
              archive_id: null,
              thread_id: "thread-a",
              score: 1.2,
              reason: "lexical memory match",
              excerpt: "Northstar is active",
            },
          ],
          created_at: "",
        },
      ],
    },
    isPending: false,
  }),
  useHCMSRecall: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ items: [] }),
    data: {
      query: "Northstar",
      items: [
        {
          memory_id: "mem-1",
          score: 0.97,
          raw_scores: { lexical: 0.8, graph: 0.7 },
          ranks: { lexical: 1, graph: 1 },
          explanation: "four-stream merged match",
          memory: {
            memory_id: "mem-1",
            version: 2,
            parent_id: null,
            content: "Northstar is active",
            summary: "Northstar rollout context",
            category: "project_context",
            confidence: 0.92,
            salience: 0.7,
            state: "active",
            source_thread_id: "thread-a",
            source_type: "manual",
            tags: [],
            entities: ["Northstar"],
            concepts: ["rollout"],
            evidence: [{ evidence_id: "hcms-ev-1", type: "observation", content: "User stated Northstar is active", weight: 0.9, timestamp: "", source_id: "thread-a", metadata: {} }],
            metadata: {},
            created_at: "",
            updated_at: "",
            accessed_at: "",
          },
        },
      ],
      metrics: { llm_calls_avoided: 2, deterministic_updates: 2, recall_count: 1, last_latency_ms: 14, recall_hit_rate: 1 },
      engine_notes: ["HCMS four-stream recall active"],
    },
    isPending: false,
  }),
  useHCMSWhy: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ paths: [] }),
    data: {
      query: "Northstar",
      paths: [
        {
          nodes: [
            { memory_id: "mem-1", event_type: "memory", timestamp: "", confidence: 0.92 },
            { memory_id: "mem-2", event_type: "memory", timestamp: "", confidence: 0.88 },
          ],
          edges: [],
          total_strength: 0.8,
          confidence: 0.9,
        },
      ],
      engine_notes: ["HCMS causal reasoning active"],
    },
    isPending: false,
  }),
  useHCMSMemories: () => ({
    data: {
      items: [
        {
          memory_id: "mem-1",
          version: 2,
          parent_id: null,
          content: "Northstar is active",
          summary: "Northstar rollout context",
          category: "project_context",
          confidence: 0.92,
          salience: 0.7,
          state: "active",
          source_thread_id: "thread-a",
          source_type: "manual",
          tags: [],
          entities: ["Northstar"],
          concepts: ["rollout"],
          evidence: [{ evidence_id: "hcms-ev-1", type: "observation", content: "User stated Northstar is active", weight: 0.9, timestamp: "", source_id: "thread-a", metadata: {} }],
          metadata: {},
          created_at: "",
          updated_at: "",
          accessed_at: "",
        },
        {
          memory_id: "mem-2",
          version: 1,
          parent_id: null,
          content: "Northstar rollout depends on the Forge thread context.",
          summary: "Forge context supports Northstar rollout.",
          category: "project_context",
          confidence: 0.88,
          salience: 0.68,
          state: "active",
          source_thread_id: "thread-b",
          source_type: "manual",
          tags: [],
          entities: ["Forge"],
          concepts: ["rollout"],
          evidence: [],
          metadata: {},
          created_at: "",
          updated_at: "",
          accessed_at: "",
        },
      ],
      total: 2,
      limit: 50,
      offset: 0,
      query: null,
      state: "all",
      category: null,
      layer_id: "all",
      engine_notes: ["HCMS memory list"],
    },
    refetch: vi.fn(),
    isFetching: false,
  }),
  useDeleteHCMSMemory: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ memory_id: "mem-1", status: "deleted", deleted: true, engine_notes: [] }),
    data: null,
    isPending: false,
  }),
  useGovernMemory: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ action: "archive", memory_id: "mem-1", status: "ok" }),
    isPending: false,
  }),
  useHCMSMemory: () => ({
    data: {
      memory: {
        memory_id: "mem-1",
        version: 2,
        parent_id: null,
        content: "Northstar is active",
        summary: "Northstar rollout context",
        category: "project_context",
        confidence: 0.92,
        salience: 0.7,
        state: "active",
        source_thread_id: "thread-a",
        source_type: "manual",
        tags: [],
        entities: ["Northstar"],
        concepts: ["rollout"],
        evidence: [{ evidence_id: "hcms-ev-1", type: "observation", content: "User stated Northstar is active", weight: 0.9, timestamp: "", source_id: "thread-a", metadata: {} }],
        metadata: {},
        created_at: "",
        updated_at: "",
        accessed_at: "",
      },
      engine_notes: ["HCMS memory detail active"],
    },
    isFetching: false,
  }),
  useHCMSMemoryRelations: () => ({
    data: {
      memory_id: "mem-1",
      relations: [
        {
          relation_id: "rel-1",
          source_memory_id: "mem-1",
          target_memory_id: "mem-2",
          relation_type: "supports",
          weight: 0.82,
          confidence: 0.88,
          bidirectional: false,
          metadata: {},
          created_at: "",
          updated_at: "",
          source_memory: null,
          target_memory: {
            memory_id: "mem-2",
            version: 1,
            parent_id: null,
            content: "Northstar rollout depends on the Forge thread context.",
            summary: "Forge context supports Northstar rollout.",
            category: "project_context",
            confidence: 0.88,
            salience: 0.68,
            state: "active",
            source_thread_id: "thread-b",
            source_type: "manual",
            tags: [],
            entities: ["Forge"],
            concepts: ["rollout"],
            evidence: [],
            metadata: {},
            created_at: "",
            updated_at: "",
            accessed_at: "",
          },
        },
      ],
      engine_notes: ["HCMS relation graph active"],
    },
    isFetching: false,
  }),
  useHCMSMemoryHistory: () => ({
    data: {
      memory_id: "mem-1",
      versions: [
        { version_id: "ver-1", memory_id: "mem-1", version: 1, parent_id: null, content: "Northstar is active", summary: "Northstar rollout context", diff: "", reason: "manual_create", created_at: "" },
      ],
      engine_notes: [],
    },
  }),
  useHCMSMemoryDiff: () => ({
    data: {
      memory_id: "mem-1",
      from_version: 1,
      to_version: 2,
      diff: "@@ -1 +1 @@\n-Northstar was draft\n+Northstar is active",
      confidence_delta: 0.16,
      evidence_added: ["hcms-ev-2"],
      evidence_removed: ["hcms-ev-old"],
      engine_notes: [],
    },
  }),
  useReflectionJobs: () => ({
    data: [{ job_id: "system-project-recap", name: "Project Recap", schedule_kind: "interval", target_store_id: "hcms_workspace", enabled: true, system_managed: true, template: "project_recap", instructions: null, source_query: null, interval_seconds: 3600, cron: null, next_run_at: null, last_run_at: null, last_status: "completed" }],
  }),
  useRunReflectionJob: () => ({
    mutateAsync: runReflectionJobMock,
  }),
  usePauseReflectionJob: () => ({
    mutateAsync: pauseReflectionJobMock,
  }),
  useResumeReflectionJob: () => ({
    mutateAsync: resumeReflectionJobMock,
  }),
  useRemoveReflectionJob: () => ({
    mutateAsync: removeReflectionJobMock,
  }),
}));

vi.mock("@/src/core/extensions/hooks", () => ({
  useExtensions: () => ({ data: [{ server_id: "github", status: "materialized", error: null, tool_names: ["repo_search"], transport_kind: "stdio", startup_policy: "lazy", refresh_policy: "dynamic", enabled: true, tool_count: 1 }] }),
}));

vi.mock("@/src/core/system/hooks", () => ({
  useGatewayHealth: () => ({ data: { status: "ok", phase: "phase8" } }),
}));

import { WorkspaceShell, buildOptimisticUserMessage, filesFromClipboard, shouldShowComposerRunning } from "./workspace/workspace-shell";
import { ArtifactRefList } from "./workspace/transcript/common";
import { UserStepMessage } from "./workspace/transcript/step-chain-message";
import type { MessageView } from "@/src/core/contracts";

describe("WorkspaceShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    threadsFixture = createDefaultThreadsFixture();
    runStreamFixture = {
      events: [
        { event: "run_started", data: { thread_id: "thread-a" } },
        { event: "run_completed", data: { assistant_message: "done" } },
      ],
      error: null,
      isStreaming: false,
      start: startRunMock,
      resumeApproval: resumeApprovalMock,
      editLatestAndResend: editLatestAndResendMock,
      stop: vi.fn(),
    };
    window.history.replaceState({}, "", "/threads/thread-a");
  });

  async function selectExecutionMode(label: RegExp) {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /execution mode|执行模式/i }));
    });
    await act(async () => {
      const menu = screen.getByRole("listbox", { name: /execution mode|执行模式/i });
      const candidates = within(menu).getAllByRole("option", { name: label });
      fireEvent.click(candidates[candidates.length - 1]!);
    });
  }

  async function openConfigurationCenter() {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^more$/i }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /configuration center/i }));
    });
  }

  function firstTextParent(text: string): HTMLElement {
    const element = screen.getAllByText(text).find((item) => item.getAttribute("role") !== "tooltip");
    const container = element?.closest("div");
    if (!container) {
      throw new Error(`Unable to find container for text: ${text}`);
    }
    return container;
  }

  function expectVisibleTextWithin(container: HTMLElement, text: string) {
    expect(within(container).getAllByText(text).some((item) => item.getAttribute("role") !== "tooltip")).toBe(true);
  }

  function expectVisibleText(text: string | RegExp) {
    const matches =
      typeof text === "string"
        ? screen.getAllByText(text)
        : screen.getAllByText((content) => text.test(content));
    expect(matches.some((item) => item.getAttribute("role") !== "tooltip")).toBe(true);
  }

  function renderShell(initialThreadId?: string | null): RenderResult {
    return render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId={initialThreadId} />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );
  }

  async function selectModel(label: RegExp) {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /select model/i }));
    });
    await act(async () => {
      fireEvent.click(await screen.findByRole("option", { name: label }));
    });
  }

  async function openComposerTools() {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(add|添加)$/i }));
    });
  }

  async function openUtilitiesDrawer() {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /open utilities/i }));
    });
  }

  it("extracts pasted clipboard files without falling back to pasted filenames", () => {
    const pasted = new File(["image-bytes"], "clipboard.png", { type: "image/png" });
    const clipboard = {
      files: { length: 0 },
      items: [
        {
          kind: "file",
          getAsFile: () => pasted,
        },
        {
          kind: "string",
          getAsFile: () => null,
        },
      ],
    } as unknown as DataTransfer;

    expect(filesFromClipboard(clipboard)).toEqual([pasted]);
  });

  it("keeps pasted image filenames out of the composer text", async () => {
    renderShell("thread-a");
    const composer = screen.getByRole("textbox", { name: /composer|输入区/i });
    const pasted = new File(["image-bytes"], "clipboard.png", { type: "image/png" });

    await act(async () => {
      fireEvent.paste(composer, {
        clipboardData: {
          files: [pasted],
          items: [],
        },
      });
    });

    expect(composer).toHaveValue("");
    expect(screen.getAllByText("clipboard.png").some((item) => item.getAttribute("role") !== "tooltip")).toBe(true);
  });

  it("auto-creates a thread on first send when none is active", async () => {
    threadsFixture = [];
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    fireEvent.change(screen.getByPlaceholderText(/describe the next task/i), {
      target: { value: "Start without clicking create" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(send|发送)$/i }));
    });

    expect(createThreadMock).toHaveBeenCalled();
    expect(startRunMock).toHaveBeenCalledWith(
      expect.objectContaining({
        message: "Start without clicking create",
      }),
      "thread-b",
    );
    threadsFixture = [
      {
        thread_id: "thread-a",
        title: "Northstar",
        status: "awaiting_approval",
        updated_at: "",
        last_user_message_preview: "Need approval",
        has_pending_approval: true,
        has_active_subagent_tasks: true,
      },
      {
        thread_id: "thread-b",
        title: "Forge",
        status: "completed",
        updated_at: "",
        last_user_message_preview: "done",
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ];
  });

  it("shows the first user message optimistically while creating a new thread", async () => {
    threadsFixture = [];
    let resolveCreate: (value: { thread_id: string }) => void = () => {};
    createThreadMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    fireEvent.change(screen.getByPlaceholderText(/describe the next task/i), {
      target: { value: "First message should appear immediately" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(send|发送)$/i }));
      await Promise.resolve();
    });

    expect(screen.getAllByText("First message should appear immediately").some((element) => element.tagName === "P")).toBe(true);
    expect(startRunMock).not.toHaveBeenCalled();

    await act(async () => {
      resolveCreate({ thread_id: "thread-b" });
      await Promise.resolve();
    });
  });

  it("renders chat-first shell with execution modes and utility drawer", () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    expect(screen.getByText("Anvil")).toBeInTheDocument();
    expect(screen.getByText(/what are you working on/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /execution mode|执行模式/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /new chat/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /search chats/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /^more$/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /open utilities/i }));
    expect(screen.getByRole("button", { name: /close utilities/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /close utilities/i }));
    expect(screen.getByRole("button", { name: /open utilities/i })).toBeInTheDocument();
  });

  it("formats context usage in drawer settings instead of rendering raw JSON", () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-a" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open utilities/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /settings/i })[0]!);

    expect(screen.getByTestId("context-window-usage-panel")).toBeInTheDocument();
    expect(screen.getAllByText(/context window/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/token usage/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show details/i }));
    expect(screen.getAllByText("Current context").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Input").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Output").length).toBeGreaterThan(0);
    expect(screen.getAllByText("162").length).toBeGreaterThan(0);
    expect(screen.queryByText(/\{"input_tokens"/)).not.toBeInTheDocument();
  });

  it("opens the configuration center from the sidebar more menu", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    expect(within(dialog).getByText(/global configuration overview/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/not the current thread runtime capability view/i)).toBeInTheDocument();
    expect(within(dialog).queryByText("Write File")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /close utilities/i })).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /close configuration center/i }));
    });
    expect(screen.getByRole("button", { name: /open utilities/i })).toHaveAttribute("aria-expanded", "false");
  });

  it("controls process terminal sessions from the utilities drawer", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-a" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open utilities/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /processes/i })[0]!);

    expect(screen.getByText(/Local · local/i)).toBeInTheDocument();
    expect(screen.getAllByText(/python worker\.py/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/booting/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/type input for the running process/i), {
      target: { value: "hello process" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /submit line/i }));
    });
    expect(writeProcessStdinMock).toHaveBeenCalledWith({
      sessionId: "proc-1",
      body: { data: "hello process", submit: true },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /interrupt/i }));
    });
    expect(interruptProcessSessionMock).toHaveBeenCalledWith("proc-1");

    fireEvent.change(screen.getByLabelText(/terminal columns/i), { target: { value: "132" } });
    fireEvent.change(screen.getByLabelText(/terminal rows/i), { target: { value: "44" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^resize$/i }));
    });
    expect(resizeProcessSessionMock).toHaveBeenCalledWith({
      sessionId: "proc-1",
      body: { columns: 132, rows: 44 },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /close stdin/i }));
    });
    expect(closeProcessStdinMock).toHaveBeenCalledWith("proc-1");
  });

  it("restores the configuration center from query params", () => {
    window.history.replaceState({}, "", "/threads/thread-a?ops=1&surface=skills&item=minimal-operator-skill");

    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    expect(within(dialog).getAllByText("Minimal Operator Skill").length).toBeGreaterThan(0);
    expect(within(dialog).getAllByRole("button", { name: /disable/i })[0]).toBeInTheDocument();
  });

  it("refreshes the skills list from the configuration center", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^skills$/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getAllByRole("button", { name: /refresh list/i })[0]!);
    });

    expect(reloadSkillsMock).toHaveBeenCalledTimes(1);
  });

  it("shows agent-curated skill procedure candidates in the configuration center", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^skills$/i }));
    });

    expect(within(dialog).getByText(/agent-curated procedure candidates/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/Edit and verify code changes/i)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /promote/i })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /reject/i })).toBeInTheDocument();
  });

  it("rejects agent-curated skill procedure candidates from the configuration center", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^skills$/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /reject/i }));
    });

    expect(rejectSkillProcedureMock).toHaveBeenCalledWith({
      procedureId: "proc-edit-verify",
      rationale: "Rejected from Ops Console.",
    });
  });

  it("shows HCMS health in the configuration center", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^memory$/i }));
    });

    expect(within(dialog).getByText(/hcms console/i)).toBeInTheDocument();
    expect(within(dialog).getAllByText(/quality score/i).length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/hcms_workspace/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/stores 2/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/issues 1/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/evidence gaps 1/i)).toBeInTheDocument();
  });

  it("runs skill governance actions through the action dialog", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^skills$/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getAllByRole("button", { name: /disable/i })[0]);
    });

    const actionDialog = screen.getByRole("dialog", { name: /disable/i });
    await act(async () => {
      fireEvent.click(within(actionDialog).getByRole("button", { name: /run action/i }));
    });

    expect(manageSkillMock).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "disable",
        skill_id: "minimal-operator-skill",
      }),
    );
  });

  it("renders MCP prompt actions inside the configuration center", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^mcp$/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("tab", { name: /prompts/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /render prompt/i }));
    });

    const actionDialog = screen.getByRole("dialog", { name: /render prompt/i });
    await act(async () => {
      fireEvent.click(within(actionDialog).getByRole("button", { name: /run action/i }));
    });

    expect(renderMcpPromptMock).toHaveBeenCalledWith({ arguments: {} });
  });

  it("shows plugin details in the configuration center", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^plugins$/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /installed/i }));
    });

    expect(within(dialog).getAllByText("core-governance").length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/publisher/i)).toBeInTheDocument();
  });

  it("installs a catalog plugin from the configuration center without manual source entry", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^plugins$/i }));
    });

    expect(within(dialog).getAllByText("Memory HTTP Integration Notes").length).toBeGreaterThan(0);
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^install$/i }));
    });

    expect(installPluginMock).toHaveBeenCalledWith({
      source: "/plugins/memory-http-integration-notes",
      plugin_id: "memory-http-integration-notes",
      enable: true,
      force: true,
    });
  });

  it("manages plugin registry sources from the configuration center", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^plugins$/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^sources$/i }));
    });

    expect(within(dialog).getAllByText("Anvil curated plugins").length).toBeGreaterThan(0);
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /add source/i }));
    });

    const actionDialog = screen.getByRole("dialog", { name: /add source/i });
    fireEvent.change(within(actionDialog).getByLabelText(/source url\/path/i), {
      target: { value: "/team/plugins" },
    });
    fireEvent.change(within(actionDialog).getByLabelText(/source name/i), {
      target: { value: "Team plugins" },
    });
    await act(async () => {
      fireEvent.click(within(actionDialog).getByRole("button", { name: /run action/i }));
    });

    expect(upsertPluginRegistryMock).toHaveBeenCalledWith({
      source: "/team/plugins",
      registry_id: null,
      name: "Team plugins",
      enabled: true,
      trust_level: "third-party",
    });
  });

  it("closes the configuration center from the header close button", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /close configuration center/i }));
    });

    expect(screen.queryByRole("dialog", { name: /configuration center/i })).not.toBeInTheDocument();
  });

  it("does not reopen the configuration center when closing after selecting a nested configuration item", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await openConfigurationCenter();

    const dialog = screen.getByRole("dialog", { name: /configuration center/i });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /^tools$/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /patch file/i }));
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: /close configuration center/i }));
    });

    expect(screen.queryByRole("dialog", { name: /configuration center/i })).not.toBeInTheDocument();
  });

  it("renders completed step chains folded while keeping final content visible", async () => {
    runStreamFixture = {
      events: [
        {
          event: "step_started",
          data: {
            message_id: "live-1",
            step: {
              step_id: "think-1",
              message_id: "live-1",
              type: "thinking",
              title: "Analyzing...",
              status: "running",
              payload: "",
              language: "text",
              order: 0,
            },
          },
          receivedAt: 1_000,
        },
        { event: "step_delta", data: { message_id: "live-1", step_id: "think-1", payload_delta: "Plan the patch.\n\nValidate the UI." }, receivedAt: 2_000 },
        { event: "summary_update", data: { message_id: "live-1", title: "已运行 1 条消息", folded_step_count: 1 }, receivedAt: 2_500 },
        {
          event: "step_started",
          data: {
            message_id: "live-1",
            step: {
              step_id: "content-1",
              message_id: "live-1",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "",
              language: "markdown",
              order: 1,
            },
          },
          receivedAt: 3_000,
        },
        { event: "step_delta", data: { message_id: "live-1", step_id: "content-1", payload_delta: "done" }, receivedAt: 3_100 },
        {
          event: "step_updated",
          data: {
            message_id: "live-1",
            step: {
              step_id: "think-1",
              message_id: "live-1",
              type: "thinking",
              title: "已思考 2 秒",
              status: "success",
              duration: "2s",
              duration_ms: 2_000,
              payload: "Plan the patch.\n\nValidate the UI.",
              language: "text",
              order: 0,
            },
          },
          receivedAt: 3_500,
        },
        {
          event: "step_updated",
          data: {
            message_id: "live-1",
            step: {
              step_id: "content-1",
              message_id: "live-1",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "done",
              language: "markdown",
              order: 1,
            },
          },
          receivedAt: 3_700,
        },
        { event: "message_completed", data: { message_id: "live-1" }, receivedAt: 4_000 },
      ],
      error: null,
      isStreaming: false,
      start: startRunMock,
      resumeApproval: resumeApprovalMock,
      editLatestAndResend: editLatestAndResendMock,
      stop: vi.fn(),
    };

    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-b" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    expect(screen.getAllByText(/done/i).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /已运行 1 条消息/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/Plan the patch/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Validate the UI/i).length).toBeGreaterThan(0);
  });

  it("shows context token pressure from thread state", () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-a" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    const trigger = screen.getByRole("button", { name: /context window 162 \/ 1k \(16%\)/i });
    expect(trigger).toHaveClass("size-7");
    expect(within(trigger).getByTestId("context-window-indicator")).toBeInTheDocument();
    expect(within(trigger).getByText(/162 \/ 1k \(16%\)/i)).toHaveClass("sr-only");

    fireEvent.click(trigger);
    const popover = screen.getByTestId("context-window-popover");
    expect(popover).toHaveTextContent(/162 \/ 1k \(16%\)/i);
    expect(popover).toHaveTextContent(/Autocompact buffer/i);
    expect(popover).toHaveTextContent(/200\s*20%/i);
    expect(screen.queryByText(/20% to compaction/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/context window: 1k/i)).not.toBeInTheDocument();
  });

  it("shows backend dynamic context categories in the context panel", () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-a" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open utilities/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /settings/i })[0]!);
    fireEvent.click(screen.getByRole("button", { name: /show details/i }));

    expectVisibleText("Request Context");
    expectVisibleText("Upload Context");
    expectVisibleText("Approval Context");
    expectVisibleText("Plan Context");
    expectVisibleText("Dynamic Memory");
    expectVisibleText("Conversation Summary");
    expectVisibleText("Todo State");
    expectVisibleText("Image Context");
    expect(screen.getAllByText("Messages").length).toBeGreaterThan(0);
    expect(screen.getByText("104")).toBeInTheDocument();
  });

  it("keeps reported token usage separate from context pressure estimates", () => {
    runStreamFixture = {
      ...runStreamFixture,
      threadStatePatch: {
        context_window_usage: {
          model: "mimo",
          concrete_model: "mimo-thinking",
          context_tokens: 160,
          context_source: "estimated",
          input_tokens: null,
          output_tokens: null,
          total_tokens: null,
          request_count: null,
          context_window_tokens: 1000,
          auto_compact_threshold_tokens: 800,
          usage_ratio: null,
          compact_ratio: null,
          compact_status: "unknown",
          summarization_triggered: false,
        },
        token_usage: {
          input_tokens: 120,
          output_tokens: 40,
          total_tokens: 160,
          request_count: 1,
          total: { input_tokens: 120, output_tokens: 40, total_tokens: 160 },
          last: { input_tokens: 120, output_tokens: 40, total_tokens: 160 },
        },
      },
    };

    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-a" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    expect(screen.getByRole("button", { name: /context window 160 \/ 1k \(16%\)/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pending/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /open utilities/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /settings/i })[0]!);
    expect(screen.getByTestId("context-window-usage-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show details/i }));
    expectVisibleTextWithin(firstTextParent("Current context"), "160");
    expectVisibleTextWithin(firstTextParent("Input"), "120");
    expectVisibleTextWithin(firstTextParent("Output"), "40");
    expect(screen.getByText(/Last call/i)).toBeInTheDocument();
    expect(screen.getByText(/Total: 160/i)).toBeInTheDocument();
  });

  it("shows an auto-compaction notice in the transcript", () => {
    threadsFixture = [
      {
        thread_id: "thread-compacted",
        title: "Compacted",
        status: "completed",
        updated_at: "",
        last_user_message_preview: "summarized",
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ];
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-compacted" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    expect(screen.getAllByText(/context auto-compacted/i).length).toBeGreaterThan(0);
  });

  it("persists composer selections before editing and resending the latest user message", async () => {
    runStreamFixture = {
      ...runStreamFixture,
      editLatestAndResend: editLatestAndResendMock,
    };
    editLatestAndResendMock.mockClear();
    updateThreadSettingsMock.mockClear();

    renderShell("thread-a");

    await selectModel(/^minimax$/i);
    await selectExecutionMode(/agent|代理/i);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /edit message/i }));
    });
    const editTextareas = screen.getAllByDisplayValue("Need approval");
    fireEvent.change(editTextareas[0]!, {
      target: { value: "Edited request" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /resend/i }));
    });

    expect(updateThreadSettingsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          execution_mode: "agent",
          selected_model: "minimax",
        }),
        threadIdOverride: "thread-a",
      }),
    );
    expect(editLatestAndResendMock).toHaveBeenCalledWith(
      "message-0",
      expect.objectContaining({
        message: "Edited request",
        execution_mode: "agent",
        selected_model: "minimax",
      }),
      "thread-a",
    );
  });

  it("shows existing latest-message attachments while editing without adding attachment controls", async () => {
    const artifactRef = {
      kind: "upload",
      label: "mockup.png",
      artifact_url: "/threads/thread-a/artifacts/uploads/mockup.png",
      virtual_path: "/mnt/user-data/uploads/mockup.png",
      source_scope: null,
      internal: false,
      extension: "png",
      markdown_file: null,
      markdown_virtual_path: null,
      markdown_artifact_url: null,
      companions: [],
      extraction: null,
      outline: [],
      outline_preview: [],
      converter_used: null,
      ocr_used: false,
      conversion_error: null,
    };
    render(
      <I18nProvider>
        <UserStepMessage
          message={buildOptimisticUserMessage("Need approval", [artifactRef])}
          canEdit
          isEditing
          editDraft="Need approval"
          onEditDraftChange={() => {}}
          onCopy={() => {}}
          onStartEdit={() => {}}
          onCancelEdit={() => {}}
          onSubmitEdit={() => {}}
          editor={
            <div className="w-[min(44rem,100%)] rounded-[1.5rem] bg-[var(--panel-muted)] p-4 text-left">
              <ArtifactRefList artifactRefs={[artifactRef]} className="mb-3 mt-0" />
              <textarea value="Need approval" readOnly />
              <button type="button">Cancel</button>
              <button type="button">Resend</button>
            </div>
          }
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("button", { name: /preview mockup\.png/i })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Need approval")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remove mockup\.png/i })).not.toBeInTheDocument();
  });

  it("renders the Memory Workspace with session, user, workspace tabs and advanced controls", async () => {
    renderShell("thread-a");

    await openUtilitiesDrawer();
    fireEvent.click(screen.getAllByRole("button", { name: /memory/i })[0]!);

    expect(screen.getByText(/memory workspace/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /session memory/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /HCMS User Layer/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /HCMS Workspace Layer/i })).toBeInTheDocument();
    expect(screen.getByText(/focused summary: northstar/i)).toBeInTheDocument();
    expect(screen.getByText(/session_search summarize evidence/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /HCMS Workspace Layer/i }));
    expect(screen.getByText(/store usage/i)).toBeInTheDocument();
    expect(screen.getByText(/80 \/ 700/i)).toBeInTheDocument();
    expect(screen.getByText(/injection budget 350 tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/snapshot payload 88 tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/HCMS Signals/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Active Forgetting/i).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /session memory/i }));
    expect(screen.getByText(/recall inspector/i)).toBeInTheDocument();
    expect(screen.getByText(/hcms recall/i)).toBeInTheDocument();
    expect(screen.getByText(/northstar rollout context/i)).toBeInTheDocument();
    expect(screen.getByText(/causal path/i)).toBeInTheDocument();
    expect(screen.getByText(/user stated northstar is active/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /HCMS Workspace Layer/i }));
    fireEvent.click(screen.getByRole("button", { name: /^hcms$/i }));
    expect(screen.getByText(/hcms history/i)).toBeInTheDocument();
    expect(screen.getByText(/hcms diff/i)).toBeInTheDocument();
    expect(screen.getByText(/v1 → v2/i)).toBeInTheDocument();
    expect(screen.getByText(/confidence \+0\.16/i)).toBeInTheDocument();
    expect(screen.getByText(/evidence \+1 \/ -1/i)).toBeInTheDocument();
    expect(screen.getByText(/HCMS Control Plane/i)).toBeInTheDocument();
  });

  it("runs Session Search from the semantic session memory panel", async () => {
    renderShell("thread-a");

    await openUtilitiesDrawer();
    fireEvent.click(screen.getAllByRole("button", { name: /memory/i })[0]!);
    fireEvent.click(screen.getByRole("button", { name: /session memory/i }));
    fireEvent.change(screen.getByPlaceholderText(/search prior sessions/i), {
      target: { value: "Northstar" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /session search/i }));
    });

    expect(searchSessionsMock).toHaveBeenCalledWith({ query: "Northstar", threadId: "thread-a", limit: 6 });
  });

  it("switches mode, sends run requests, and still supports drawer actions", async () => {
    renderShell("thread-a");

    await selectExecutionMode(/agent|代理/i);
    fireEvent.change(screen.getByPlaceholderText(/describe the next task/i), {
      target: { value: "Refine the rollout plan" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(send|发送)$/i }));
    });
    expect(startRunMock).toHaveBeenCalledWith(
      expect.objectContaining({
        message: "Refine the rollout plan",
        execution_mode: "agent",
      }),
      "thread-a",
    );
    expect(screen.getByRole("textbox", { name: /composer|输入区/i })).toHaveValue("");
    expect(screen.getByText("Refine the rollout plan")).toBeInTheDocument();

    await openUtilitiesDrawer();
    fireEvent.click(screen.getAllByRole("button", { name: /approvals/i })[0]!);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^yes$/i }));
    });
    expect(resumeApprovalMock).toHaveBeenCalledWith(
      expect.objectContaining({ approval_context: "approved for this turn" }),
      "thread-a",
    );

    fireEvent.click(screen.getAllByRole("button", { name: /forge/i })[0]!);
    expect(screen.getByTestId("thread-card-thread-b")).toHaveClass("bg-[color-mix(in_srgb,var(--ink)_9%,var(--panel)_91%)]");

    fireEvent.click(screen.getAllByRole("button", { name: /memory/i })[0]!);
    expect(screen.getByText(/HCMS Control Plane/i)).toBeInTheDocument();
    expect(screen.getByText(/HCMS Store Health/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /HCMS Workspace Layer/i }));
    fireEvent.change(screen.getByPlaceholderText(/entry category/i), {
      target: { value: "preference" },
    });
    fireEvent.change(screen.getByPlaceholderText(/write a durable memory note/i), {
      target: { value: "Prefer terse release notes." },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create entry/i }));
    });
    expect(createMemoryLayerEntryMock).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /session memory/i }));
    fireEvent.change(screen.getByPlaceholderText(/search prior sessions/i), {
      target: { value: "Northstar" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /session search/i }));
    });
    expect(searchSessionsMock).toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /run project recap/i }));
    });
    expect(runReflectionJobMock).toHaveBeenCalledWith("system-project-recap");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
      fireEvent.click(screen.getByRole("button", { name: /^resume$/i }));
      fireEvent.click(screen.getAllByRole("button", { name: /^remove$/i })[0]!);
    });
    expect(pauseReflectionJobMock).toHaveBeenCalledWith("system-project-recap");
    expect(resumeReflectionJobMock).toHaveBeenCalledWith("system-project-recap");
    expect(removeReflectionJobMock).toHaveBeenCalledWith("system-project-recap");
  });

  it("toggles theme both directions from the top bar button", async () => {
    renderShell("thread-a");

    const toggle = screen.getByRole("button", { name: /toggle theme/i });
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(document.documentElement.className).toContain("control-dark");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /toggle theme/i }));
    });
    expect(document.documentElement.className).toContain("forge-light");
  });

  it("uses automatic locale copy without a manual top-bar locale switch", () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    expect(screen.getByText("Anvil")).toBeInTheDocument();
    expect(screen.getByText(/what are you working on/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "中文" })).not.toBeInTheDocument();
  });

  it("supports deleting a thread from the rail", async () => {
    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /thread actions/i })[0]!);
    });
    const deleteButton = screen.getByRole("button", { name: /(delete|删除) northstar/i });
    await act(async () => {
      fireEvent.pointerDown(deleteButton);
      fireEvent.click(deleteButton);
    });

    expect(deleteThreadMock).toHaveBeenCalledWith("thread-a");
  });

  it("renders user messages as right-side muted bubbles with hover-only actions", () => {
    const message: MessageView = {
      message_id: "user-message-1",
      role: "user",
      content: "关于 LangGraph 的 Checkpointer 怎么持久化？",
      steps: [],
      content_blocks: [],
      reasoning: null,
      tool_calls: [],
      tool_call_id: null,
      name: null,
      status: null,
      artifact_refs: [],
      approval: null,
    };

    render(
      <UserStepMessage
        message={message}
        canEdit
        isEditing={false}
        editDraft=""
        onEditDraftChange={vi.fn()}
        onCopy={vi.fn()}
        onStartEdit={vi.fn()}
        onCancelEdit={vi.fn()}
        onSubmitEdit={vi.fn()}
        editor={null}
      />,
    );

    const content = screen.getByText(/Checkpointer/).closest(".workspace-rich-content");
    expect(content).toHaveClass("text-left");
    expect(content?.parentElement).toHaveClass("bg-[var(--panel-muted)]");
    const copyButton = screen.getByRole("button", { name: /copy message/i });
    expect(copyButton.parentElement).toHaveClass("opacity-0", "group-hover/user-message:opacity-100");
  });

  it("uses a draft session for new thread until the first send", async () => {
    createThreadMock.mockClear();
    startRunMock.mockClear();

    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new chat|新会话/i }));
    });
    expect(createThreadMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("textbox", { name: /composer|输入区/i }), {
      target: { value: "Draft thread start" },
    });

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /forge/i })[0]!);
    });
    expect(screen.getByRole("textbox", { name: /composer|输入区/i })).toHaveValue("");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new chat|新会话/i }));
    });
    expect(screen.getByRole("textbox", { name: /composer|输入区/i })).toHaveValue("Draft thread start");
    expect(createThreadMock).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(send|发送)$/i }));
    });
    expect(createThreadMock).toHaveBeenCalledTimes(1);
    expect(startRunMock).toHaveBeenCalledWith(
      expect.objectContaining({
        message: "Draft thread start",
      }),
      "thread-b",
    );
  });

  it("caches unsent composer drafts per thread", async () => {
    renderShell("thread-a");

    const composer = screen.getByRole("textbox", { name: /composer|输入区/i });
    fireEvent.change(composer, {
      target: { value: "Draft for Northstar" },
    });

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /forge/i })[0]!);
    });
    expect(screen.getByRole("textbox", { name: /composer|输入区/i })).toHaveValue("");

    fireEvent.change(screen.getByRole("textbox", { name: /composer|输入区/i }), {
      target: { value: "Draft for Forge" },
    });

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /northstar/i })[0]!);
    });
    expect(screen.getByRole("textbox", { name: /composer|输入区/i })).toHaveValue("Draft for Northstar");

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /forge/i })[0]!);
    });
    expect(screen.getByRole("textbox", { name: /composer|输入区/i })).toHaveValue("Draft for Forge");
  });

  it("uploads attachments on send and persists composer model choice", async () => {
    startRunMock.mockClear();
    updateThreadSettingsMock.mockClear();
    uploadFilesMock.mockClear();
    const { container } = renderShell("thread-a");
    await openComposerTools();

    const attachInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["pdf"], "brief.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(attachInput, { target: { files: [file] } });
    });

    await selectModel(/^minimax$/i);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(send|发送)$/i }));
    });

    expect(uploadFilesMock).toHaveBeenCalledWith({
      files: [file],
      threadIdOverride: "thread-a",
    });
    expect(startRunMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        message: expect.stringMatching(/Please process the files I just uploaded\.|请处理我刚上传的附件。/),
        selected_model: "minimax",
        uploaded_filenames: ["brief.pdf"],
      }),
      "thread-a",
    );
    expect(updateThreadSettingsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          selected_model: "minimax",
        }),
        threadIdOverride: "thread-a",
      }),
    );
  });

  it("surfaces plan mode controls and sends is_plan_mode through run and settings updates", async () => {
    startRunMock.mockClear();
    updateThreadSettingsMock.mockClear();

    renderShell("thread-a");

    await selectExecutionMode(/agent|代理/i);
    await openComposerTools();
    expect(screen.getByRole("button", { name: /plan mode|计划模式/i })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /plan mode|计划模式/i }));
    });

    fireEvent.change(screen.getByRole("textbox", { name: /composer|输入区/i }), {
      target: { value: "Plan this rollout" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^(send|发送)$/i }));
    });

    expect(startRunMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        message: "Plan this rollout",
        is_plan_mode: true,
      }),
      "thread-a",
    );
    expect(updateThreadSettingsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          is_plan_mode: true,
        }),
      }),
    );
  });

  it("hides plan mode while chat execution mode is selected", async () => {
    renderShell("thread-a");

    await openComposerTools();
    expect(screen.queryByRole("button", { name: /plan mode|计划模式/i })).not.toBeInTheDocument();

    await openComposerTools();
    await selectExecutionMode(/agent|代理/i);
    await openComposerTools();
    expect(screen.getByRole("button", { name: /plan mode|计划模式/i })).toBeInTheDocument();

    await openComposerTools();
    await selectExecutionMode(/chat|聊天/i);
    await openComposerTools();
    expect(screen.queryByRole("button", { name: /plan mode|计划模式/i })).not.toBeInTheDocument();
  });

  it("prefers thread titles in the rail and avoids rendering duplicate raw tool-result transcript bubbles", () => {
    renderShell("thread-a");

    expect(screen.getAllByText("Northstar").length).toBeGreaterThan(0);
    const folded = screen.getByRole("button", { name: /已调用 1 个工具|called 1 tool|已调用 1 个工具/i });
    expect(folded).toBeInTheDocument();
    fireEvent.click(folded);
    expect(screen.getByRole("button", { name: /List Directory|已运行 List Directory/i })).toBeInTheDocument();
    expect(screen.queryByText('["src","tests"]')).not.toBeInTheDocument();
  });

  it("keeps long thread rail labels horizontally clipped inside the fixed rail", () => {
    const longText = "VeryLongUnbrokenThreadMessage".repeat(12);
    threadsFixture = [
      {
        thread_id: "thread-a",
        title: longText,
        status: "running",
        updated_at: "",
        last_user_message_preview: longText,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ];

    render(
      <ThemeProvider>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-a" />
          </TooltipProvider>
        </I18nProvider>
      </ThemeProvider>,
    );

    expect(screen.getByTestId("thread-rail")).toHaveClass("max-w-[272px]");
    expect(screen.getByTestId("thread-rail-scroll")).toHaveClass("overflow-x-hidden");
    expect(screen.getByTestId("thread-card-thread-a")).toHaveClass("min-w-0");
    expect(screen.getByTestId("thread-card-primary-thread-a")).toHaveClass("truncate");
    expect(screen.getByTestId("thread-card-primary-thread-a")).not.toHaveAttribute("title");
    expect(screen.queryByRole("tooltip", { name: longText })).not.toBeInTheDocument();
  });

  it("hides thread rows and brand content in the collapsed rail", async () => {
    renderShell("thread-a");

    expect(screen.getByTestId("thread-rail")).toHaveClass("max-w-[272px]");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /collapse sidebar/i }));
    });

    expect(screen.getByTestId("thread-rail")).toHaveClass("max-w-14");
    expect(screen.queryByTestId("thread-card-thread-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("thread-card-primary-thread-a")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand sidebar/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new chat|新会话/i })).toBeInTheDocument();
  });

  it("dismisses rail and composer popovers when clicking outside", async () => {
    renderShell("thread-a");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^more$/i }));
    });
    expect(screen.getByRole("button", { name: /configuration center/i })).toBeInTheDocument();
    await act(async () => {
      fireEvent.pointerDown(document.body);
    });
    expect(screen.queryByRole("button", { name: /configuration center/i })).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /thread actions/i })[0]!);
    });
    expect(screen.getByRole("button", { name: /^rename$/i })).toBeInTheDocument();
    await act(async () => {
      fireEvent.pointerDown(document.body);
    });
    expect(screen.queryByRole("button", { name: /^rename$/i })).not.toBeInTheDocument();

    await openComposerTools();
    expect(screen.getByRole("button", { name: /attach|附件/i })).toBeInTheDocument();
    await act(async () => {
      fireEvent.pointerDown(document.body);
    });
    expect(screen.queryByRole("button", { name: /attach|附件/i })).not.toBeInTheDocument();
  });

  it("runs thread action menu commands on pointer down before outside dismissal can close the menu", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("Renamed Northstar");
    renderShell("thread-a");

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /thread actions/i })[0]!);
    });
    await act(async () => {
      fireEvent.pointerDown(screen.getByRole("button", { name: /^rename$/i }));
    });
    expect(screen.getByText("Renamed Northstar")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /thread actions/i })[0]!);
    });
    await act(async () => {
      fireEvent.pointerDown(screen.getByRole("button", { name: /^pin$/i }));
    });
    expect(JSON.parse(window.localStorage.getItem("forge.chat.pinned-thread-ids") ?? "[]")).toContain("thread-a");

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /thread actions/i })[0]!);
    });
    await act(async () => {
      fireEvent.pointerDown(screen.getByRole("button", { name: /delete renamed northstar/i }));
    });
    expect(deleteThreadMock).toHaveBeenCalledWith("thread-a");

    promptSpy.mockRestore();
  });

  it("renders queued follow-ups as a bounded review panel", () => {
    const longMessage = "Use skills to generate a presentation in the download folder and keep this queue preview short.".repeat(3);
    runStreamFixture = {
      ...runStreamFixture,
      threadStatePatch: {
        status: "running",
        has_pending_approval: false,
        pending_approval_reason: null,
        active_followup_dispatch: null,
        queued_followups: [
          queuedFollowupFixture({ queue_id: "queue-1", message: longMessage }),
          queuedFollowupFixture({ queue_id: "queue-2", message: "Second queued turn" }),
        ],
      },
    };

    renderShell("thread-b");

    const stack = screen.getByTestId("queued-followup-stack");
    expect(stack).toHaveClass("max-w-[900px]");
    expect(stack).toHaveClass("rounded-[0.95rem]");
    expect(stack).toHaveTextContent("Use skills to generate a presentation");
    expect(within(stack).getByText("Second queued turn")).toBeInTheDocument();
    expect(within(stack).getByTestId("queued-followup-list")).toHaveClass("max-h-44");
    expect(within(stack).getAllByRole("button", { name: /guide/i })).toHaveLength(2);
    expect(within(stack).getAllByRole("button", { name: /more queued actions/i })).toHaveLength(2);
  });

  it("renders auto-dispatched queued follow-ups as optimistic user messages", async () => {
    const queued = queuedFollowupFixture({ queue_id: "queue-auto", message: "Please continue with the queued task" });
    let resolveRun: (result: { status: "completed"; error: null }) => void = () => {};
    const runPromise = new Promise<{ status: "completed"; error: null }>((resolve) => {
      resolveRun = resolve;
    });
    const startQueuedFollowupMock = vi.fn().mockReturnValue(runPromise);
    runStreamFixture = {
      ...runStreamFixture,
      start: startQueuedFollowupMock,
      threadStatePatch: {
        status: "completed",
        has_pending_approval: false,
        pending_approval_reason: null,
        active_followup_dispatch: null,
        queued_followups: [queued],
      },
    };
    popNextFollowupMock.mockImplementationOnce(async () => {
      runStreamFixture = {
        ...runStreamFixture,
        threadStatePatch: {
          ...runStreamFixture.threadStatePatch,
          queued_followups: [],
        },
      };
      return queued;
    });

    renderShell("thread-b");

    await waitFor(() => {
      expect(popNextFollowupMock).toHaveBeenCalledWith("thread-b");
    });
    await waitFor(() => {
      expect(startQueuedFollowupMock).toHaveBeenCalledWith(
        expect.objectContaining({
          client_message_id: "queued:queue-auto",
          message: "Please continue with the queued task",
        }),
        "thread-b",
      );
    });
    await act(async () => {
      resolveRun({ status: "completed", error: null });
      await runPromise;
    });
  });

  it("releases the composer running state when the durable thread is terminal after the streamed message completed", () => {
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: true,
        isSubmittingRun: false,
        optimisticRunningThreadId: "thread-a",
        durableThreadStatus: "completed",
        streamTerminalSeen: false,
        streamMessageCompletedSeen: true,
      }),
    ).toBe(false);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: true,
        isSubmittingRun: false,
        optimisticRunningThreadId: "thread-a",
        durableThreadStatus: "completed",
        streamTerminalSeen: false,
        streamMessageCompletedSeen: false,
      }),
    ).toBe(true);
  });
});
