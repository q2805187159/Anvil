import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { getThreadDetailRefetchInterval, hasRecoverableThread, useCancelThreadApproval, useCreateThread, useDeleteThread, useEnqueueThreadFollowup, useThreadDetail, useThreadRunStream, useThreadState, useThreads, useUpdateThreadFollowup, useUpdateThreadSettings } from "./hooks";


function wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function wrapperWithClient(client: QueryClient) {
  return function QueryWrapper({ children }: Readonly<{ children: React.ReactNode }>) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}


describe("thread hooks", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("invalidates thread queries after create", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.spyOn(api, "createThread").mockResolvedValue({
      thread_id: "thread-new",
      title: null,
      status: "ready",
      updated_at: "",
      last_user_message_preview: null,
      has_pending_approval: false,
      has_active_subagent_tasks: false,
    });

    const { result } = renderHook(() => useCreateThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("thread-new");
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
  });

  it("moves a thread to the top of the cached list when a run starts", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["threads"], [
      {
        thread_id: "thread-newer",
        title: "Newer",
        status: "completed",
        updated_at: "2026-05-23T11:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      {
        thread_id: "thread-old",
        title: "Old",
        status: "completed",
        updated_at: "2026-05-23T09:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ]);
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield { event: "run_started", data: { thread_id: "thread-old" } };
    });

    const { result } = renderHook(() => useThreadRunStream("thread-old"), { wrapper: wrapperWithClient(client) });
    await act(async () => {
      await result.current.start({ message: "resume older thread", execution_mode: "agent" });
    });

    expect((client.getQueryData(["threads"]) as Array<{ thread_id: string; status: string; last_user_message_preview: string | null }>).map((thread) => thread.thread_id)).toEqual([
      "thread-old",
      "thread-newer",
    ]);
    expect(client.getQueryData<Array<{ status: string; last_user_message_preview: string | null }>>(["threads"])?.[0]).toMatchObject({
      status: "running",
      last_user_message_preview: "resume older thread",
    });
  });

  it("keeps optimistic message recency when a stale thread refetch arrives", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["threads"], [
      {
        thread_id: "thread-newer",
        title: "Newer",
        status: "completed",
        updated_at: "2026-05-23T11:00:00.000Z",
        last_message_at: "2026-05-23T11:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      {
        thread_id: "thread-old",
        title: "Old",
        status: "completed",
        updated_at: "2026-05-23T09:00:00.000Z",
        last_message_at: "2026-05-23T09:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ]);
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield { event: "run_started", data: { thread_id: "thread-old" } };
    });
    vi.spyOn(api, "listThreads").mockResolvedValue([
      {
        thread_id: "thread-newer",
        title: "Newer",
        status: "completed",
        updated_at: "2026-05-23T11:00:00.000Z",
        last_message_at: "2026-05-23T11:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      {
        thread_id: "thread-old",
        title: "Old",
        status: "completed",
        updated_at: "2026-05-23T09:00:00.000Z",
        last_message_at: "2026-05-23T09:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ]);

    const { result: threadsResult } = renderHook(() => useThreads(), { wrapper: wrapperWithClient(client) });
    const { result } = renderHook(() => useThreadRunStream("thread-old"), { wrapper: wrapperWithClient(client) });
    await act(async () => {
      await result.current.start({ message: "resume older thread", execution_mode: "agent" });
    });
    await act(async () => {
      await threadsResult.current.refetch();
    });

    expect(client.getQueryData<Array<{ thread_id: string }>>(["threads"])?.map((thread) => thread.thread_id)).toEqual([
      "thread-old",
      "thread-newer",
    ]);
  });

  it("keeps paused active thread status when a stale thread refetch arrives", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["threads"], [
      {
        thread_id: "thread-a",
        title: "Alpha",
        status: "awaiting_approval",
        updated_at: "2026-05-23T11:00:00.000Z",
        last_message_at: "2026-05-23T11:00:00.000Z",
        last_user_message_preview: "approve command",
        has_pending_approval: true,
        has_active_subagent_tasks: false,
      },
    ]);
    vi.spyOn(api, "listThreads").mockResolvedValue([
      {
        thread_id: "thread-a",
        title: "Alpha",
        status: "completed",
        updated_at: "2026-05-23T10:00:00.000Z",
        last_message_at: "2026-05-23T10:00:00.000Z",
        last_user_message_preview: "approve command",
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ]);

    const { result } = renderHook(() => useThreads(), { wrapper: wrapperWithClient(client) });
    await act(async () => {
      await result.current.refetch();
    });

    expect(client.getQueryData<Array<{ status: string; has_pending_approval: boolean }>>(["threads"])?.[0]).toMatchObject({
      status: "awaiting_approval",
      has_pending_approval: true,
    });
  });

  it("treats paused active threads as recoverable for list polling", () => {
    expect(
      hasRecoverableThread([
        {
          thread_id: "thread-a",
          title: "Alpha",
          status: "awaiting_clarification",
          updated_at: "",
          last_user_message_preview: null,
          has_pending_approval: false,
          has_active_subagent_tasks: false,
        },
      ]),
    ).toBe(true);
  });

  it("settles optimistic running status when a completed stream payload has older durable recency", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["threads"], [
      {
        thread_id: "thread-newer",
        title: "Newer",
        status: "completed",
        updated_at: "2026-05-23T11:00:00.000Z",
        last_message_at: "2026-05-23T11:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      {
        thread_id: "thread-old",
        title: "Old",
        status: "completed",
        updated_at: "2026-05-23T09:00:00.000Z",
        last_message_at: "2026-05-23T09:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ]);
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield { event: "run_started", data: { thread_id: "thread-old" } };
      yield {
        event: "run_completed",
        data: {
          thread_id: "thread-old",
          status: "completed",
          thread: {
            thread_id: "thread-old",
            title: "Old",
            status: "completed",
            updated_at: "2026-05-23T09:00:30.000Z",
            last_message_at: "2026-05-23T09:00:30.000Z",
            last_user_message_preview: "resume older thread",
            has_pending_approval: false,
            has_active_subagent_tasks: false,
          },
        },
      };
    });

    const { result } = renderHook(() => useThreadRunStream("thread-old"), { wrapper: wrapperWithClient(client) });
    await act(async () => {
      await result.current.start({ message: "resume older thread", execution_mode: "agent" });
    });

    const cached = client.getQueryData<Array<{ thread_id: string; status: string; last_user_message_preview: string | null }>>(["threads"]);
    expect(cached?.map((thread) => thread.thread_id)).toEqual(["thread-old", "thread-newer"]);
    expect(cached?.[0]).toMatchObject({
      status: "completed",
      last_user_message_preview: "resume older thread",
    });
  });

  it("collects lifecycle events and invalidates state on completion", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const setQueryDataSpy = vi.spyOn(QueryClient.prototype, "setQueryData");
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield { event: "run_started", data: { thread_id: "thread-a" } };
      yield { event: "message_opened", data: { message_id: "message-1", role: "ai" } };
      yield { event: "message_delta", data: { message_id: "message-1", delta: "done" } };
      yield {
        event: "run_completed",
        data: {
          thread_id: "thread-a",
          status: "completed",
          thread: {
            thread_id: "thread-a",
            title: "Alpha",
            status: "completed",
            updated_at: "",
            last_user_message_preview: "done",
            has_pending_approval: false,
            has_active_subagent_tasks: false,
          },
          state: {
            thread_id: "thread-a",
            status: "completed",
            token_usage: { total_tokens: 650 },
            context_window_usage: {
              context_tokens: 650,
              context_source: "provider",
              total_tokens: 650,
              context_window_tokens: 1000,
              auto_compact_threshold_tokens: 800,
              compact_ratio: 0.8125,
              compact_status: "below_threshold",
            },
          },
        },
      };
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
    expect(result.current.events.map((event) => event.event)).toEqual([
      "run_started",
      "message_opened",
      "message_delta",
      "run_completed",
    ]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
    expect(setQueryDataSpy).toHaveBeenCalledWith(
      ["thread-state", "thread-a"],
      expect.objectContaining({
        context_window_usage: expect.objectContaining({
          compact_ratio: 0.8125,
          compact_status: "below_threshold",
        }),
      }),
    );
  });

  it("keeps a newly created thread stream alive when the active thread id is adopted mid-run", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const emittedEvents: string[] = [];
    let streamAborted = false;
    let releaseStream: (() => void) | null = null;
    const streamBarrier = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* (_threadId, _body, signal) {
      signal?.addEventListener("abort", () => {
        streamAborted = true;
      });
      emittedEvents.push("run_started");
      yield { event: "run_started", data: { thread_id: "thread-new" } };
      await streamBarrier;
      if (signal?.aborted) {
        streamAborted = true;
        return;
      }
      emittedEvents.push("run_completed");
      yield { event: "run_completed", data: { thread_id: "thread-new", status: "completed" } };
    });

    const { result, rerender } = renderHook(
      ({ threadId }: { threadId: string | null }) => useThreadRunStream(threadId),
      {
        wrapper,
        initialProps: { threadId: null as string | null },
      },
    );

    await act(async () => {
      const pending = result.current.start({ message: "hello", execution_mode: "agent" }, "thread-new");
      rerender({ threadId: "thread-new" });
      await Promise.resolve();
      releaseStream?.();
      await pending;
    });

    expect(emittedEvents).toEqual(["run_started", "run_completed"]);
    expect(streamAborted).toBe(false);
    expect(result.current.error).toBeNull();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-new"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-new"] });
  });

  it("keeps a background stream alive across active thread switches", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const emittedEvents: string[] = [];
    let streamAborted = false;
    let releaseDelta: (() => void) | null = null;
    let releaseComplete: (() => void) | null = null;
    const deltaBarrier = new Promise<void>((resolve) => {
      releaseDelta = resolve;
    });
    const completeBarrier = new Promise<void>((resolve) => {
      releaseComplete = resolve;
    });

    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* (_threadId, _body, signal) {
      signal?.addEventListener("abort", () => {
        streamAborted = true;
      });
      emittedEvents.push("run_started");
      yield { event: "run_started", data: { thread_id: "thread-a" } };
      await deltaBarrier;
      if (signal?.aborted) {
        streamAborted = true;
        return;
      }
      emittedEvents.push("message_delta");
      yield { event: "message_opened", data: { thread_id: "thread-a", message_id: "message-1", role: "ai" } };
      yield { event: "message_delta", data: { thread_id: "thread-a", message_id: "message-1", delta: "partial" } };
      await completeBarrier;
      if (signal?.aborted) {
        streamAborted = true;
        return;
      }
      emittedEvents.push("run_completed");
      yield { event: "message_completed", data: { thread_id: "thread-a", message_id: "message-1" } };
      yield { event: "run_completed", data: { thread_id: "thread-a", status: "completed" } };
    });

    const { result, rerender } = renderHook(
      ({ threadId }: { threadId: string | null }) => useThreadRunStream(threadId),
      {
        wrapper,
        initialProps: { threadId: "thread-a" },
      },
    );

    let pending: ReturnType<typeof result.current.start>;
    await act(async () => {
      pending = result.current.start({ message: "hello", execution_mode: "agent" });
    });

    await waitFor(() => {
      expect(result.current.events.map((event) => event.event)).toEqual(["run_started"]);
    });

    rerender({ threadId: "thread-b" });
    expect(result.current.events).toEqual([]);
    expect(result.current.isStreaming).toBe(false);

    await act(async () => {
      releaseDelta?.();
    });
    await waitFor(() => {
      expect(emittedEvents).toContain("message_delta");
    });

    rerender({ threadId: "thread-a" });
    await waitFor(() => {
      expect(result.current.events.map((event) => event.event)).toEqual([
        "run_started",
        "message_opened",
        "message_delta",
      ]);
    });
    expect(result.current.isStreaming).toBe(true);

    await act(async () => {
      releaseComplete?.();
      await pending!;
    });

    expect(streamAborted).toBe(false);
    expect(emittedEvents).toEqual(["run_started", "message_delta", "run_completed"]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("resumes structured user interactions through the same run stream lifecycle", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const events: string[] = [];
    vi.spyOn(api, "streamThreadUserInteractionWithSignal").mockImplementation(async function* (_threadId, body) {
      events.push(String(body.request_id));
      yield { event: "user_interaction_resolved", data: { thread_id: "thread-a", request_id: body.request_id } };
      yield { event: "run_completed", data: { thread_id: "thread-a", status: "completed" } };
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });

    await act(async () => {
      await result.current.resumeUserInteraction({
        request_id: "ui:stack",
        selected_option_ids: ["vite"],
        custom_response: null,
      });
    });

    expect(events).toEqual(["ui:stack"]);
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.events.map((event) => event.event)).toEqual([
      "user_interaction_resolved",
      "run_completed",
    ]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("marks streaming complete as soon as run_completed arrives while retaining terminal events", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    let releaseClose: (() => void) | null = null;
    const closeBarrier = new Promise<void>((resolve) => {
      releaseClose = resolve;
    });
    let settled = false;
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield { event: "run_preparing", data: { thread_id: "thread-a", status: "preparing" } };
      yield { event: "run_started", data: { thread_id: "thread-a" } };
      yield { event: "run_completed", data: { thread_id: "thread-a", status: "completed" } };
      await closeBarrier;
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let pending: ReturnType<typeof result.current.start>;
    await act(async () => {
      pending = result.current.start({ message: "hello", execution_mode: "agent" });
      void pending.then(() => {
        settled = true;
      });
    });

    await waitFor(() => {
      expect(result.current.events.map((event) => event.event)).toEqual(["run_preparing", "run_started", "run_completed"]);
    });
    expect(result.current.isStreaming).toBe(false);
    expect(settled).toBe(false);
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
    });

    await act(async () => {
      releaseClose?.();
      await pending!;
    });
    expect(settled).toBe(true);
  });

  it("replays missing run events when a stream closes before a terminal event", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_started",
        event_id: "run-1:000001",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-1", sequence: 1 },
      };
      yield {
        event: "message_delta",
        event_id: "run-1:000002",
        sequence: 2,
        data: { thread_id: "thread-a", run_id: "run-1", sequence: 2, message_id: "message-1", delta: "partial" },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-1",
      after_sequence: 2,
      next_cursor: 4,
      has_more: false,
      events: [
        {
          event: "message_completed",
          event_id: "run-1:000003",
          sequence: 3,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 3, message_id: "message-1" },
        },
        {
          event: "run_completed",
          event_id: "run-1:000004",
          sequence: 4,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 4, status: "completed" },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(streamResult).toEqual({ status: "completed", error: null });
    expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-1", afterSequence: 2, limit: 500 });
    expect(result.current.events.map((event) => event.event)).toEqual([
      "run_started",
      "message_delta",
      "message_completed",
      "run_completed",
    ]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("uses the highest live sequence as the durable replay cursor when stream events arrive out of order", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_started",
        event_id: "run-out-of-order:000001",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-out-of-order", sequence: 1 },
      };
      yield {
        event: "message_delta",
        event_id: "run-out-of-order:000003",
        sequence: 3,
        data: {
          thread_id: "thread-a",
          run_id: "run-out-of-order",
          sequence: 3,
          message_id: "message-1",
          delta: "latest",
        },
      };
      yield {
        event: "step_delta",
        event_id: "run-out-of-order:000002",
        sequence: 2,
        data: {
          thread_id: "thread-a",
          run_id: "run-out-of-order",
          sequence: 2,
          step_id: "message-1:content",
          payload_delta: "middle",
        },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-out-of-order",
      after_sequence: 3,
      next_cursor: 4,
      has_more: false,
      events: [
        {
          event: "run_completed",
          event_id: "run-out-of-order:000004",
          sequence: 4,
          data: { thread_id: "thread-a", run_id: "run-out-of-order", sequence: 4, status: "completed" },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-out-of-order",
      afterSequence: 3,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "completed", error: null });
    expect(result.current.events.map((event) => event.sequence)).toEqual([1, 2, 3, 4]);
  });

  it("stops durable replay pagination when the replay cursor does not advance", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_started",
        event_id: "run-stalled-page:000001",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-stalled-page", sequence: 1 },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-stalled-page",
      after_sequence: 1,
      next_cursor: 1,
      has_more: true,
      events: [],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(replaySpy).toHaveBeenCalledTimes(1);
    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-stalled-page",
      afterSequence: 1,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "interrupted", error: null });
    expect(result.current.events.map((event) => event.sequence)).toEqual([1]);
  });

  it("keeps the replay cursor monotonic when a replay page cursor lags behind returned events", async () => {
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          event_id: "run-lagging-page:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-lagging-page", sequence: 1 },
        };
      })
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_preparing",
          data: { thread_id: "thread-a", status: "preparing", phase: "gateway_received" },
        };
      });
    const replaySpy = vi
      .spyOn(api, "listThreadRunEvents")
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-lagging-page",
        after_sequence: 1,
        next_cursor: 1,
        has_more: true,
        events: [
          {
            event: "message_delta",
            event_id: "run-lagging-page:000002",
            sequence: 2,
            data: {
              thread_id: "thread-a",
              run_id: "run-lagging-page",
              sequence: 2,
              message_id: "message-1",
              delta: "late",
            },
          },
        ],
      })
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-lagging-page",
        after_sequence: 2,
        next_cursor: 3,
        has_more: false,
        events: [
          {
            event: "run_completed",
            event_id: "run-lagging-page:000003",
            sequence: 3,
            data: { thread_id: "thread-a", run_id: "run-lagging-page", sequence: 3, status: "completed" },
          },
        ],
      });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    await act(async () => {
      streamResult = await result.current.start({ message: "resume same run", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenNthCalledWith(
      2,
      "thread-a",
      { message: "resume same run", execution_mode: "agent" },
      expect.any(AbortSignal),
      "run-lagging-page:000002",
    );
    expect(replaySpy).toHaveBeenNthCalledWith(2, "thread-a", {
      runId: "run-lagging-page",
      afterSequence: 2,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "completed", error: null });
    expect(result.current.events.map((event) => event.event)).toEqual(["run_preparing", "run_completed"]);
  });

  it("ignores stale durable replay events at or before the requested cursor", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "message_delta",
        event_id: "run-stale-cursor:000002",
        sequence: 2,
        data: {
          thread_id: "thread-a",
          run_id: "run-stale-cursor",
          sequence: 2,
          message_id: "message-1",
          delta: "current",
        },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-stale-cursor",
      after_sequence: 2,
      next_cursor: 3,
      has_more: false,
      events: [
        {
          event: "message_delta",
          event_id: "run-stale-cursor:000001-stale",
          sequence: 1,
          data: {
            thread_id: "thread-a",
            run_id: "run-stale-cursor",
            sequence: 1,
            message_id: "message-1",
            delta: "stale",
          },
        },
        {
          event: "run_completed",
          event_id: "run-stale-cursor:000003",
          sequence: 3,
          data: { thread_id: "thread-a", run_id: "run-stale-cursor", sequence: 3, status: "completed" },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-stale-cursor",
      afterSequence: 2,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "completed", error: null });
    expect(result.current.events.map((event) => `${event.sequence}:${event.event}`)).toEqual([
      "2:message_delta",
      "3:run_completed",
    ]);
  });

  it("ignores durable replay pages whose echoed cursor does not match the request", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "message_delta",
        event_id: "run-cursor-echo:000004",
        sequence: 4,
        data: {
          thread_id: "thread-a",
          run_id: "run-cursor-echo",
          sequence: 4,
          message_id: "message-1",
          delta: "current",
        },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-cursor-echo",
      after_sequence: 2,
      next_cursor: 5,
      has_more: false,
      events: [
        {
          event: "run_completed",
          event_id: "run-cursor-echo:000005",
          sequence: 5,
          data: { thread_id: "thread-a", run_id: "run-cursor-echo", sequence: 5, status: "completed" },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-cursor-echo",
      afterSequence: 4,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "interrupted", error: null });
    expect(result.current.events.map((event) => `${event.sequence}:${event.event}`)).toEqual(["4:message_delta"]);
  });

  it("dedupes replay events by run sequence when one copy lacks event_id", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "message_delta",
        sequence: 2,
        data: {
          thread_id: "thread-a",
          run_id: "run-sequence-dedupe",
          sequence: 2,
          message_id: "message-1",
          delta: "streamed",
        },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-sequence-dedupe",
      after_sequence: 2,
      next_cursor: 3,
      has_more: false,
      events: [
        {
          event: "message_delta",
          event_id: "run-sequence-dedupe:000002",
          sequence: 2,
          data: {
            thread_id: "thread-a",
            run_id: "run-sequence-dedupe",
            sequence: 2,
            message_id: "message-1",
            delta: "streamed",
          },
        },
        {
          event: "run_completed",
          event_id: "run-sequence-dedupe:000003",
          sequence: 3,
          data: {
            thread_id: "thread-a",
            run_id: "run-sequence-dedupe",
            sequence: 3,
            status: "completed",
          },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-sequence-dedupe",
      afterSequence: 2,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "completed", error: null });
    expect(result.current.events.map((event) => event.event)).toEqual(["message_delta", "run_completed"]);
    expect(result.current.events.map((event) => event.sequence)).toEqual([2, 3]);
  });

  it("ignores durable replay pages that do not match the requested run", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_started",
        event_id: "run-current:000001",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-current", sequence: 1 },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-stale",
      after_sequence: 1,
      next_cursor: 2,
      has_more: false,
      events: [
        {
          event: "run_completed",
          event_id: "run-stale:000002",
          sequence: 2,
          data: { thread_id: "thread-a", run_id: "run-stale", sequence: 2, status: "completed" },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-current",
      afterSequence: 1,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "interrupted", error: null });
    expect(result.current.events.map((event) => event.data.run_id)).toEqual(["run-current"]);
    expect(result.current.events.map((event) => event.event)).toEqual(["run_started"]);
  });

  it("ignores durable replay pages that do not match the requested thread", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_started",
        event_id: "run-current:000001",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-current", sequence: 1 },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-other",
      run_id: "run-current",
      after_sequence: 1,
      next_cursor: 2,
      has_more: false,
      events: [
        {
          event: "run_completed",
          event_id: "run-current:000002",
          sequence: 2,
          data: { thread_id: "thread-other", run_id: "run-current", sequence: 2, status: "completed" },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-current",
      afterSequence: 1,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "interrupted", error: null });
    expect(result.current.events.map((event) => event.data.thread_id)).toEqual(["thread-a"]);
    expect(result.current.events.map((event) => event.event)).toEqual(["run_started"]);
  });

  it("surfaces replayed run_failed events after a stream disconnect", async () => {
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_started",
        event_id: "run-2:000001",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-2", sequence: 1 },
      };
    });
    vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-2",
      after_sequence: 1,
      next_cursor: 2,
      has_more: false,
      events: [
        {
          event: "run_failed",
          event_id: "run-2:000002",
          sequence: 2,
          data: { thread_id: "thread-a", run_id: "run-2", sequence: 2, error: "provider unavailable" },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(streamResult).toEqual({ status: "failed", error: "provider unavailable" });
    expect(result.current.error).toBe("provider unavailable");
    expect(result.current.events.map((event) => event.event)).toEqual(["run_started", "run_failed"]);
  });

  it("hydrates missing events for a running thread after the page reconnects with only durable state", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      run_id: "run-reconnect",
      status: "running",
      execution_mode: "agent",
      is_plan_mode: false,
      token_usage: {},
      allowed_local_actions: [],
      requires_approval_actions: [],
      restricted_actions: [],
      visible_tool_names: [],
      deferred_tool_names: [],
      enabled_skill_ids: [],
      output_artifacts: [],
      uploaded_files: [],
      presented_artifacts: [],
      runtime_path_roots: [],
      active_subagent_task_ids: [],
      subagent_tasks: [],
      process_sessions: [],
      durable_subagent_job_history: [],
      tool_calls: [],
      recent_tool_activity: [],
      recent_approval_events: [],
      queued_followups: [],
      todo_snapshot: [],
      archived_summaries: [],
      project_context_files: [],
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-reconnect",
      after_sequence: 0,
      next_cursor: 2,
      has_more: false,
      events: [
        {
          event: "run_started",
          event_id: "run-reconnect:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-reconnect", sequence: 1 },
        },
        {
          event: "step_started",
          event_id: "run-reconnect:000002",
          sequence: 2,
          data: {
            thread_id: "thread-a",
            run_id: "run-reconnect",
            sequence: 2,
            step: {
              step_id: "message-1:content",
              message_id: "message-1",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "partial",
              language: "markdown",
              order: 0,
            },
          },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

    await waitFor(() => {
      expect(result.current.events.map((event) => event.event)).toEqual(["run_started", "step_started"]);
    });
    expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-reconnect", afterSequence: 0, limit: 500 });
  });

  it("hydrates missing events for an approval-paused thread after reconnecting with only durable state", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      run_id: "run-awaiting-approval",
      status: "awaiting_approval",
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-awaiting-approval",
      after_sequence: 0,
      next_cursor: 1,
      has_more: false,
      events: [
        {
          event: "approval_requested",
          event_id: "run-awaiting-approval:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-awaiting-approval", sequence: 1 },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

    await waitFor(() => {
      expect(result.current.events.map((event) => event.event)).toEqual(["approval_requested"]);
    });
    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-awaiting-approval",
      afterSequence: 0,
      limit: 500,
    });
  });

  it("hydrates missing events for a clarification-paused thread after reconnecting with only durable state", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      run_id: "run-awaiting-clarification",
      status: "awaiting_clarification",
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-awaiting-clarification",
      after_sequence: 0,
      next_cursor: 1,
      has_more: false,
      events: [
        {
          event: "step_started",
          event_id: "run-awaiting-clarification:000001",
          sequence: 1,
          data: {
            thread_id: "thread-a",
            run_id: "run-awaiting-clarification",
            sequence: 1,
            step: {
              step_id: "message-1:call:clarify",
              message_id: "message-1",
              type: "call",
              title: "Clarification",
              status: "running",
              tool_name: "ask_clarification",
              payload: "Which workspace should I inspect?",
            },
          },
        },
      ],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

    await waitFor(() => {
      expect(result.current.events.map((event) => event.event)).toEqual(["step_started"]);
    });
    expect(replaySpy).toHaveBeenCalledWith("thread-a", {
      runId: "run-awaiting-clarification",
      afterSequence: 0,
      limit: 500,
    });
  });

  it("keeps polling durable events for a recovered running thread until late terminal events arrive", async () => {
    vi.useFakeTimers();
    try {
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      client.setQueryData(["thread-state", "thread-a"], {
        thread_id: "thread-a",
        run_id: "run-late",
        status: "running",
      });
      const replaySpy = vi
        .spyOn(api, "listThreadRunEvents")
        .mockResolvedValueOnce({
          thread_id: "thread-a",
          run_id: "run-late",
          after_sequence: 0,
          next_cursor: 1,
          has_more: false,
          events: [
            {
              event: "run_started",
              event_id: "run-late:000001",
              sequence: 1,
              data: { thread_id: "thread-a", run_id: "run-late", sequence: 1 },
            },
          ],
        })
        .mockResolvedValueOnce({
          thread_id: "thread-a",
          run_id: "run-late",
          after_sequence: 1,
          next_cursor: 3,
          has_more: false,
          events: [
            {
              event: "message_delta",
              event_id: "run-late:000002",
              sequence: 2,
              data: { thread_id: "thread-a", run_id: "run-late", sequence: 2, message_id: "message-1", delta: "done" },
            },
            {
              event: "run_completed",
              event_id: "run-late:000003",
              sequence: 3,
              data: { thread_id: "thread-a", run_id: "run-late", sequence: 3, status: "completed" },
            },
          ],
        });

      const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

      await act(async () => {
        await Promise.resolve();
      });
      expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-late", afterSequence: 0, limit: 500 });
      expect(result.current.events.map((event) => event.event)).toEqual(["run_started"]);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });

      expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-late", afterSequence: 1, limit: 500 });
      expect(result.current.events.map((event) => event.event)).toEqual(["run_started", "message_delta", "run_completed"]);
      expect(result.current.isStreaming).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps polling recovered running events when cached active state omits the run id", async () => {
    vi.useFakeTimers();
    try {
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      client.setQueryData(["thread-state", "thread-a"], {
        thread_id: "thread-a",
        run_id: "run-active-missing-id",
        status: "running",
      });
      const replaySpy = vi
        .spyOn(api, "listThreadRunEvents")
        .mockResolvedValueOnce({
          thread_id: "thread-a",
          run_id: "run-active-missing-id",
          after_sequence: 0,
          next_cursor: 1,
          has_more: false,
          events: [
            {
              event: "run_started",
              event_id: "run-active-missing-id:000001",
              sequence: 1,
              data: { thread_id: "thread-a", run_id: "run-active-missing-id", sequence: 1 },
            },
          ],
        })
        .mockResolvedValueOnce({
          thread_id: "thread-a",
          run_id: "run-active-missing-id",
          after_sequence: 1,
          next_cursor: 2,
          has_more: false,
          events: [
            {
              event: "message_delta",
              event_id: "run-active-missing-id:000002",
              sequence: 2,
              data: {
                thread_id: "thread-a",
                run_id: "run-active-missing-id",
                sequence: 2,
                message_id: "message-1",
                delta: "still running",
              },
            },
          ],
        })
        .mockResolvedValueOnce({
          thread_id: "thread-a",
          run_id: "run-active-missing-id",
          after_sequence: 2,
          next_cursor: 3,
          has_more: false,
          events: [
            {
              event: "run_completed",
              event_id: "run-active-missing-id:000003",
              sequence: 3,
              data: { thread_id: "thread-a", run_id: "run-active-missing-id", sequence: 3, status: "completed" },
            },
          ],
        });

      const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

      await act(async () => {
        await Promise.resolve();
      });
      expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-active-missing-id", afterSequence: 0, limit: 500 });
      client.setQueryData(["thread-state", "thread-a"], {
        thread_id: "thread-a",
        status: "running",
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });

      expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-active-missing-id", afterSequence: 1, limit: 500 });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });

      expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-active-missing-id", afterSequence: 2, limit: 500 });
      expect(result.current.events.map((event) => event.event)).toEqual(["run_started", "message_delta", "run_completed"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("clears recovered-run polling when a new live run starts on the same thread", async () => {
    vi.useFakeTimers();
    try {
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      client.setQueryData(["thread-state", "thread-a"], {
        thread_id: "thread-a",
        run_id: "run-old",
        status: "running",
      });
      const replaySpy = vi
        .spyOn(api, "listThreadRunEvents")
        .mockImplementation(async (_threadId, options) => {
          const runId = options?.runId ?? null;
          const afterSequence = options?.afterSequence ?? 0;
          if (runId === "run-old" && afterSequence === 0) {
            return {
              thread_id: "thread-a",
              run_id: "run-old",
              after_sequence: 0,
              next_cursor: 1,
              has_more: false,
              events: [
                {
                  event: "run_started",
                  event_id: "run-old:000001",
                  sequence: 1,
                  data: { thread_id: "thread-a", run_id: "run-old", sequence: 1 },
                },
              ],
            };
          }
          if (runId === "run-old" && afterSequence === 1) {
            return {
              thread_id: "thread-a",
              run_id: "run-old",
              after_sequence: 1,
              next_cursor: 2,
              has_more: false,
              events: [
                {
                  event: "message_delta",
                  event_id: "run-old:000002",
                  sequence: 2,
                  data: { thread_id: "thread-a", run_id: "run-old", sequence: 2, message_id: "old-message", delta: "stale" },
                },
              ],
            };
          }
          return {
            thread_id: "thread-a",
            run_id: runId ?? "run-new",
            after_sequence: afterSequence,
            next_cursor: afterSequence,
            has_more: false,
            events: [],
          };
        });
      vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
        yield {
          event: "run_started",
          event_id: "run-new:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-new", sequence: 1 },
        };
      });

      const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

      await act(async () => {
        await Promise.resolve();
      });
      expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-old", afterSequence: 0, limit: 500 });

      await act(async () => {
        await result.current.start({ message: "new work", execution_mode: "agent" });
      });
      expect(result.current.events.map((event) => event.data.run_id)).toEqual(["run-new"]);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });

      expect(replaySpy).toHaveBeenCalledTimes(2);
      expect(replaySpy).toHaveBeenNthCalledWith(2, "thread-a", { runId: "run-new", afterSequence: 1, limit: 500 });
      expect(result.current.events.map((event) => event.data.run_id)).toEqual(["run-new"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a new live run streaming when a cancelled recovery request resolves late", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      run_id: "run-old",
      status: "running",
    });

    let resolveOldReplay: ((value: Awaited<ReturnType<typeof api.listThreadRunEvents>>) => void) | null = null;
    const oldReplay = new Promise<Awaited<ReturnType<typeof api.listThreadRunEvents>>>((resolve) => {
      resolveOldReplay = resolve;
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockImplementation(async (_threadId, options) => {
      const runId = options?.runId ?? null;
      const afterSequence = options?.afterSequence ?? 0;
      if (runId === "run-old") {
        return oldReplay;
      }
      return {
        thread_id: "thread-a",
        run_id: runId ?? "run-new",
        after_sequence: afterSequence,
        next_cursor: afterSequence,
        has_more: false,
        events: [],
      };
    });

    let releaseLiveRun: (() => void) | null = null;
    const liveRunFinished = new Promise<void>((resolve) => {
      releaseLiveRun = resolve;
    });
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_started",
        event_id: "run-new:000001",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-new", sequence: 1 },
      };
      await liveRunFinished;
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

    await waitFor(() => {
      expect(replaySpy).toHaveBeenCalledWith("thread-a", { runId: "run-old", afterSequence: 0, limit: 500 });
    });

    let startPromise: Promise<unknown> | null = null;
    await act(async () => {
      startPromise = result.current.start({ message: "new work", execution_mode: "agent" });
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.events.map((event) => event.data.run_id)).toEqual(["run-new"]);
    });
    expect(result.current.isStreaming).toBe(true);

    await act(async () => {
      resolveOldReplay?.({
        thread_id: "thread-a",
        run_id: "run-old",
        after_sequence: 0,
        next_cursor: 0,
        has_more: false,
        events: [],
      });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.events.map((event) => event.data.run_id)).toEqual(["run-new"]);
    expect(result.current.isStreaming).toBe(true);

    await act(async () => {
      releaseLiveRun?.();
      await startPromise;
    });
  });

  it("does not pass the old terminal cursor when starting a new completed thread run", async () => {
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          event_id: "run-1:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 1 },
        };
        yield {
          event: "run_completed",
          event_id: "run-1:000002",
          sequence: 2,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 2, status: "completed" },
        };
      })
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          event_id: "run-2:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-2", sequence: 1 },
        };
      });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    await act(async () => {
      await result.current.start({ message: "hello again", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenNthCalledWith(
      2,
      "thread-a",
      { message: "hello again", execution_mode: "agent" },
      expect.any(AbortSignal),
      null,
    );
  });

  it("passes the last received event id only when reconnecting an unterminated thread stream", async () => {
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          event_id: "run-1:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 1 },
        };
      })
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_completed",
          event_id: "run-1:000002",
          sequence: 2,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 2, status: "completed" },
        };
      });

    vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-1",
      after_sequence: 1,
      next_cursor: 1,
      has_more: false,
      events: [],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    await act(async () => {
      await result.current.start({ message: "resume same run", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenNthCalledWith(
      2,
      "thread-a",
      { message: "resume same run", execution_mode: "agent" },
      expect.any(AbortSignal),
      "run-1:000001",
    );
  });

  it("uses durable replay instead of starting a reconnect stream when the cursor has no raw event id", async () => {
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-no-raw-id", sequence: 1 },
        };
      })
      .mockImplementationOnce(async function* () {
        return;
      });
    const replaySpy = vi
      .spyOn(api, "listThreadRunEvents")
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-no-raw-id",
        after_sequence: 1,
        next_cursor: 1,
        has_more: false,
        events: [],
      })
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-no-raw-id",
        after_sequence: 1,
        next_cursor: 2,
        has_more: false,
        events: [
          {
            event: "run_completed",
            event_id: "run-no-raw-id:000002",
            sequence: 2,
            data: { thread_id: "thread-a", run_id: "run-no-raw-id", sequence: 2, status: "completed" },
          },
        ],
      });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    await act(async () => {
      streamResult = await result.current.start({ message: "resume same run", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenNthCalledWith(
      1,
      "thread-a",
      { message: "hello", execution_mode: "agent" },
      expect.any(AbortSignal),
      null,
    );
    expect(streamSpy).toHaveBeenCalledTimes(1);
    expect(replaySpy).toHaveBeenNthCalledWith(2, "thread-a", {
      runId: "run-no-raw-id",
      afterSequence: 1,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "completed", error: null });
  });

  it("starts a new run when a no-raw-id cursor belongs to a settled durable thread state", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-no-raw-settled", sequence: 1 },
        };
      })
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          event_id: "run-new:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-new", sequence: 1 },
        };
      });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-no-raw-settled",
      after_sequence: 1,
      next_cursor: 1,
      has_more: false,
      events: [],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      run_id: "run-no-raw-settled",
      status: "completed",
    });
    replaySpy.mockClear();

    await act(async () => {
      await result.current.start({ message: "new work", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenNthCalledWith(
      2,
      "thread-a",
      { message: "new work", execution_mode: "agent" },
      expect.any(AbortSignal),
      null,
    );
    expect(replaySpy).not.toHaveBeenCalledWith("thread-a", {
      runId: "run-no-raw-settled",
      afterSequence: 1,
      limit: 500,
    });
    expect(result.current.events.map((event) => event.data.run_id)).toEqual(["run-new"]);
  });

  it("uses durable replay for a no-raw-id cursor when durable state is active without a run id", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy.mockImplementationOnce(async function* () {
      yield {
        event: "run_started",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-no-raw-active", sequence: 1 },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-no-raw-active",
      after_sequence: 1,
      next_cursor: 1,
      has_more: false,
      events: [],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper: wrapperWithClient(client) });

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      status: "running",
    });

    await act(async () => {
      await result.current.start({ message: "resume active run", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenCalledTimes(1);
    expect(replaySpy).toHaveBeenNthCalledWith(2, "thread-a", {
      runId: "run-no-raw-active",
      afterSequence: 1,
      limit: 500,
    });
  });

  it("refreshes thread queries after durable-only reconnect replay returns no terminal event", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy.mockImplementationOnce(async function* () {
      yield {
        event: "run_started",
        sequence: 1,
        data: { thread_id: "thread-a", run_id: "run-no-raw-refresh", sequence: 1 },
      };
    });
    const replaySpy = vi.spyOn(api, "listThreadRunEvents").mockResolvedValue({
      thread_id: "thread-a",
      run_id: "run-no-raw-refresh",
      after_sequence: 1,
      next_cursor: 1,
      has_more: false,
      events: [],
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    invalidateSpy.mockClear();
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "resume same run", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenCalledTimes(1);
    expect(replaySpy).toHaveBeenNthCalledWith(2, "thread-a", {
      runId: "run-no-raw-refresh",
      afterSequence: 1,
      limit: 500,
    });
    expect(streamResult).toEqual({ status: "interrupted", error: null });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("uses durable replay when a cursor reconnect stream returns no buffered events", async () => {
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          event_id: "run-1:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 1 },
        };
      })
      .mockImplementationOnce(async function* () {
        return;
      });
    const replaySpy = vi
      .spyOn(api, "listThreadRunEvents")
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-1",
        after_sequence: 1,
        next_cursor: 1,
        has_more: false,
        events: [],
      })
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-1",
        after_sequence: 1,
        next_cursor: 2,
        has_more: false,
        events: [
          {
            event: "run_completed",
            event_id: "run-1:000002",
            sequence: 2,
            data: { thread_id: "thread-a", run_id: "run-1", sequence: 2, status: "completed" },
          },
        ],
      });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    await act(async () => {
      streamResult = await result.current.start({ message: "resume same run", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenNthCalledWith(
      2,
      "thread-a",
      { message: "resume same run", execution_mode: "agent" },
      expect.any(AbortSignal),
      "run-1:000001",
    );
    expect(replaySpy).toHaveBeenNthCalledWith(2, "thread-a", { runId: "run-1", afterSequence: 1, limit: 500 });
    expect(streamResult).toEqual({ status: "completed", error: null });
    expect(result.current.events.map((event) => event.event)).toEqual(["run_completed"]);
  });

  it("uses durable replay when a cursor reconnect stream only returns gateway preparing", async () => {
    const streamSpy = vi.spyOn(api, "streamThreadRunWithSignal");
    streamSpy
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_started",
          event_id: "run-1:000001",
          sequence: 1,
          data: { thread_id: "thread-a", run_id: "run-1", sequence: 1 },
        };
      })
      .mockImplementationOnce(async function* () {
        yield {
          event: "run_preparing",
          data: { thread_id: "thread-a", status: "preparing", phase: "gateway_received" },
        };
      });
    const replaySpy = vi
      .spyOn(api, "listThreadRunEvents")
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-1",
        after_sequence: 1,
        next_cursor: 1,
        has_more: false,
        events: [],
      })
      .mockResolvedValueOnce({
        thread_id: "thread-a",
        run_id: "run-1",
        after_sequence: 1,
        next_cursor: 2,
        has_more: false,
        events: [
          {
            event: "run_completed",
            event_id: "run-1:000002",
            sequence: 2,
            data: { thread_id: "thread-a", run_id: "run-1", sequence: 2, status: "completed" },
          },
        ],
      });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;

    await act(async () => {
      await result.current.start({ message: "hello", execution_mode: "agent" });
    });
    await act(async () => {
      streamResult = await result.current.start({ message: "resume same run", execution_mode: "agent" });
    });

    expect(streamSpy).toHaveBeenNthCalledWith(
      2,
      "thread-a",
      { message: "resume same run", execution_mode: "agent" },
      expect.any(AbortSignal),
      "run-1:000001",
    );
    expect(replaySpy).toHaveBeenNthCalledWith(2, "thread-a", { runId: "run-1", afterSequence: 1, limit: 500 });
    expect(streamResult).toEqual({ status: "completed", error: null });
    expect(result.current.events.map((event) => event.event)).toEqual(["run_preparing", "run_completed"]);
  });

  it("sends an interrupt request before aborting an active stream", async () => {
    let streamAborted = false;
    let releaseStream: (() => void) | null = null;
    const streamBarrier = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    const interruptSpy = vi.spyOn(api, "interruptThreadRun").mockResolvedValue({
      thread_id: "thread-a",
      status: "running",
      title: null,
      summary: null,
      selected_model: null,
      selected_profile: null,
      selected_reasoning_effort: null,
      effective_model: null,
      active_model: null,
      reasoning_effort: null,
      execution_mode: "agent",
      token_usage: {},
      context_window_usage: null,
      approval_policy_summary: null,
      allowed_local_actions: [],
      requires_approval_actions: [],
      restricted_actions: [],
      todo_snapshot: [],
      archived_summaries: [],
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
      runtime_path_roots: [],
      project_context_files: [],
      durable_subagent_job_history: [],
      tool_calls: [],
      recent_tool_activity: [],
      recent_approval_events: [],
      last_error: null,
      queued_followups: [],
    });
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* (_threadId, _body, signal) {
      signal?.addEventListener("abort", () => {
        streamAborted = true;
      });
      yield { event: "run_started", data: { thread_id: "thread-a" } };
      await streamBarrier;
      if (signal?.aborted) {
        return;
      }
      yield { event: "run_completed", data: { thread_id: "thread-a", status: "completed" } };
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let pending: ReturnType<typeof result.current.start>;
    await act(async () => {
      pending = result.current.start({ message: "hello", execution_mode: "agent" });
    });
    await waitFor(() => {
      expect(result.current.isStreaming).toBe(true);
    });

    act(() => {
      result.current.stop();
    });
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.events.at(-1)).toMatchObject({
      event: "run_completed",
      data: { stream_status: "interrupted", status: "interrupted" },
    });
    await waitFor(() => {
      expect(interruptSpy).toHaveBeenCalledWith("thread-a", { reason: "Interrupted from UI" });
    });
    expect(streamAborted).toBe(true);

    await act(async () => {
      releaseStream?.();
      await pending!;
    });
  });

  it("surfaces run_failed events as stream errors", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.spyOn(api, "streamThreadRunWithSignal").mockImplementation(async function* () {
      yield {
        event: "run_failed",
        data: { thread_id: "thread-a", error: "runtime unavailable" },
      };
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    let streamResult;
    await act(async () => {
      streamResult = await result.current.start({ message: "hello", execution_mode: "agent" });
    });

    expect(streamResult).toEqual({ status: "failed", error: "runtime unavailable" });
    expect(result.current.error).toBe("runtime unavailable");
    expect(result.current.events).toHaveLength(1);
    expect(result.current.isStreaming).toBe(false);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("invalidates state after streamed approval resume", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.spyOn(api, "streamThreadApprovalWithSignal").mockImplementation(async function* () {
      yield { event: "approval_resolved", data: { thread_id: "thread-a", request_id: "approval:write_file" } };
      yield { event: "message_opened", data: { message_id: "message-2", role: "ai" } };
      yield { event: "message_delta", data: { message_id: "message-2", delta: "done" } };
      yield { event: "run_completed", data: { thread_id: "thread-a", status: "completed" } };
    });

    const { result } = renderHook(() => useThreadRunStream("thread-a"), { wrapper });
    await act(async () => {
      await result.current.resumeApproval({ approval_context: "approved for this turn" });
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("invalidates state after approval cancel", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.spyOn(api, "cancelApproval").mockResolvedValue({
      thread_id: "thread-a",
      status: "cancelled",
      title: null,
      summary: null,
      selected_model: null,
      selected_profile: null,
      selected_reasoning_effort: null,
      effective_model: null,
      active_model: null,
      reasoning_effort: null,
      execution_mode: "agent",
      token_usage: {},
      approval_policy_summary: "Agent mode allows runtime tool execution. Read-only filesystem actions like list_dir, read_file, and extract_document run without approval; writes, shell execution, and external or otherwise guarded actions still require explicit approval.",
      allowed_local_actions: ["conversation", "filesystem_tools"],
      requires_approval_actions: ["guarded_tool_calls"],
      restricted_actions: [],
      todo_snapshot: [],
      archived_summaries: [],
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
      runtime_path_roots: [],
      project_context_files: [],
      durable_subagent_job_history: [],
      tool_calls: [],
      recent_tool_activity: [],
      recent_approval_events: [],
      last_error: "cancelled",
      queued_followups: [],
    });

    const { result } = renderHook(() => useCancelThreadApproval("thread-a"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("cancelled");
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("loads thread detail by thread id", async () => {
    const detailSpy = vi.spyOn(api, "getThreadDetail").mockResolvedValue({
      thread: {
        thread_id: "thread-a",
        title: "Alpha",
        status: "completed",
        updated_at: "",
        last_user_message_preview: "hello",
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      state: {
        thread_id: "thread-a",
        status: "completed",
        title: "Alpha",
        summary: null,
        selected_model: "openai",
        selected_profile: "default",
        selected_reasoning_effort: "high",
        effective_model: "openai",
        active_model: "openai",
        reasoning_effort: "high",
        execution_mode: "agent",
        token_usage: { input_tokens: 10, output_tokens: 4, total_tokens: 14 },
        approval_policy_summary: "Agent mode allows runtime tool execution. Read-only filesystem actions like list_dir, read_file, and extract_document run without approval; writes, shell execution, and external or otherwise guarded actions still require explicit approval.",
        allowed_local_actions: ["conversation", "filesystem_tools"],
        requires_approval_actions: ["guarded_tool_calls"],
        restricted_actions: [],
        todo_snapshot: [],
        archived_summaries: [],
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
        runtime_path_roots: [],
        project_context_files: [],
        durable_subagent_job_history: [],
        tool_calls: [],
        recent_tool_activity: [],
        recent_approval_events: [],
        last_error: null,
        queued_followups: [],
      },
      messages: [
        {
          message_id: "message-0",
          role: "human",
          content: "hello",
          steps: [],
          content_blocks: [],
          reasoning: null,
          tool_calls: [],
          tool_call_id: null,
          name: null,
          status: null,
          stream_status: null,
          artifact_refs: [],
          approval: null,
        },
      ],
      message_window: {
        total: 1,
        offset: 0,
        limit: 120,
        returned: 1,
        has_more_before: false,
        has_more_after: false,
        truncated: false,
        start_message_id: "message-0",
        end_message_id: "message-0",
      },
      pending_approval: null,
      stream_capabilities: {
        supports_message_delta: true,
        supports_reasoning_delta: true,
        supports_structured_events: true,
      },
    });

    const { result } = renderHook(() => useThreadDetail("thread-a"), { wrapper });
    await waitFor(() => {
      expect(result.current.data?.thread.thread_id).toBe("thread-a");
    });
    expect(detailSpy).toHaveBeenCalledWith("thread-a", {
      messageOffset: null,
      messageLimit: 120,
      stateScope: "chat",
      stateSource: "auto",
    });
  });

  it("keeps cached thread detail fresh long enough for fast thread reselects", async () => {
    const detailSpy = vi.spyOn(api, "getThreadDetail").mockResolvedValue({
      thread: {
        thread_id: "thread-a",
        title: "Alpha",
        status: "completed",
        updated_at: "",
        last_user_message_preview: "hello",
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      state: {
        thread_id: "thread-a",
        status: "completed",
        title: "Alpha",
        summary: null,
        selected_model: "openai",
        selected_profile: "default",
        selected_reasoning_effort: "high",
        effective_model: "openai",
        active_model: "openai",
        reasoning_effort: "high",
        execution_mode: "agent",
        token_usage: { input_tokens: 10, output_tokens: 4, total_tokens: 14 },
        approval_policy_summary: "Agent mode allows runtime tool execution.",
        allowed_local_actions: [],
        requires_approval_actions: [],
        restricted_actions: [],
        todo_snapshot: [],
        archived_summaries: [],
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
        runtime_path_roots: [],
        project_context_files: [],
        durable_subagent_job_history: [],
        tool_calls: [],
        recent_tool_activity: [],
        recent_approval_events: [],
        last_error: null,
        queued_followups: [],
      },
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
      pending_approval: null,
      stream_capabilities: {
        supports_message_delta: true,
        supports_reasoning_delta: true,
        supports_structured_events: true,
      },
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const localWrapper = ({ children }: Readonly<{ children: React.ReactNode }>) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(() => useThreadDetail("thread-a"), { wrapper: localWrapper });
    await waitFor(() => {
      expect(result.current.data?.thread.thread_id).toBe("thread-a");
    });
    unmount();

    const { result: remounted } = renderHook(() => useThreadDetail("thread-a"), { wrapper: localWrapper });
    expect(remounted.current.data?.thread.thread_id).toBe("thread-a");
    expect(detailSpy).toHaveBeenCalledTimes(1);
  });

  it("keeps polling thread detail while cached state is approval-paused", () => {
    expect(
      getThreadDetailRefetchInterval({
        state: { status: "awaiting_approval" },
        thread: { status: "completed" },
      }),
    ).toBe(1500);
  });

  it("can gate thread state fallback requests", async () => {
    const stateSpy = vi.spyOn(api, "getThreadState").mockResolvedValue({
      thread_id: "thread-a",
      status: "completed",
      title: "Alpha",
      summary: null,
      selected_model: "openai",
      selected_profile: "default",
      selected_reasoning_effort: "high",
      effective_model: "openai",
      active_model: "openai",
      reasoning_effort: "high",
      execution_mode: "agent",
      token_usage: { total_tokens: 14 },
      approval_policy_summary: "Agent mode allows runtime tool execution.",
      allowed_local_actions: [],
      requires_approval_actions: [],
      restricted_actions: [],
      todo_snapshot: [],
      archived_summaries: [],
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
      runtime_path_roots: [],
      project_context_files: [],
      durable_subagent_job_history: [],
      tool_calls: [],
      recent_tool_activity: [],
      recent_approval_events: [],
      last_error: null,
      queued_followups: [],
    });

    renderHook(() => useThreadState("thread-a", { enabled: false }), { wrapper });
    expect(stateSpy).not.toHaveBeenCalled();
  });

  it("uses event-log projected state for an already running cached run", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      run_id: "run-reconnect",
      status: "running",
      title: "Alpha",
      summary: null,
      active_model: "openai",
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
      subagent_tasks: [],
      process_sessions: [],
      runtime_path_roots: [],
      project_context_files: [],
      durable_subagent_job_history: [],
      tool_calls: [],
      recent_tool_activity: [],
      recent_approval_events: [],
      last_error: null,
      queued_followups: [],
    });
    const stateSpy = vi.spyOn(api, "getThreadState").mockResolvedValue({
      ...(client.getQueryData(["thread-state", "thread-a"]) as object),
      status: "running",
    } as Awaited<ReturnType<typeof api.getThreadState>>);

    const { result } = renderHook(() => useThreadState("thread-a"), { wrapper: wrapperWithClient(client) });

    await waitFor(() => {
      expect(result.current.data?.run_id).toBe("run-reconnect");
    });
    expect(stateSpy).toHaveBeenCalledWith("thread-a", {
      stateSource: "event_log",
      runId: "run-reconnect",
      stateScope: "chat",
    });
  });

  it("uses event-log projected state for a cached clarification-paused run", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["thread-state", "thread-a"], {
      thread_id: "thread-a",
      run_id: "run-clarification",
      status: "awaiting_clarification",
      title: "Alpha",
      summary: null,
      active_model: "openai",
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
      subagent_tasks: [],
      process_sessions: [],
      runtime_path_roots: [],
      project_context_files: [],
      durable_subagent_job_history: [],
      tool_calls: [],
      recent_tool_activity: [],
      recent_approval_events: [],
      last_error: null,
      queued_followups: [],
    });
    const stateSpy = vi.spyOn(api, "getThreadState").mockResolvedValue({
      ...(client.getQueryData(["thread-state", "thread-a"]) as object),
      status: "awaiting_clarification",
    } as Awaited<ReturnType<typeof api.getThreadState>>);

    const { result } = renderHook(() => useThreadState("thread-a"), { wrapper: wrapperWithClient(client) });

    await waitFor(() => {
      expect(result.current.data?.run_id).toBe("run-clarification");
    });
    expect(stateSpy).toHaveBeenCalledWith("thread-a", {
      stateSource: "event_log",
      runId: "run-clarification",
      stateScope: "chat",
    });
  });

  it("invalidates thread queries after settings update", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.spyOn(api, "updateThreadSettings").mockResolvedValue({
      thread_id: "thread-a",
      execution_mode: "full_access",
      selected_model: "minimax",
      selected_profile: "coder",
      selected_reasoning_effort: "xhigh",
      runtime_path_roots: [],
    });

    const { result } = renderHook(() => useUpdateThreadSettings("thread-a"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        execution_mode: "full_access",
        selected_model: "minimax",
      });
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-settings", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("invalidates durable thread views after queued follow-up mutations", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    vi.spyOn(api, "enqueueThreadFollowup").mockResolvedValue({
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
    });
    vi.spyOn(api, "updateThreadFollowup").mockResolvedValue({
      queue_id: "followup-1",
      thread_id: "thread-a",
      message: "guide the next turn",
      mode: "guidance",
      status: "queued",
      created_at: "2026-05-25T00:00:00.000Z",
      updated_at: "2026-05-25T00:00:01.000Z",
      uploaded_filenames: [],
      uploaded_file_refs: [],
      promoted_capabilities: [],
    });

    const { result: enqueueResult } = renderHook(() => useEnqueueThreadFollowup("thread-a"), { wrapper });
    const { result: updateResult } = renderHook(() => useUpdateThreadFollowup("thread-a"), { wrapper });
    await act(async () => {
      await enqueueResult.current.mutateAsync({
        message: "continue after this run",
        mode: "followup",
        execution_mode: "agent",
      });
      await updateResult.current.mutateAsync({
        queueId: "followup-1",
        body: { message: "guide the next turn", mode: "guidance" },
      });
    });

    expect(api.enqueueThreadFollowup).toHaveBeenCalledWith("thread-a", {
      message: "continue after this run",
      mode: "followup",
      execution_mode: "agent",
    });
    expect(api.updateThreadFollowup).toHaveBeenCalledWith("thread-a", "followup-1", {
      message: "guide the next turn",
      mode: "guidance",
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
  });

  it("supports explicit thread overrides when updating settings before the active thread hook catches up", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const updateSpy = vi.spyOn(api, "updateThreadSettings").mockResolvedValue({
      thread_id: "thread-new",
      execution_mode: "agent",
      selected_model: null,
      selected_profile: null,
      selected_reasoning_effort: null,
      runtime_path_roots: [],
    });

    const { result } = renderHook(() => useUpdateThreadSettings(null), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        body: {
          execution_mode: "agent",
          selected_model: null,
        },
        threadIdOverride: "thread-new",
      });
    });

    expect(updateSpy).toHaveBeenCalledWith("thread-new", {
      execution_mode: "agent",
      selected_model: null,
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-settings", "thread-new"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-new"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-new"] });
  });

  it("optimistically removes deleted thread from the list and clears queries after delete", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const removeSpy = vi.spyOn(QueryClient.prototype, "removeQueries");
    vi.spyOn(api, "deleteThread").mockResolvedValue({
      thread_id: "thread-a",
      deleted: true,
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["threads"], [
      {
        thread_id: "thread-a",
        title: "Alpha",
        status: "ready",
        updated_at: "",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      {
        thread_id: "thread-b",
        title: "Beta",
        status: "ready",
        updated_at: "",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ]);
    function localWrapper({ children }: Readonly<{ children: React.ReactNode }>) {
      return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    }

    const { result } = renderHook(() => useDeleteThread(), { wrapper: localWrapper });
    await act(async () => {
      await result.current.mutateAsync("thread-a");
    });

    expect(client.getQueryData<Array<{ thread_id: string }>>(["threads"])?.map((thread) => thread.thread_id)).toEqual([
      "thread-b",
    ]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["threads"] });
    expect(removeSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-a"] });
    expect(removeSpy).toHaveBeenCalledWith({ queryKey: ["thread-detail", "thread-a"] });
    expect(removeSpy).toHaveBeenCalledWith({ queryKey: ["thread-settings", "thread-a"] });
    expect(removeSpy).toHaveBeenCalledWith({ queryKey: ["uploads", "thread-a"] });
  });
});
