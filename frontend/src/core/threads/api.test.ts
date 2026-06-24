import { beforeEach, describe, expect, it, vi } from "vitest";

import { resolveGatewayUrl } from "@/src/core/api/client";

import { approveThread, cancelApproval, createThread, deleteThread, deleteThreadFollowup, enqueueThreadFollowup, getThreadDetail, getThreadEvaluationReport, getThreadSettings, getThreadState, interruptThreadRun, listThreadRunEvents, listThreads, popNextThreadFollowup, runThread, streamThreadApprovalWithSignal, streamThreadRun, streamThreadRunWithSignal, streamThreadUserInteractionWithSignal, updateThreadFollowup, updateThreadSettings } from "./api";

describe("threads api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("lists threads through the gateway wrapper", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([{ thread_id: "thread-a", title: null, status: "ready", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await listThreads();
    expect(result[0].thread_id).toBe("thread-a");
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18000/threads", expect.any(Object));
  });

  it("creates threads with post translation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ thread_id: "thread-b", title: null, status: "ready", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await createThread("thread-b");
    expect(result.thread_id).toBe("thread-b");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("creates threads with workspace root override", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ thread_id: "thread-c", title: null, status: "ready", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await createThread("thread-c", "E:\\projects\\northstar");
    expect(result.thread_id).toBe("thread-c");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          thread_id: "thread-c",
          workspace_root: "E:\\projects\\northstar",
        }),
      }),
    );
  });

  it("requests event-log projected thread state when asked", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        thread_id: "thread-a",
        run_id: "run-1",
        status: "running",
        title: null,
        summary: null,
        active_model: null,
        reasoning_effort: null,
        visible_tool_names: [],
        deferred_tool_names: [],
        enabled_skill_ids: [],
        memory_namespace: null,
        injected_memory_snapshot_id: null,
        has_pending_approval: false,
        pending_approval_reason: null,
        output_artifacts: [],
        uploaded_files: [],
        presented_artifacts: [],
        active_subagent_task_ids: [],
        last_error: null,
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await getThreadState("thread-a", {
      stateSource: "event_log",
      runId: "run-1",
      stateScope: "chat",
    });

    expect(result.run_id).toBe("run-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/state?state_scope=chat&state_source=event_log&run_id=run-1",
      expect.any(Object),
    );
  });

  it("requests thread-scoped event-log projected state without run id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        thread_id: "thread-a",
        status: "completed",
        title: null,
        summary: null,
        active_model: null,
        reasoning_effort: null,
        visible_tool_names: [],
        deferred_tool_names: [],
        enabled_skill_ids: [],
        memory_namespace: null,
        injected_memory_snapshot_id: null,
        has_pending_approval: false,
        pending_approval_reason: null,
        output_artifacts: [],
        uploaded_files: [],
        presented_artifacts: [],
        active_subagent_task_ids: [],
        last_error: null,
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await getThreadState("thread-a", {
      stateSource: "event_log",
      stateScope: "chat",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/state?state_scope=chat&state_source=event_log",
      expect.any(Object),
    );
  });

  it("resumes approvals through the approval endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ thread_id: "thread-a", status: "completed", assistant_message: "done", last_error: null, thread: { thread_id: "thread-a", title: null, status: "completed", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false }, state: { thread_id: "thread-a", status: "completed", title: null, summary: null, active_model: "openai", reasoning_effort: null, visible_tool_names: [], deferred_tool_names: [], enabled_skill_ids: [], memory_namespace: null, injected_memory_snapshot_id: null, has_pending_approval: false, pending_approval_reason: null, output_artifacts: [], uploaded_files: [], presented_artifacts: [], active_subagent_task_ids: [], last_error: null } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await approveThread("thread-a");
    expect(result.status).toBe("completed");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/approvals/approve",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("streams approval resumes through the dedicated event-stream endpoint", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: approval_resolved\ndata: {"thread_id":"thread-a","request_id":"approval:write_file"}\n\n' +
              'event: run_completed\ndata: {"thread_id":"thread-a","status":"completed"}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const events = [];
    for await (const event of streamThreadApprovalWithSignal("thread-a", { approval_context: "approved for this turn" })) {
      events.push(event);
    }

    expect(events.map((event) => event.event)).toEqual(["approval_resolved", "run_completed"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/approvals/approve/stream",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("streams structured user interaction resumes through the dedicated endpoint", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: user_interaction_resolved\ndata: {"thread_id":"thread-a","request_id":"ui:stack"}\n\n' +
              'event: run_completed\ndata: {"thread_id":"thread-a","status":"completed"}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const events = [];
    for await (const event of streamThreadUserInteractionWithSignal("thread-a", {
      request_id: "ui:stack",
      selected_option_ids: ["vite"],
      custom_response: null,
    })) {
      events.push(event);
    }

    expect(events.map((event) => event.event)).toEqual(["user_interaction_resolved", "run_completed"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/interactions/resume/stream",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          request_id: "ui:stack",
          selected_option_ids: ["vite"],
          custom_response: null,
        }),
      }),
    );
  });

  it("cancels approvals through the cancel endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: "thread-a",
          status: "cancelled",
          title: null,
          summary: null,
          active_model: null,
          reasoning_effort: null,
          execution_mode: "agent",
          token_usage: {},
          approval_policy_summary: "Agent mode allows runtime tool execution. Read-only filesystem actions like list_dir, read_file, and extract_document run without approval; writes, shell execution, and external or otherwise guarded actions still require explicit approval.",
          allowed_local_actions: ["conversation", "filesystem_tools"],
          requires_approval_actions: ["guarded_tool_calls"],
          restricted_actions: [],
          visible_tool_names: [],
          deferred_tool_names: [],
          enabled_skill_ids: [],
          memory_namespace: null,
          injected_memory_snapshot_id: null,
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
          last_error: "cancelled from ui",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const result = await cancelApproval("thread-a", { reason: "cancelled from ui" });
    expect(result.status).toBe("cancelled");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/approvals/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("interrupts active thread runs through the run interrupt endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: "thread-a",
          status: "running",
          title: null,
          summary: null,
          active_model: null,
          reasoning_effort: null,
          execution_mode: "agent",
          token_usage: {},
          approval_policy_summary: null,
          allowed_local_actions: [],
          requires_approval_actions: [],
          restricted_actions: [],
          visible_tool_names: [],
          deferred_tool_names: [],
          enabled_skill_ids: [],
          memory_namespace: null,
          injected_memory_snapshot_id: null,
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
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const result = await interruptThreadRun("thread-a", { reason: "stop" });

    expect(result.thread_id).toBe("thread-a");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/runs/interrupt",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "stop" }),
      }),
    );
  });

  it("manages queued follow-ups through dedicated thread endpoints", async () => {
    const queued = {
      queue_id: "followup-1",
      thread_id: "thread-a",
      message: "continue after this run",
      mode: "followup",
      status: "queued",
      created_at: "2026-05-25T00:00:00.000Z",
      updated_at: "2026-05-25T00:00:00.000Z",
      uploaded_filenames: [],
      uploaded_file_refs: [],
      promoted_capabilities: [],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(queued), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...queued, message: "edited", mode: "guidance" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...queued, mode: "guidance" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...queued, status: "deleted" }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await enqueueThreadFollowup("thread-a", {
      message: "continue after this run",
      mode: "followup",
      execution_mode: "agent",
    });
    await updateThreadFollowup("thread-a", "followup-1", { message: "edited", mode: "guidance" });
    await popNextThreadFollowup("thread-a");
    await deleteThreadFollowup("thread-a", "followup-1");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:18000/threads/thread-a/followups",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          message: "continue after this run",
          mode: "followup",
          execution_mode: "agent",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:18000/threads/thread-a/followups/followup-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ message: "edited", mode: "guidance" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:18000/threads/thread-a/followups/next",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "http://127.0.0.1:18000/threads/thread-a/followups/followup-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("deletes threads through the gateway wrapper", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ thread_id: "thread-a", deleted: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await deleteThread("thread-a");
    expect(result).toEqual({ thread_id: "thread-a", deleted: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("loads thread detail through the dedicated detail endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread: { thread_id: "thread-a", title: "Alpha", status: "completed", updated_at: "", last_user_message_preview: "hello", has_pending_approval: false, has_active_subagent_tasks: false },
          state: { thread_id: "thread-a", status: "completed", title: "Alpha", summary: null, active_model: "openai", reasoning_effort: "high", visible_tool_names: [], deferred_tool_names: [], enabled_skill_ids: [], memory_namespace: null, injected_memory_snapshot_id: null, has_pending_approval: false, pending_approval_reason: null, output_artifacts: [], uploaded_files: [], presented_artifacts: [], active_subagent_task_ids: [], last_error: null },
          messages: [
            { message_id: "message-0", role: "human", content: "hello", content_blocks: [], reasoning: null, tool_calls: [], tool_call_id: null, name: null, status: null, artifact_refs: [], approval: null },
            { message_id: "message-1", role: "ai", content: "hi", content_blocks: [{ type: "thinking", text: "thinking" }, { type: "text", text: "hi" }], reasoning: { text: "thinking", block_count: 1, duration_ms: 1200 }, tool_calls: [], tool_call_id: null, name: null, status: null, artifact_refs: [], approval: null },
          ],
          message_window: { total: 2, offset: 0, limit: null, returned: 2, has_more_before: false, has_more_after: false, truncated: false, start_message_id: "message-0", end_message_id: "message-1" },
          pending_approval: null,
          stream_capabilities: { supports_message_delta: true, supports_reasoning_delta: true, supports_structured_events: true },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const result = await getThreadDetail("thread-a");
    expect(result.thread.thread_id).toBe("thread-a");
    expect(result.messages[1]?.reasoning?.text).toBe("thinking");
    expect(result.messages[1]?.reasoning?.duration_ms).toBe(1200);
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18000/threads/thread-a/detail", expect.any(Object));
  });

  it("loads thread evaluation reports through the observability endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          report_id: "report-thread-a",
          thread_id: "thread-a",
          run_id: "run-a",
          generated_at: "2026-06-11T00:00:00.000Z",
          outcome: "passed",
          score: 1,
          evaluator: null,
          runtime: {
            status: "completed",
            model: "openai",
            execution_mode: "default",
            reasoning_effort: null,
            runtime_phase_timings: {},
            runtime_phase_diagnostics: {},
            runtime_assembly_snapshot: {},
            runtime_assembly_diff: {},
            context_v2_evaluation: {
              trace_id: "ctx-trace-1",
              selected_memory: ["claim-1"],
              selected_tools: ["read_file"],
              selected_tool_result_refs: ["artifact://thread-a/tool-results/call-read.txt"],
            },
            context_window_usage: {},
            token_usage: {},
            model_fallback_history: [],
          },
          tool_calls: [],
          approvals: [],
          artifacts: {},
          hidden_bug_risks: [],
          recommendations: [],
          notes: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const result = await getThreadEvaluationReport("thread-a");
    expect(result.runtime.context_v2_evaluation.selected_memory).toEqual(["claim-1"]);
    expect(result.runtime.context_v2_evaluation.selected_tool_result_refs).toEqual([
      "artifact://thread-a/tool-results/call-read.txt",
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/evaluation-report",
      expect.any(Object),
    );
  });

  it("loads thread detail with an explicit message window", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread: { thread_id: "thread-a", title: "Alpha", status: "completed", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false },
          state: { thread_id: "thread-a", status: "completed", title: "Alpha", summary: null, active_model: "openai", reasoning_effort: null, visible_tool_names: [], deferred_tool_names: [], enabled_skill_ids: [], memory_namespace: null, injected_memory_snapshot_id: null, has_pending_approval: false, pending_approval_reason: null, output_artifacts: [], uploaded_files: [], presented_artifacts: [], active_subagent_task_ids: [], last_error: null },
          messages: [],
          message_window: { total: 20, offset: 0, limit: 120, returned: 20, has_more_before: false, has_more_after: false, truncated: false, start_message_id: null, end_message_id: null },
          pending_approval: null,
          stream_capabilities: { supports_message_delta: true, supports_reasoning_delta: true, supports_structured_events: true },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await getThreadDetail("thread-a", { messageLimit: 120 });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18000/threads/thread-a/detail?message_limit=120", expect.any(Object));
  });

  it("loads thread detail with an explicit state scope", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread: { thread_id: "thread-a", title: "Alpha", status: "completed", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false },
          state: { thread_id: "thread-a", status: "completed", title: "Alpha", summary: null, active_model: "openai", reasoning_effort: null, visible_tool_names: [], deferred_tool_names: [], enabled_skill_ids: [], memory_namespace: null, injected_memory_snapshot_id: null, has_pending_approval: false, pending_approval_reason: null, output_artifacts: [], uploaded_files: [], presented_artifacts: [], active_subagent_task_ids: [], last_error: null },
          messages: [],
          message_window: { total: 20, offset: 0, limit: 120, returned: 20, has_more_before: false, has_more_after: false, truncated: false, start_message_id: null, end_message_id: null },
          pending_approval: null,
          stream_capabilities: { supports_message_delta: true, supports_reasoning_delta: true, supports_structured_events: true },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await getThreadDetail("thread-a", { messageLimit: 120, stateScope: "full" });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18000/threads/thread-a/detail?message_limit=120&state_scope=full", expect.any(Object));
  });

  it("loads thread detail from the event-log projection when requested", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread: { thread_id: "thread-a", title: "Alpha", status: "completed", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false },
          state: { thread_id: "thread-a", status: "completed", title: "Alpha", summary: null, active_model: "openai", reasoning_effort: null, visible_tool_names: [], deferred_tool_names: [], enabled_skill_ids: [], memory_namespace: null, injected_memory_snapshot_id: null, has_pending_approval: false, pending_approval_reason: null, output_artifacts: [], uploaded_files: [], presented_artifacts: [], active_subagent_task_ids: [], last_error: null },
          messages: [],
          message_window: { total: 0, offset: 0, limit: 120, returned: 0, has_more_before: false, has_more_after: false, truncated: false, start_message_id: null, end_message_id: null },
          pending_approval: null,
          stream_capabilities: { supports_message_delta: true, supports_reasoning_delta: true, supports_structured_events: true },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await getThreadDetail("thread-a", { messageLimit: 120, stateScope: "chat", stateSource: "event_log" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/detail?message_limit=120&state_scope=chat&state_source=event_log",
      expect.any(Object),
    );
  });

  it("loads thread detail with offset and limit for explicit history windows", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread: { thread_id: "thread-a", title: "Alpha", status: "completed", updated_at: "", last_user_message_preview: null, has_pending_approval: false, has_active_subagent_tasks: false },
          state: { thread_id: "thread-a", status: "completed", title: "Alpha", summary: null, active_model: "openai", reasoning_effort: null, visible_tool_names: [], deferred_tool_names: [], enabled_skill_ids: [], memory_namespace: null, injected_memory_snapshot_id: null, has_pending_approval: false, pending_approval_reason: null, output_artifacts: [], uploaded_files: [], presented_artifacts: [], active_subagent_task_ids: [], last_error: null },
          messages: [],
          message_window: { total: 600, offset: 360, limit: 120, returned: 120, has_more_before: true, has_more_after: true, truncated: true, start_message_id: null, end_message_id: null },
          pending_approval: null,
          stream_capabilities: { supports_message_delta: true, supports_reasoning_delta: true, supports_structured_events: true },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await getThreadDetail("thread-a", { messageOffset: 360, messageLimit: 120 });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18000/threads/thread-a/detail?message_offset=360&message_limit=120", expect.any(Object));
  });

  it("streams lifecycle events through post event-stream parsing", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: run_started\ndata: {"thread_id":"thread-a"}\n\n' +
              'event: message_opened\ndata: {"message_id":"message-1","role":"ai"}\n\n' +
              'event: message_delta\ndata: {"message_id":"message-1","delta":"hello"}\n\n' +
              'event: run_completed\ndata: {"thread_id":"thread-a","status":"completed"}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const events = [];
    for await (const event of streamThreadRun("thread-a", { message: "hello", execution_mode: "agent" })) {
      events.push(event);
    }

    expect(events.map((event) => event.event)).toEqual([
      "run_started",
      "message_opened",
      "message_delta",
      "run_completed",
    ]);
  });

  it("passes Last-Event-ID when resuming a posted event stream", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('event: run_completed\ndata: {"thread_id":"thread-a","status":"completed"}\n\n'),
        );
        controller.close();
      },
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const events = [];
    for await (const event of streamThreadRunWithSignal(
      "thread-a",
      { message: "hello", execution_mode: "agent" },
      undefined,
      "run-1:000002",
    )) {
      events.push(event);
    }

    expect(events.map((event) => event.event)).toEqual(["run_completed"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/runs/stream",
      expect.objectContaining({
        headers: expect.objectContaining({ "Last-Event-ID": "run-1:000002" }),
      }),
    );
  });

  it("lists persisted run events with run-local cursor parameters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: "thread-a",
          run_id: "run-1",
          after_sequence: 3,
          next_cursor: 4,
          has_more: false,
          events: [{ event: "run_completed", data: { run_id: "run-1", sequence: 4 } }],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const replay = await listThreadRunEvents("thread-a", { runId: "run-1", afterSequence: 3, limit: 50 });

    expect(replay.next_cursor).toBe(4);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/runs/events?run_id=run-1&after_sequence=3&limit=50",
      expect.any(Object),
    );
  });

  it("sends execution mode in run submissions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: "thread-a",
          status: "completed",
          assistant_message: "done",
          last_error: null,
          thread: {
            thread_id: "thread-a",
            title: null,
            status: "completed",
            updated_at: "",
            last_user_message_preview: null,
            has_pending_approval: false,
            has_active_subagent_tasks: false,
          },
          state: {
            thread_id: "thread-a",
            status: "completed",
            title: null,
            summary: null,
            active_model: "openai",
            reasoning_effort: null,
            execution_mode: "agent",
            visible_tool_names: [],
            deferred_tool_names: [],
            enabled_skill_ids: [],
            memory_namespace: null,
            injected_memory_snapshot_id: null,
            has_pending_approval: false,
            pending_approval_reason: null,
            output_artifacts: [],
            uploaded_files: [],
            presented_artifacts: [],
            active_subagent_task_ids: [],
            recent_tool_activity: [],
            last_error: null,
          },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const result = await runThread("thread-a", {
      message: "ship it",
      execution_mode: "agent",
    });

    expect(result.state.execution_mode).toBe("agent");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/threads/thread-a/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ message: "ship it", execution_mode: "agent" }),
      }),
    );
  });

  it("resolves artifact paths against the gateway origin", () => {
    expect(resolveGatewayUrl("/threads/thread-a/artifacts/uploads/note.txt")).toBe(
      "http://127.0.0.1:18000/threads/thread-a/artifacts/uploads/note.txt",
    );
  });

  it("loads and updates thread settings through dedicated settings endpoints", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            thread_id: "thread-a",
            execution_mode: "agent",
            selected_model: "openai_compatible",
            selected_profile: "default",
            selected_reasoning_effort: "high",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            thread_id: "thread-a",
            execution_mode: "full_access",
            selected_model: "minimax",
            selected_profile: "coder",
            selected_reasoning_effort: "xhigh",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    const settings = await getThreadSettings("thread-a");
    expect(settings.execution_mode).toBe("agent");

    const updated = await updateThreadSettings("thread-a", {
      execution_mode: "full_access",
      selected_model: "minimax",
      selected_profile: "coder",
      selected_reasoning_effort: "xhigh",
    });
    expect(updated.execution_mode).toBe("full_access");
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:18000/threads/thread-a/settings",
      expect.any(Object),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:18000/threads/thread-a/settings",
      expect.objectContaining({ method: "PUT" }),
    );
  });
});
