import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/src/components/ui/tooltip";
import { I18nProvider } from "@/src/core/i18n";

const createThreadMock = vi.fn().mockResolvedValue({ thread_id: "thread-b" });

vi.mock("@/src/core/threads/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/src/core/threads/hooks")>("@/src/core/threads/hooks");
  return {
    ...actual,
    useThreads: () => ({
      data: [
        {
          thread_id: "thread-a",
          title: "Northstar",
          status: "completed",
          updated_at: "",
          last_user_message_preview: "hello",
          has_pending_approval: false,
          has_active_subagent_tasks: false,
        },
      ],
    }),
    useCreateThread: () => ({ mutateAsync: createThreadMock }),
    useDeleteThread: () => ({ mutateAsync: vi.fn() }),
    useThreadState: () => ({
      data: {
        thread_id: "thread-a",
        status: "completed",
        title: "Northstar",
        summary: "Thread summary",
        active_model: "openai_compatible",
        reasoning_effort: "xhigh",
        visible_tool_names: ["write_file"],
        deferred_tool_names: [],
        enabled_skill_ids: ["minimal-operator-skill"],
        memory_namespace: "global/default",
        injected_memory_snapshot_id: "snapshot-1",
        has_pending_approval: false,
        pending_approval_reason: null,
        output_artifacts: [],
        uploaded_files: [],
        presented_artifacts: [],
        active_subagent_task_ids: [],
        subagent_tasks: [],
        process_sessions: [],
        last_error: null,
      },
    }),
    useThreadDetail: () => ({
      data: {
        thread: {
          thread_id: "thread-a",
          title: "Northstar",
          status: "completed",
          updated_at: "",
          last_user_message_preview: "hello",
          has_pending_approval: false,
          has_active_subagent_tasks: false,
        },
        state: {
          thread_id: "thread-a",
          status: "completed",
          title: "Northstar",
          summary: "Thread summary",
          selected_model: "openai_compatible",
          selected_profile: "default",
          selected_reasoning_effort: "high",
          effective_model: "openai_compatible",
          active_model: "openai_compatible",
          reasoning_effort: "xhigh",
          execution_mode: "chat",
          token_usage: { input_tokens: 12, output_tokens: 6, total_tokens: 18 },
          approval_policy_summary: "Chat mode disables tool execution and keeps the session conversational.",
          allowed_local_actions: ["conversation"],
          requires_approval_actions: [],
          restricted_actions: ["tool_execution"],
          visible_tool_names: ["write_file"],
          deferred_tool_names: [],
          enabled_skill_ids: ["minimal-operator-skill"],
          memory_namespace: "global/default",
          injected_memory_snapshot_id: "snapshot-1",
          has_pending_approval: false,
          pending_approval_reason: null,
          output_artifacts: [],
          uploaded_files: [],
          presented_artifacts: [],
          active_subagent_task_ids: [],
          subagent_tasks: [],
          process_sessions: [],
          recent_tool_activity: [],
          recent_approval_events: [],
          last_error: null,
        },
        messages: [
          {
            message_id: "message-0",
            role: "human",
            content: "Hello, Anvil.",
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
            message_id: "message-tool-plan",
            role: "ai",
            content: "I need to inspect the tool catalog before answering.",
            steps: [
              {
                step_id: "message-tool-plan:thinking",
                message_id: "message-tool-plan",
                type: "thinking",
                title: "已思考 1 秒",
                status: "success",
                duration: "1s",
                duration_ms: 1000,
                payload: "I need to inspect the tool catalog before answering.",
                language: "text",
                order: 0,
              },
              {
                step_id: "message-tool-plan:call",
                message_id: "message-tool-plan",
                type: "call",
                title: "已检索工具能力",
                status: "success",
                duration: "<1s",
                duration_ms: 120,
                payload: "{\"items\":[]}",
                language: "json",
                tool_name: "tool_catalog",
                tool_call_id: "call-tool-catalog",
                order: 1,
              },
            ],
            content_blocks: [
              { type: "thinking", text: "I need to inspect the tool catalog before answering." },
            ],
            reasoning: {
              text: "I need to inspect the tool catalog before answering.",
              block_count: 1,
              duration_ms: 1000,
            },
            tool_calls: [
              {
                tool_call_id: "call-tool-catalog",
                name: "tool_catalog",
                display_name: "Tool Catalog",
                source_kind: "builtin",
                source_id: null,
                capability_group: "capability_discovery",
                tool_execution_mode: "safe",
                args: {},
                status: "completed",
                result_text: "{\"items\":[]}",
                started_at: null,
                completed_at: null,
                duration_ms: 120,
              },
            ],
            tool_call_id: null,
            name: null,
            status: null,
            artifact_refs: [],
            approval: null,
          },
          {
            message_id: "message-1",
            role: "ai",
            content:
              "I can help with that.\n\n```python\nprint('hello')\n```\n\n$$E=mc^2$$\n\n```mermaid\ngraph TD;\nA-->B;\n```",
            steps: [
              {
                step_id: "message-1:thinking",
                message_id: "message-1",
                type: "thinking",
                title: "已思考 3 秒",
                status: "success",
                duration: "3s",
                duration_ms: 3200,
                payload: "Checking thread state.",
                language: "text",
                order: 2,
              },
              {
                step_id: "message-1:content",
                message_id: "message-1",
                type: "content",
                title: "最终回答",
                status: "success",
                payload:
                  "I can help with that.\n\n```python\nprint('hello')\n```\n\n$$E=mc^2$$\n\n```mermaid\ngraph TD;\nA-->B;\n```",
                language: "markdown",
                order: 3,
              },
            ],
            content_blocks: [
              { type: "thinking", text: "Checking thread state." },
              {
                type: "text",
                text: "I can help with that.\n\n```python\nprint('hello')\n```\n\n$$E=mc^2$$\n\n```mermaid\ngraph TD;\nA-->B;\n```",
              },
            ],
            reasoning: {
              text: "Checking thread state.",
              block_count: 1,
              duration_ms: 3200,
            },
            tool_calls: [],
            tool_call_id: null,
            name: null,
            status: null,
            artifact_refs: [],
            approval: null,
          },
        ],
        pending_approval: null,
        stream_capabilities: {
          supports_step_chain: true,
          supports_message_delta: false,
          supports_reasoning_delta: false,
          supports_structured_events: true,
        },
      },
    }),
    useThreadSettings: () => ({
      data: {
        thread_id: "thread-a",
        workspace_root: null,
        anvil_home: "C:/Users/test/.anvil",
        anvil_profile: "default",
        anvil_profile_home: "C:/Users/test/.anvil",
        workspace_mode: "thread",
        resolved_workspace_path: "E:/python/python学习/harness/Anvil/.anvil/workspace/thread-a",
      },
    }),
    useThreadRunStream: () => ({
      events: [],
      error: null,
      isStreaming: false,
      start: vi.fn(),
      resumeApproval: vi.fn(),
      stop: vi.fn(),
    }),
    useCancelThreadApproval: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useUpdateThreadSettings: () => ({ mutateAsync: vi.fn() }),
    useWaitSubagentTask: () => ({ mutateAsync: vi.fn() }),
    useCancelSubagentTask: () => ({ mutateAsync: vi.fn() }),
    useWaitProcessSession: () => ({ mutateAsync: vi.fn() }),
    useKillProcessSession: () => ({ mutateAsync: vi.fn() }),
    useWriteProcessStdin: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useCloseProcessStdin: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useInterruptProcessSession: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useResizeProcessSession: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useProcessLog: () => ({ data: null }),
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
    useScheduledTasks: () => ({ data: { items: [], executions: [] }, isFetching: false }),
    useRunScheduledTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
    usePauseScheduledTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useResumeScheduledTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

vi.mock("@/src/core/uploads/hooks", () => ({
  useUploads: () => ({ data: { thread_id: "thread-a", files: [] } }),
  useUploadFiles: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("@/src/core/models/hooks", () => ({
  useModels: () => ({ data: [{ name: "openai_compatible" }] }),
  useUpdateModelSelection: () => ({ mutate: vi.fn(), isPending: false, variables: null }),
}));

vi.mock("@/src/core/config/hooks", () => ({
  useConfigOverview: () => ({
    data: {
      status: "ok",
      config_fingerprint: "cfg-current",
      models: { total: 1, available: 1, source_counts: {}, enabled_source_counts: {} },
      tools: { total: 1, enabled: 1, ready: 1, source_counts: {}, enabled_source_counts: {} },
      skills: { total: 1, enabled: 1, source_counts: { repo: 1 }, enabled_source_counts: { repo: 1 } },
      memory: { total: 1, enabled: 1, quality_score: 1, source_counts: {}, enabled_source_counts: {} },
      mcp: { total: 0, enabled: 0, ready: 0, source_counts: {}, enabled_source_counts: {} },
      plugins: { total: 0, enabled: 0, source_counts: {}, enabled_source_counts: {} },
      scheduled: { total: 0, enabled: 0, source_counts: {}, enabled_source_counts: {} },
    },
    isLoading: false,
    isFetching: false,
    isError: false,
  }),
}));

vi.mock("@/src/core/skills/hooks", () => ({
  useSkills: () => ({ data: [{ skill_id: "minimal-operator-skill", title: "Minimal Operator Skill", summary: "demo", allowed_tools: ["read_file"], tags: ["ops"], enabled: true, path: "/skills/minimal" }] }),
  useSkill: () => ({ data: null }),
  useSkillProcedures: () => ({ data: { items: [], counts: { total: 0, returned: 0, promotable: 0, promoted: 0 } }, refetch: vi.fn(), isFetching: false }),
  usePromoteSkillProcedure: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRejectSkillProcedure: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useManageSkill: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReloadSkills: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/src/core/catalog/hooks", () => ({
  useToolCatalog: () => ({ data: [] }),
  useCatalogTools: () => ({ data: [] }),
  useToolCatalogEntry: () => ({ data: null }),
}));

vi.mock("@/src/core/plugins/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/src/core/plugins/hooks")>("@/src/core/plugins/hooks");
  return {
    ...actual,
    usePlugins: () => ({ data: [] }),
    useInstallPlugin: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useUpsertPluginRegistry: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRefreshPluginRegistry: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeletePluginRegistry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

vi.mock("@/src/core/mcp/hooks", () => ({
  useMcpConfigOverview: () => ({ data: null }),
  useMcpServers: () => ({ data: [] }),
  useMcpServerTools: () => ({ data: null }),
  useMcpServerProvenance: () => ({ data: null }),
  useMcpResources: () => ({ data: [] }),
  useMcpResource: () => ({ data: null }),
  useMcpPrompts: () => ({ data: [] }),
  useRenderMcpPrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRefreshMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReconnectMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpsertMcpServers: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/src/core/memory/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/src/core/memory/hooks")>("@/src/core/memory/hooks");
  return {
    ...actual,
    useMemoryLayers: () => ({
      data: [
        { layer_id: "session", display_name: "Session Memory", description: "Current thread history.", writable: false, entry_count: 0, store_id: null, summary: "session" },
        { layer_id: "workspace", display_name: "Workspace Memory", description: "Global work context.", writable: true, entry_count: 0, store_id: "runtime_memory", summary: "workspace" },
      ],
    }),
    useMemoryOverview: () => ({ data: { active_provider_id: "factgraph_provider", store_count: 1, archive_turn_count: 0, reflection_job_count: 0, stores: [] }, refetch: vi.fn(), isFetching: false }),
    useMemoryLayerEntries: () => ({ data: [], refetch: vi.fn(), isFetching: false }),
    useMemoryProviders: () => ({ data: [], refetch: vi.fn(), isFetching: false }),
    useMemoryConflicts: () => ({ data: [], refetch: vi.fn(), isFetching: false }),
    useMemoryStaleness: () => ({ data: [], refetch: vi.fn(), isFetching: false }),
    useMemoryReview: () => ({ data: [], refetch: vi.fn(), isFetching: false }),
    useMemoryHealth: () => ({ data: null, refetch: vi.fn(), isFetching: false }),
    useMemoryAdminAudit: () => ({ data: [] }),
    useBatchMemoryReview: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useFlushMemory: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRunMemoryBenchmark: () => ({ mutateAsync: vi.fn(), data: null, isPending: false }),
    useApproveMemoryReview: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRejectMemoryReview: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useResolveMemoryConflict: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useMemoryTrace: () => ({ data: { items: [] }, mutateAsync: vi.fn() }),
    useSessionMemory: () => ({ data: null }),
    useSessionSearch: () => ({ data: null, mutateAsync: vi.fn() }),
    useActivateMemoryProvider: () => ({ mutateAsync: vi.fn() }),
    useReloadMemoryProviders: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useTestMemoryProvider: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useExportMemoryAdmin: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useImportMemoryAdmin: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useCreateMemoryLayerEntry: () => ({ mutateAsync: vi.fn() }),
    useUpdateMemoryLayerEntry: () => ({ mutateAsync: vi.fn() }),
    useDeleteMemoryLayerEntry: () => ({ mutateAsync: vi.fn() }),
    useReflectionJobs: () => ({ data: [] }),
    useRunReflectionJob: () => ({ mutateAsync: vi.fn() }),
    usePauseReflectionJob: () => ({ mutateAsync: vi.fn() }),
    useResumeReflectionJob: () => ({ mutateAsync: vi.fn() }),
    useRemoveReflectionJob: () => ({ mutateAsync: vi.fn() }),
  };
});

vi.mock("@/src/core/extensions/hooks", () => ({
  useExtensions: () => ({ data: [] }),
}));

vi.mock("@/src/core/system/hooks", () => ({
  useGatewayHealth: () => ({ data: { status: "ok", phase: "phase8" } }),
}));

import { WorkspaceShell } from "./workspace/workspace-shell";

describe("WorkspaceShell thread stage", () => {
  it("renders transcript reasoning, code, math, and mermaid blocks", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          <TooltipProvider>
            <WorkspaceShell initialThreadId="thread-a" />
          </TooltipProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText("Hello, Anvil.")).toBeInTheDocument();
    expect(screen.getByText(/I can help with that\./i)).toBeInTheDocument();
    expect(screen.getByText(/I need to inspect the tool catalog before answering/i)).toBeInTheDocument();
    expect(screen.queryByText("Tool Catalog")).not.toBeInTheDocument();
    expect(screen.getByText(/Checking thread state./i)).toBeInTheDocument();
    expect(screen.getAllByText(/Checking thread state./i).length).toBeGreaterThan(0);
    expect(screen.getByText(/python/i)).toBeInTheDocument();
    expect(screen.getByText(/copy code/i)).toBeInTheDocument();
    expect(screen.getByText(/E=mc\^2/i)).toBeTruthy();
    expect(screen.getByText(/mermaid diagram/i)).toBeInTheDocument();
  });
});
