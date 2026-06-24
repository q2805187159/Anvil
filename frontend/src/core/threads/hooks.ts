"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import type {
  ApprovalResumeRequest,
  EvaluationThreadReportView,
  MessageEditResendRequest,
  QueuedFollowUpCreateRequest,
  QueuedFollowUpUpdateRequest,
  ProcessResizeRequest,
  ProcessStdinRequest,
  ScheduledTaskAdminResponse,
  ScheduledTaskAutomationStatusResponse,
  RunRequestBody,
  RunStreamEvent,
  ThreadDetailView,
  ThreadSettingsUpdateRequest,
  ThreadStateView,
  ThreadView,
  UserInteractionResumeRequest,
} from "@/src/core/contracts";
import { ApiError } from "@/src/core/api/client";

import { approveThread, cancelApproval, cancelSubagentTask, closeProcessStdin, createThread, deleteThread, deleteThreadFollowup, editLatestMessageAndResend, enqueueThreadFollowup, getProcessCapabilities, getProcessLog, getScheduledTaskAutomation, getThreadDetail, getThreadEvaluationReport, getThreadSettings, getThreadState, interruptProcessSession, interruptThreadRun, killProcessSession, listScheduledTasks, listThreadRunEvents, listThreads, pauseScheduledTask, popNextThreadFollowup, resizeProcessSession, resumeScheduledTask, runScheduledTask, runScheduledTaskAutomation, streamEditLatestMessageAndResendWithSignal, streamThreadApprovalWithSignal, streamThreadRunWithSignal, streamThreadUserInteractionWithSignal, updateThreadFollowup, updateThreadSettings, waitProcessSession, waitSubagentTask, writeProcessStdin } from "./api";
import { sortThreadsByRecency, threadActivityAtMillis } from "./recency";

type ClientRunStreamEvent = RunStreamEvent & {
  receivedAt?: number;
};

type ThreadStreamSnapshot = {
  events: ClientRunStreamEvent[];
  isStreaming: boolean;
  error: string | null;
  lastRunId: string | null;
  lastSequence: number | null;
  lastTerminalEventId: string | null;
  hasTerminalEvent: boolean;
};

type ThreadRunStreamResult = {
  status: "completed" | "failed" | "aborted" | "interrupted";
  error?: string | null;
};

const MAX_STREAM_EVENTS = 500;
const RUN_RECOVERY_REPLAY_INTERVAL_MS = 2_000;
const THREAD_LIST_STALE_TIME_MS = 10_000;
const THREAD_LIST_POST_RUN_REFRESH_MS = 6_000;
const THREAD_LIST_POST_RUN_REFRESH_INTERVAL_MS = 1_500;
const THREAD_STATE_STALE_TIME_MS = 15_000;
const THREAD_DETAIL_STALE_TIME_MS = 30_000;
const THREAD_DETAIL_GC_TIME_MS = 10 * 60_000;
export const THREAD_DETAIL_MESSAGE_WINDOW_PAGE_SIZE = 120;

type QueryGateOptions = {
  enabled?: boolean;
  messageOffset?: number | null;
  messageLimit?: number;
  stateScope?: "chat" | "full";
  stateSource?: "snapshot" | "event_log" | "auto";
};

function threadDetailQueryKey(
  threadId: string | null,
  messageOffset: number | null,
  messageLimit: number,
  stateScope: "chat" | "full",
  stateSource: "snapshot" | "event_log" | "auto",
) {
  return ["thread-detail", threadId, { messageOffset, messageLimit, stateScope, stateSource }] as const;
}

function emptyThreadStream(): ThreadStreamSnapshot {
  return {
    events: [],
    isStreaming: false,
    error: null,
    lastRunId: null,
    lastSequence: null,
    lastTerminalEventId: null,
    hasTerminalEvent: false,
  };
}

function applyRunCompletedCacheUpdate(
  queryClient: ReturnType<typeof useQueryClient>,
  effectiveThreadId: string,
  data: Record<string, unknown>,
) {
  const state = data.state;
  if (state && typeof state === "object") {
    queryClient.setQueryData(["thread-state", effectiveThreadId], state as ThreadStateView);
  }
  const thread = data.thread;
  const completedThread = thread && typeof thread === "object" ? (thread as ThreadView) : null;
  if (completedThread) {
    queryClient.setQueryData<ThreadView[] | undefined>(["threads"], (current) => {
      if (!current?.length) {
        return [completedThread];
      }
      return sortThreadsByRecency(
        current.map((item) =>
          item.thread_id === effectiveThreadId ? mergeThreadListItem(item, completedThread, { settleStatus: true }) : item,
        ),
      );
    });
  }
  if (state && typeof state === "object") {
    queryClient.setQueriesData<ThreadDetailView | undefined>(
      { queryKey: ["thread-detail", effectiveThreadId] },
      (current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          thread: completedThread ? mergeThreadListItem(current.thread, completedThread, { settleStatus: true }) : current.thread,
          state: state as ThreadStateView,
        };
      },
    );
  }
}

function mergeThreadListItem(
  current: ThreadView,
  incoming: ThreadView,
  options: { settleStatus?: boolean } = {},
): ThreadView {
  const currentActivity = threadActivityAtMillis(current);
  const incomingActivity = threadActivityAtMillis(incoming);
  if (incomingActivity >= currentActivity) {
    return { ...current, ...incoming };
  }
  const preserveCurrentActiveState = !options.settleStatus && isRecoverableThreadStatus(current.status);
  return {
    ...current,
    ...incoming,
    updated_at: current.updated_at,
    last_message_at: current.last_message_at,
    last_user_message_preview: current.last_user_message_preview ?? incoming.last_user_message_preview,
    status: preserveCurrentActiveState ? current.status : incoming.status,
    has_pending_approval: preserveCurrentActiveState ? current.has_pending_approval : incoming.has_pending_approval,
    has_active_subagent_tasks: preserveCurrentActiveState
      ? current.has_active_subagent_tasks
      : incoming.has_active_subagent_tasks,
  };
}

function applyThreadActivityCacheUpdate(
  queryClient: ReturnType<typeof useQueryClient>,
  effectiveThreadId: string,
  message?: string | null,
) {
  queryClient.setQueryData<ThreadView[] | undefined>(["threads"], (current) => {
    if (!current?.length) {
      return current;
    }
    let matched = false;
    const updatedAt = new Date().toISOString();
    const preview = typeof message === "string" && message.trim() ? message.trim().slice(0, 120) : null;
    const next = current.map((item) => {
      if (item.thread_id !== effectiveThreadId) {
        return item;
      }
      matched = true;
      return {
        ...item,
        status: "running",
        updated_at: updatedAt,
        last_message_at: updatedAt,
        last_user_message_preview: preview ?? item.last_user_message_preview,
      };
    });
    return matched ? sortThreadsByRecency(next) : current;
  });
}

function mergeFetchedThreadList(current: ThreadView[] | undefined, incoming: ThreadView[]): ThreadView[] {
  if (!current?.length) {
    return sortThreadsByRecency(incoming);
  }
  const currentByThreadId = new Map(current.map((thread) => [thread.thread_id, thread]));
  return sortThreadsByRecency(
    incoming.map((thread) => {
      const existing = currentByThreadId.get(thread.thread_id);
      return existing ? mergeThreadListItem(existing, thread) : thread;
    }),
  );
}

export function hasRecoverableThread(threads: ThreadView[] | undefined): boolean {
  return Boolean(threads?.some((thread) => isRecoverableThreadStatus(thread.status)));
}

export function useThreads() {
  const queryClient = useQueryClient();
  const [postRunRefreshUntil, setPostRunRefreshUntil] = useState(0);
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    function handlePostRunRefresh() {
      setPostRunRefreshUntil(Date.now() + THREAD_LIST_POST_RUN_REFRESH_MS);
    }
    window.addEventListener("anvil:thread-list-post-run-refresh", handlePostRunRefresh);
    return () => window.removeEventListener("anvil:thread-list-post-run-refresh", handlePostRunRefresh);
  }, []);
  return useQuery({
    queryKey: ["threads"],
    queryFn: async () => mergeFetchedThreadList(queryClient.getQueryData<ThreadView[]>(["threads"]), await listThreads()),
    select: sortThreadsByRecency,
    staleTime: THREAD_LIST_STALE_TIME_MS,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      const threads = query.state.data;
      if (Array.isArray(threads) && hasRecoverableThread(threads)) {
        return 2000;
      }
      return Date.now() < postRunRefreshUntil ? THREAD_LIST_POST_RUN_REFRESH_INTERVAL_MS : false;
    },
  });
}

export function useCreateThread() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      input?:
        | string
        | {
            threadId?: string;
            workspaceRoot?: string | null;
          },
    ) => {
      if (typeof input === "string" || input === undefined) {
        return createThread(input);
      }
      return createThread(input.threadId, input.workspaceRoot);
    },
    onSuccess: async (created) => {
      queryClient.setQueryData<ThreadView[] | undefined>(["threads"], (current) => {
        if (!current?.length) {
          return [created];
        }
        const withoutExisting = current.filter((thread) => thread.thread_id !== created.thread_id);
        return sortThreadsByRecency([created, ...withoutExisting]);
      });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (threadId: string) => deleteThread(threadId),
    onMutate: async (threadId) => {
      await queryClient.cancelQueries({ queryKey: ["threads"] });
      const previousThreads = queryClient.getQueryData<ThreadView[]>(["threads"]);
      queryClient.setQueryData<ThreadView[] | undefined>(["threads"], (current) =>
        current?.filter((thread) => thread.thread_id !== threadId),
      );
      return { previousThreads };
    },
    onError: (_error, _threadId, context) => {
      if (context?.previousThreads) {
        queryClient.setQueryData(["threads"], context.previousThreads);
      }
    },
    onSuccess: async (deleted) => {
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      queryClient.removeQueries({ queryKey: ["thread-state", deleted.thread_id] });
      queryClient.removeQueries({ queryKey: ["thread-detail", deleted.thread_id] });
      queryClient.removeQueries({ queryKey: ["thread-evaluation-report", deleted.thread_id] });
      queryClient.removeQueries({ queryKey: ["thread-settings", deleted.thread_id] });
      queryClient.removeQueries({ queryKey: ["uploads", deleted.thread_id] });
    },
  });
}

export function useThreadState(threadId: string | null, options: QueryGateOptions = {}) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["thread-state", threadId],
    queryFn: () => {
      const current = queryClient.getQueryData<ThreadStateView>(["thread-state", threadId]);
      const runId = isRecoverableThreadStatus(current?.status) ? current?.run_id : null;
      return getThreadState(
        threadId!,
        runId
          ? {
              stateSource: "event_log",
              runId,
              stateScope: "chat",
            }
          : undefined,
      );
    },
    enabled: Boolean(threadId) && (options.enabled ?? true),
    staleTime: THREAD_STATE_STALE_TIME_MS,
    refetchOnMount: (query) => isRecoverableThreadStatus(query.state.data?.status) ? "always" : false,
    refetchOnWindowFocus: false,
  });
}

export function getThreadDetailRefetchInterval(
  detail:
    | {
        state?: { status?: ThreadStateView["status"] | null } | null;
        thread?: { status?: ThreadView["status"] | null } | null;
      }
    | null
    | undefined,
): 1500 | false {
  return isRecoverableThreadStatus(detail?.state?.status) || isRecoverableThreadStatus(detail?.thread?.status)
    ? 1500
    : false;
}

export function useThreadDetail(threadId: string | null, options: QueryGateOptions = {}) {
  const messageOffset = typeof options.messageOffset === "number" ? options.messageOffset : null;
  const messageLimit = options.messageLimit ?? THREAD_DETAIL_MESSAGE_WINDOW_PAGE_SIZE;
  const stateScope = options.stateScope ?? "chat";
  const stateSource = options.stateSource ?? "auto";
  return useQuery({
    queryKey: threadDetailQueryKey(threadId, messageOffset, messageLimit, stateScope, stateSource),
    queryFn: () => getThreadDetail(threadId!, { messageOffset, messageLimit, stateScope, stateSource }),
    enabled: Boolean(threadId) && (options.enabled ?? true),
    staleTime: THREAD_DETAIL_STALE_TIME_MS,
    gcTime: THREAD_DETAIL_GC_TIME_MS,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      return getThreadDetailRefetchInterval(query.state.data);
    },
  });
}

export function useThreadEvaluationReport(threadId: string | null, options: QueryGateOptions = {}) {
  return useQuery<EvaluationThreadReportView>({
    queryKey: ["thread-evaluation-report", threadId],
    queryFn: () => getThreadEvaluationReport(threadId!),
    enabled: Boolean(threadId) && (options.enabled ?? true),
    staleTime: THREAD_STATE_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useThreadMessageWindowLoader(threadId: string | null) {
  const queryClient = useQueryClient();
  return async ({
    messageOffset,
    messageLimit = THREAD_DETAIL_MESSAGE_WINDOW_PAGE_SIZE,
  }: {
    messageOffset: number;
    messageLimit?: number;
  }) => {
    if (!threadId) {
      throw new Error("thread_id_required");
    }
    return queryClient.fetchQuery({
      queryKey: threadDetailQueryKey(threadId, messageOffset, messageLimit, "chat", "auto"),
      queryFn: () => getThreadDetail(threadId, { messageOffset, messageLimit, stateScope: "chat", stateSource: "auto" }),
      staleTime: THREAD_DETAIL_STALE_TIME_MS,
      gcTime: THREAD_DETAIL_GC_TIME_MS,
    });
  };
}

export function useThreadSettings(threadId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["thread-settings", threadId],
    queryFn: () => getThreadSettings(threadId!),
    enabled: Boolean(threadId) && (options.enabled ?? true),
    staleTime: THREAD_STATE_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useThreadRunStream(threadId: string | null) {
  const queryClient = useQueryClient();
  const [streamsByThread, setStreamsByThread] = useState<Record<string, ThreadStreamSnapshot>>({});
  const streamsRef = useRef<Record<string, ThreadStreamSnapshot>>({});
  const controllersRef = useRef<Map<string, AbortController>>(new Map());
  const recoveryInFlightRef = useRef<Set<string>>(new Set());
  const recoveryTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const recoveryGenerationRef = useRef<Map<string, number>>(new Map());

  function updateThreadStream(
    effectiveThreadId: string,
    updater: (current: ThreadStreamSnapshot) => ThreadStreamSnapshot,
  ) {
    const current = streamsRef.current;
    const previous = current[effectiveThreadId] ?? emptyThreadStream();
    const next = {
      ...current,
      [effectiveThreadId]: updater(previous),
    };
    streamsRef.current = next;
    setStreamsByThread(next);
  }

  function appendThreadEvent(effectiveThreadId: string, event: ClientRunStreamEvent) {
    updateThreadStream(effectiveThreadId, (current) => {
      const cursor = advanceRunCursor({ runId: current.lastRunId, sequence: current.lastSequence }, event);
      return {
        ...current,
        events: appendDedupedRunEvents(current.events, [event]).slice(-MAX_STREAM_EVENTS),
        lastRunId: cursor.runId,
        lastSequence: cursor.sequence,
        lastTerminalEventId: terminalEventId(event) ?? current.lastTerminalEventId,
        hasTerminalEvent: current.hasTerminalEvent || isTerminalRunEvent(event),
      };
    });
  }

  async function replayMissingRunEvents(
    effectiveThreadId: string,
    runId: string | null,
    afterSequence: number | null,
    recoveryGeneration?: number,
  ): Promise<ThreadRunStreamResult | null> {
    if (!runId || typeof afterSequence !== "number") {
      return null;
    }
    let terminalResult: ThreadRunStreamResult | null = null;
    try {
      let cursor = afterSequence;
      while (true) {
        const replay = await listThreadRunEvents(effectiveThreadId, {
          runId,
          afterSequence: cursor,
          limit: MAX_STREAM_EVENTS,
        });
        if (
          typeof recoveryGeneration === "number" &&
          (recoveryGenerationRef.current.get(effectiveThreadId) ?? 0) !== recoveryGeneration
        ) {
          return null;
        }
        const currentRunId = streamsRef.current[effectiveThreadId]?.lastRunId;
        if (currentRunId && currentRunId !== runId) {
          return null;
        }
        if (replay.run_id && replay.run_id !== runId) {
          return null;
        }
        if (replay.thread_id !== effectiveThreadId) {
          return null;
        }
        if (typeof replay.after_sequence === "number" && replay.after_sequence !== cursor) {
          return null;
        }
        const replayedEvents = replay.events.map((event) => ({ ...event, receivedAt: Date.now() }));
        if (replayedEvents.some((event) => event.data.thread_id !== effectiveThreadId)) {
          return null;
        }
        if (replayedEvents.some((event) => runIdFromEvent(event) !== runId)) {
          return null;
        }
        const replayEventsAfterCursor = replayedEvents.filter((event) => {
          const sequence = sequenceFromEvent(event);
          return typeof sequence === "number" && sequence > cursor;
        });
        if (replayEventsAfterCursor.length > 0) {
          const terminalEvent = replayEventsAfterCursor.find(isTerminalRunEvent);
          updateThreadStream(effectiveThreadId, (current) => {
            const replayRunId = replay.run_id ?? runId;
            const replayCursor = replayEventsAfterCursor.reduce<{ runId: string | null; sequence: number | null }>(
              (cursor, event) => (runIdFromEvent(event) === replayRunId ? advanceRunCursor(cursor, event) : cursor),
              { runId: replayRunId, sequence: current.lastSequence },
            );
            const nextSequence = Math.max(current.lastSequence ?? 0, replayCursor.sequence ?? 0, replay.next_cursor);
            return {
              ...current,
              events: appendDedupedRunEvents(current.events, replayEventsAfterCursor).slice(-MAX_STREAM_EVENTS),
              lastRunId: replayRunId,
              lastSequence: nextSequence,
              lastTerminalEventId: terminalEvent ? terminalEventId(terminalEvent) : current.lastTerminalEventId,
              hasTerminalEvent: current.hasTerminalEvent || Boolean(terminalEvent),
            };
          });
          if (terminalEvent?.event === "run_completed") {
            applyRunCompletedCacheUpdate(queryClient, effectiveThreadId, terminalEvent.data);
            terminalResult = { status: "completed", error: null };
          } else if (terminalEvent?.event === "run_failed") {
            const message =
              typeof terminalEvent.data.error === "string" && terminalEvent.data.error
                ? terminalEvent.data.error
                : "Run failed";
            updateThreadStream(effectiveThreadId, (current) => ({
              ...current,
              error: message,
              isStreaming: false,
            }));
            terminalResult = { status: "failed", error: message };
          }
        }
        if (replay.next_cursor <= cursor) {
          break;
        }
        cursor = replay.next_cursor;
        if (!replay.has_more || terminalResult) {
          break;
        }
      }
    } catch {
      // Replay is best-effort; the thread snapshot refresh below remains the durable fallback.
    }
    return terminalResult;
  }

  function settleTerminalThreadStream(effectiveThreadId: string) {
    updateThreadStream(effectiveThreadId, (current) => ({
      ...current,
      error: current.error,
      isStreaming: false,
    }));
  }

  function refreshThreadRunQueries(effectiveThreadId: string) {
    const refresh = Promise.all([
      queryClient.invalidateQueries({ queryKey: ["threads"] }),
      queryClient.invalidateQueries({ queryKey: ["thread-state", effectiveThreadId] }),
      queryClient.invalidateQueries({ queryKey: ["thread-detail", effectiveThreadId] }),
      queryClient.invalidateQueries({ queryKey: ["thread-evaluation-report", effectiveThreadId] }),
    ]);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new Event("anvil:thread-list-post-run-refresh"));
    }
    return refresh;
  }

  function clearRecoveryTimer(recoveryKey: string) {
    const timer = recoveryTimersRef.current.get(recoveryKey);
    if (timer) {
      clearTimeout(timer);
      recoveryTimersRef.current.delete(recoveryKey);
    }
  }

  function cancelThreadRecovery(effectiveThreadId: string) {
    recoveryGenerationRef.current.set(effectiveThreadId, (recoveryGenerationRef.current.get(effectiveThreadId) ?? 0) + 1);
    for (const recoveryKey of Array.from(recoveryTimersRef.current.keys())) {
      if (recoveryKey.startsWith(`${effectiveThreadId}:`)) {
        clearRecoveryTimer(recoveryKey);
      }
    }
    for (const recoveryKey of Array.from(recoveryInFlightRef.current)) {
      if (recoveryKey.startsWith(`${effectiveThreadId}:`)) {
        recoveryInFlightRef.current.delete(recoveryKey);
      }
    }
  }

  function scheduleRecoveredRunReplay(effectiveThreadId: string, runId: string, afterSequence: number) {
    const recoveryKey = `${effectiveThreadId}:${runId}`;
    clearRecoveryTimer(recoveryKey);
    const timer = setTimeout(() => {
      recoveryTimersRef.current.delete(recoveryKey);
      void recoverRunningThreadEvents(effectiveThreadId, runId, afterSequence);
    }, RUN_RECOVERY_REPLAY_INTERVAL_MS);
    recoveryTimersRef.current.set(recoveryKey, timer);
  }

  async function recoverRunningThreadEvents(
    effectiveThreadId: string,
    runId: string,
    afterSequence: number,
  ) {
    const recoveryKey = `${effectiveThreadId}:${runId}`;
    if (recoveryInFlightRef.current.has(recoveryKey)) {
      return;
    }
    if (streamsRef.current[effectiveThreadId]?.hasTerminalEvent) {
      clearRecoveryTimer(recoveryKey);
      return;
    }
    recoveryInFlightRef.current.add(recoveryKey);
    const recoveryGeneration = recoveryGenerationRef.current.get(effectiveThreadId) ?? 0;
    updateThreadStream(effectiveThreadId, (current) => ({
      ...current,
      isStreaming: true,
      lastRunId: runId,
      lastSequence: current.lastSequence ?? afterSequence,
    }));
    try {
      const terminal = await replayMissingRunEvents(effectiveThreadId, runId, afterSequence, recoveryGeneration);
      if ((recoveryGenerationRef.current.get(effectiveThreadId) ?? 0) !== recoveryGeneration) {
        return;
      }
      const current = streamsRef.current[effectiveThreadId] ?? emptyThreadStream();
      if (terminal || current.hasTerminalEvent) {
        clearRecoveryTimer(recoveryKey);
        void refreshThreadRunQueries(effectiveThreadId);
        return;
      }
      const state = queryClient.getQueryData<ThreadStateView>(["thread-state", effectiveThreadId]);
      if (isRecoverableThreadStatus(state?.status) && (!state?.run_id || state.run_id === runId)) {
        scheduleRecoveredRunReplay(effectiveThreadId, runId, current.lastSequence ?? afterSequence);
      } else {
        clearRecoveryTimer(recoveryKey);
      }
    } finally {
      recoveryInFlightRef.current.delete(recoveryKey);
      if ((recoveryGenerationRef.current.get(effectiveThreadId) ?? 0) !== recoveryGeneration) {
        return;
      }
      const current = streamsRef.current[effectiveThreadId];
      if (current?.lastRunId && current.lastRunId !== runId) {
        return;
      }
      if (current?.hasTerminalEvent || !recoveryTimersRef.current.has(recoveryKey)) {
        updateThreadStream(effectiveThreadId, (snapshot) => ({
          ...snapshot,
          isStreaming: false,
        }));
      }
    }
  }

  async function consumeStream(
    streamFactory: (signal: AbortSignal) => AsyncGenerator<RunStreamEvent>,
    effectiveThreadId: string,
    reconnectCursor: { runId: string | null; sequence: number | null } | null = null,
  ): Promise<ThreadRunStreamResult> {
    controllersRef.current.get(effectiveThreadId)?.abort();
    cancelThreadRecovery(effectiveThreadId);
    const nextController = new AbortController();
    controllersRef.current.set(effectiveThreadId, nextController);
    updateThreadStream(effectiveThreadId, () => ({
      events: [],
      error: null,
      isStreaming: true,
      lastRunId: null,
      lastSequence: null,
      lastTerminalEventId: null,
      hasTerminalEvent: false,
    }));
    let terminalEventSeen = false;
    let result: ThreadRunStreamResult = { status: "interrupted", error: null };
    let lastRunId: string | null = null;
    let lastSequence: number | null = null;
    let streamCursorSeen = false;
    try {
      for await (const event of streamFactory(nextController.signal)) {
        if (terminalEventSeen) {
          continue;
        }
        const eventRunId = runIdFromEvent(event);
        const eventSequence = sequenceFromEvent(event);
        if (eventRunId && typeof eventSequence === "number") {
          streamCursorSeen = true;
        }
        ({ runId: lastRunId, sequence: lastSequence } = advanceRunCursor(
          { runId: lastRunId, sequence: lastSequence },
          event,
        ));
        const knownSystemVersion =
          typeof event.data.known_system_version === "number"
            ? event.data.known_system_version
            : 0;
        const currentSystemVersion = (queryClient.getQueryData(["system-version"]) as number | undefined) ?? 0;
        if (knownSystemVersion > currentSystemVersion) {
          queryClient.setQueryData(["system-version"], knownSystemVersion);
          await queryClient.invalidateQueries({ queryKey: ["skills"] });
          await queryClient.invalidateQueries({ queryKey: ["extensions"] });
          await queryClient.invalidateQueries({ queryKey: ["threads"] });
        }
        if (event.event === "run_failed") {
          const message =
            typeof event.data.error === "string" && event.data.error
              ? event.data.error
              : "Run failed";
          appendThreadEvent(effectiveThreadId, { ...event, receivedAt: Date.now() });
          updateThreadStream(effectiveThreadId, (current) => ({
            ...current,
            error: message,
            isStreaming: false,
          }));
          void refreshThreadRunQueries(effectiveThreadId);
          result = { status: "failed", error: message };
          return result;
        }
        if (event.event === "run_completed") {
          terminalEventSeen = true;
          appendThreadEvent(effectiveThreadId, { ...event, receivedAt: Date.now() });
          settleTerminalThreadStream(effectiveThreadId);
          applyRunCompletedCacheUpdate(queryClient, effectiveThreadId, event.data);
          void refreshThreadRunQueries(effectiveThreadId);
          result = { status: "completed", error: null };
          continue;
        }
        appendThreadEvent(effectiveThreadId, { ...event, receivedAt: Date.now() });
      }
      if (!terminalEventSeen) {
        if (!streamCursorSeen) {
          lastRunId = lastRunId ?? reconnectCursor?.runId ?? null;
          lastSequence = lastSequence ?? reconnectCursor?.sequence ?? null;
        }
        result = (await replayMissingRunEvents(effectiveThreadId, lastRunId, lastSequence)) ?? result;
        void refreshThreadRunQueries(effectiveThreadId);
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        if (controllersRef.current.get(effectiveThreadId) === nextController) {
          updateThreadStream(effectiveThreadId, (current) => ({
            ...current,
            error: null,
          }));
        }
        result = { status: "aborted", error: null };
      } else {
        const message = caught instanceof ApiError ? caught.message : "Streaming failed";
        if (controllersRef.current.get(effectiveThreadId) === nextController) {
          updateThreadStream(effectiveThreadId, (current) => ({
            ...current,
            error: message,
          }));
        }
        result = { status: "failed", error: message };
      }
    } finally {
      if (controllersRef.current.get(effectiveThreadId) === nextController) {
        controllersRef.current.delete(effectiveThreadId);
        updateThreadStream(effectiveThreadId, (current) => ({
          ...current,
          isStreaming: false,
        }));
      }
    }
    return result;
  }

  async function start(body: RunRequestBody, threadIdOverride?: string): Promise<ThreadRunStreamResult | null> {
    const effectiveThreadId = threadIdOverride ?? threadId;
    if (!effectiveThreadId) {
      return null;
    }
    applyThreadActivityCacheUpdate(queryClient, effectiveThreadId, body.message);
    const reconnectStream = streamsRef.current[effectiveThreadId];
    const lastEventId = reconnectEventIdForStream(reconnectStream);
    const reconnectCursor = reconnectStream && !reconnectStream.hasTerminalEvent
      ? {
          runId: reconnectStream?.lastRunId ?? null,
          sequence: reconnectStream?.lastSequence ?? null,
        }
      : null;
    const recoveredCursorKey = reconnectCursor?.runId ? `${effectiveThreadId}:${reconnectCursor.runId}` : null;
    const reconnectCursorFromRecoveredRun = recoveredCursorKey
      ? recoveryInFlightRef.current.has(recoveredCursorKey) || recoveryTimersRef.current.has(recoveredCursorKey)
      : false;
    const durableState = queryClient.getQueryData<ThreadStateView>(["thread-state", effectiveThreadId]);
    const shouldRecoverNoRawCursor =
      !reconnectCursorFromRecoveredRun &&
      !lastEventId &&
      reconnectCursor?.runId &&
      typeof reconnectCursor.sequence === "number" &&
      (!durableState ||
        (isRecoverableThreadStatus(durableState.status) &&
          (!durableState.run_id || durableState.run_id === reconnectCursor.runId)));
    if (shouldRecoverNoRawCursor) {
      const result = (await replayMissingRunEvents(effectiveThreadId, reconnectCursor.runId, reconnectCursor.sequence)) ?? {
        status: "interrupted",
        error: null,
      };
      void refreshThreadRunQueries(effectiveThreadId);
      return result;
    }
    return consumeStream(
      (signal) => streamThreadRunWithSignal(effectiveThreadId, body, signal, lastEventId),
      effectiveThreadId,
      reconnectCursor,
    );
  }

  async function resumeApproval(body: ApprovalResumeRequest = {}, threadIdOverride?: string) {
    const effectiveThreadId = threadIdOverride ?? threadId;
    if (!effectiveThreadId) {
      return;
    }
    await consumeStream((signal) => streamThreadApprovalWithSignal(effectiveThreadId, body, signal), effectiveThreadId);
  }

  async function resumeUserInteraction(body: UserInteractionResumeRequest, threadIdOverride?: string) {
    const effectiveThreadId = threadIdOverride ?? threadId;
    if (!effectiveThreadId) {
      return;
    }
    await consumeStream((signal) => streamThreadUserInteractionWithSignal(effectiveThreadId, body, signal), effectiveThreadId);
  }

  async function editLatestAndResend(messageId: string, body: MessageEditResendRequest, threadIdOverride?: string) {
    const effectiveThreadId = threadIdOverride ?? threadId;
    if (!effectiveThreadId) {
      return;
    }
    applyThreadActivityCacheUpdate(queryClient, effectiveThreadId, body.message);
    await consumeStream(
      (signal) => streamEditLatestMessageAndResendWithSignal(effectiveThreadId, messageId, body, signal),
      effectiveThreadId,
    );
  }

  function stop() {
    if (!threadId) {
      return;
    }
    appendThreadEvent(threadId, {
      event: "run_completed",
      data: {
        thread_id: threadId,
        status: "interrupted",
        stream_status: "interrupted",
      },
      receivedAt: Date.now(),
    });
    settleTerminalThreadStream(threadId);
    void interruptThreadRun(threadId, { reason: "Interrupted from UI" })
      .then((state) => {
        queryClient.setQueryData(["thread-state", threadId], state);
        return Promise.all([
          queryClient.invalidateQueries({ queryKey: ["threads"] }),
          queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] }),
          queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] }),
          queryClient.invalidateQueries({ queryKey: ["thread-evaluation-report", threadId] }),
        ]);
      })
      .catch(() => undefined);
    controllersRef.current.get(threadId)?.abort();
  }

  useEffect(() => {
    return () => {
      controllersRef.current.forEach((controller) => controller.abort());
      controllersRef.current.clear();
      recoveryTimersRef.current.forEach((timer) => clearTimeout(timer));
      recoveryTimersRef.current.clear();
      recoveryInFlightRef.current.clear();
    };
  }, []);

  const activeStream = threadId ? streamsByThread[threadId] ?? emptyThreadStream() : emptyThreadStream();

  useEffect(() => {
    if (!threadId) {
      return;
    }
    const state = queryClient.getQueryData<ThreadStateView>(["thread-state", threadId]);
    const runId = state?.run_id;
    if (!isRecoverableThreadStatus(state?.status) || !runId || activeStream.hasTerminalEvent) {
      return;
    }
    if (activeStream.lastRunId && activeStream.lastRunId !== runId) {
      return;
    }
    const recoveryKey = `${threadId}:${runId}`;
    if (activeStream.isStreaming || recoveryInFlightRef.current.has(recoveryKey) || recoveryTimersRef.current.has(recoveryKey)) {
      return;
    }
    void recoverRunningThreadEvents(threadId, runId, activeStream.lastSequence ?? 0);
  });

  return {
    events: activeStream.events,
    error: activeStream.error,
    isStreaming: activeStream.isStreaming,
    start,
    resumeApproval,
    resumeUserInteraction,
    editLatestAndResend,
    stop,
  };
}

function runIdFromEvent(event: RunStreamEvent): string | null {
  const value = event.data.run_id;
  return typeof value === "string" && value ? value : null;
}

function sequenceFromEvent(event: RunStreamEvent): number | null {
  if (typeof event.sequence === "number") {
    return event.sequence;
  }
  const value = event.data.sequence;
  return typeof value === "number" ? value : null;
}

function advanceRunCursor(
  current: { runId: string | null; sequence: number | null },
  event: RunStreamEvent,
): { runId: string | null; sequence: number | null } {
  const eventRunId = runIdFromEvent(event);
  const eventSequence = sequenceFromEvent(event);
  if (!eventRunId) {
    return current;
  }
  if (eventRunId !== current.runId) {
    return { runId: eventRunId, sequence: eventSequence ?? null };
  }
  if (typeof eventSequence !== "number") {
    return current;
  }
  return {
    runId: eventRunId,
    sequence: Math.max(current.sequence ?? 0, eventSequence),
  };
}

function eventIdentity(event: RunStreamEvent): string | null {
  const raw = rawEventIdentity(event);
  if (raw) {
    return raw;
  }
  const runId = runIdFromEvent(event);
  const sequence = sequenceFromEvent(event);
  return runId && typeof sequence === "number" ? `${runId}:${sequence}` : null;
}

function rawEventIdentity(event: RunStreamEvent): string | null {
  if (typeof event.event_id === "string" && event.event_id) {
    return event.event_id;
  }
  const eventId = event.data.event_id;
  if (typeof eventId === "string" && eventId) {
    return eventId;
  }
  return null;
}

function terminalEventId(event: RunStreamEvent): string | null {
  return isTerminalRunEvent(event) ? eventIdentity(event) : null;
}

function isTerminalRunEvent(event: RunStreamEvent): boolean {
  return event.event === "run_completed" || event.event === "run_failed";
}

function isRecoverableThreadStatus(status: ThreadStateView["status"] | null | undefined): boolean {
  return status === "running" || status === "awaiting_approval" || status === "awaiting_clarification";
}

function dedupeEventIdentity(event: RunStreamEvent): string | null {
  const runId = runIdFromEvent(event);
  const sequence = sequenceFromEvent(event);
  if (runId && typeof sequence === "number") {
    return `${runId}:${sequence}`;
  }
  return eventIdentity(event);
}

function appendDedupedRunEvents(
  current: ClientRunStreamEvent[],
  additions: ClientRunStreamEvent[],
): ClientRunStreamEvent[] {
  if (additions.length === 0) {
    return current;
  }
  const seen = new Set(current.map(dedupeEventIdentity).filter(Boolean));
  const next = [...current];
  for (const event of additions) {
    const identity = dedupeEventIdentity(event);
    if (identity && seen.has(identity)) {
      continue;
    }
    if (identity) {
      seen.add(identity);
    }
    next.push(event);
  }
  return next.sort((a, b) => {
    const aSequence = sequenceFromEvent(a);
    const bSequence = sequenceFromEvent(b);
    if (typeof aSequence === "number" && typeof bSequence === "number" && aSequence !== bSequence) {
      return aSequence - bSequence;
    }
    return 0;
  });
}

function reconnectEventIdForStream(stream: ThreadStreamSnapshot | undefined): string | null {
  if (!stream?.events.length) {
    return null;
  }
  if (stream.hasTerminalEvent) {
    return null;
  }
  for (let index = stream.events.length - 1; index >= 0; index -= 1) {
    const identity = rawEventIdentity(stream.events[index]!);
    if (identity) {
      return identity;
    }
  }
  return null;
}

export function useApproveThread(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => approveThread(threadId!),
    onSuccess: async () => {
      if (!threadId) {
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
    },
  });
}

export function useCancelThreadApproval(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason?: string) => cancelApproval(threadId!, reason ? { reason } : {}),
    onSuccess: async () => {
      if (!threadId) {
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
    },
  });
}

function invalidateQueuedFollowupViews(
  queryClient: ReturnType<typeof useQueryClient>,
  effectiveThreadId: string,
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["thread-state", effectiveThreadId] }),
    queryClient.invalidateQueries({ queryKey: ["thread-detail", effectiveThreadId] }),
  ]);
}

export function useEnqueueThreadFollowup(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      bodyOrOptions:
        | QueuedFollowUpCreateRequest
        | {
            body: QueuedFollowUpCreateRequest;
            threadIdOverride?: string;
          },
    ) => {
      const body = "body" in bodyOrOptions ? bodyOrOptions.body : bodyOrOptions;
      const effectiveThreadId = "body" in bodyOrOptions ? bodyOrOptions.threadIdOverride ?? threadId : threadId;
      return enqueueThreadFollowup(effectiveThreadId!, body);
    },
    onSuccess: async (_result, bodyOrOptions) => {
      const effectiveThreadId = "body" in bodyOrOptions ? bodyOrOptions.threadIdOverride ?? threadId : threadId;
      if (!effectiveThreadId) {
        return;
      }
      await invalidateQueuedFollowupViews(queryClient, effectiveThreadId);
    },
  });
}

export function useUpdateThreadFollowup(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      body,
      threadIdOverride,
    }: {
      queueId: string;
      body: QueuedFollowUpUpdateRequest;
      threadIdOverride?: string;
    }) => updateThreadFollowup(threadIdOverride ?? threadId!, queueId, body),
    onSuccess: async (_result, variables) => {
      const effectiveThreadId = variables.threadIdOverride ?? threadId;
      if (!effectiveThreadId) {
        return;
      }
      await invalidateQueuedFollowupViews(queryClient, effectiveThreadId);
    },
  });
}

export function useDeleteThreadFollowup(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, threadIdOverride }: { queueId: string; threadIdOverride?: string }) =>
      deleteThreadFollowup(threadIdOverride ?? threadId!, queueId),
    onSuccess: async (_result, variables) => {
      const effectiveThreadId = variables.threadIdOverride ?? threadId;
      if (!effectiveThreadId) {
        return;
      }
      await invalidateQueuedFollowupViews(queryClient, effectiveThreadId);
    },
  });
}

export function usePopNextThreadFollowup(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (threadIdOverride?: string) => popNextThreadFollowup(threadIdOverride ?? threadId!),
    onSuccess: async (_result, threadIdOverride) => {
      const effectiveThreadId = threadIdOverride ?? threadId;
      if (!effectiveThreadId) {
        return;
      }
      await invalidateQueuedFollowupViews(queryClient, effectiveThreadId);
    },
  });
}

export function useUpdateThreadSettings(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      bodyOrOptions:
        | ThreadSettingsUpdateRequest
        | {
            body: ThreadSettingsUpdateRequest;
            threadIdOverride?: string;
          },
    ) => {
      const body = "body" in bodyOrOptions ? bodyOrOptions.body : bodyOrOptions;
      const effectiveThreadId = "body" in bodyOrOptions ? bodyOrOptions.threadIdOverride ?? threadId : threadId;
      return updateThreadSettings(effectiveThreadId!, body);
    },
    onSuccess: async (_result, bodyOrOptions) => {
      const effectiveThreadId = "body" in bodyOrOptions ? bodyOrOptions.threadIdOverride ?? threadId : threadId;
      if (!effectiveThreadId) {
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["thread-settings", effectiveThreadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-state", effectiveThreadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", effectiveThreadId] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useWaitSubagentTask(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, timeoutSeconds }: { taskId: string; timeoutSeconds?: number }) =>
      waitSubagentTask(threadId!, taskId, timeoutSeconds),
    onSuccess: async () => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useCancelSubagentTask(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => cancelSubagentTask(threadId!, taskId),
    onSuccess: async () => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useWaitProcessSession(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, timeoutSeconds }: { sessionId: string; timeoutSeconds?: number }) =>
      waitProcessSession(threadId!, sessionId, timeoutSeconds),
    onSuccess: async () => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
    },
  });
}

export function useKillProcessSession(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => killProcessSession(threadId!, sessionId),
    onSuccess: async () => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
    },
  });
}

export function useWriteProcessStdin(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, body }: { sessionId: string; body: ProcessStdinRequest }) =>
      writeProcessStdin(threadId!, sessionId, body),
    onSuccess: async (_data, variables) => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-process-log", threadId, variables.sessionId] });
    },
  });
}

export function useCloseProcessStdin(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => closeProcessStdin(threadId!, sessionId),
    onSuccess: async (_data, sessionId) => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-process-log", threadId, sessionId] });
    },
  });
}

export function useInterruptProcessSession(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => interruptProcessSession(threadId!, sessionId),
    onSuccess: async () => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
    },
  });
}

export function useResizeProcessSession(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, body }: { sessionId: string; body: ProcessResizeRequest }) =>
      resizeProcessSession(threadId!, sessionId, body),
    onSuccess: async () => {
      if (!threadId) return;
      await queryClient.invalidateQueries({ queryKey: ["thread-state", threadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-detail", threadId] });
    },
  });
}

export function useProcessLog(
  threadId: string | null,
  sessionId: string | null,
  options: { enabled?: boolean; offset?: number; cursor?: number; limit?: number; refetchIntervalMs?: number | false } = {},
) {
  return useQuery({
    queryKey: ["thread-process-log", threadId, sessionId, options.offset ?? 0, options.cursor ?? null, options.limit ?? 200],
    queryFn: () => getProcessLog(threadId!, sessionId!, options),
    enabled: Boolean(threadId && sessionId) && (options.enabled ?? true),
    refetchInterval: options.refetchIntervalMs ?? false,
  });
}

export function useProcessCapabilities(threadId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["thread-process-capabilities", threadId],
    queryFn: () => getProcessCapabilities(threadId!),
    enabled: Boolean(threadId) && (options.enabled ?? true),
    staleTime: THREAD_STATE_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useScheduledTasks(options: { enabled?: boolean } = {}) {
  return useQuery<ScheduledTaskAdminResponse>({
    queryKey: ["scheduled-tasks"],
    queryFn: listScheduledTasks,
    enabled: options.enabled ?? true,
    refetchInterval: (query) => {
      const tasks = query.state.data?.items ?? [];
      return tasks.some((task) => task.status === "running") ? 2000 : false;
    },
  });
}

export function useScheduledTaskAutomation(options: { enabled?: boolean } = {}) {
  return useQuery<ScheduledTaskAutomationStatusResponse>({
    queryKey: ["scheduled-tasks", "automation"],
    queryFn: getScheduledTaskAutomation,
    enabled: options.enabled ?? true,
    refetchInterval: (query) => {
      const status = query.state.data;
      return status && ((status.running_count ?? 0) > 0 || (status.due_count ?? 0) > 0) ? 2000 : false;
    },
  });
}

export function useRunScheduledTaskAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runScheduledTaskAutomation(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks", "automation"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useRunScheduledTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => runScheduledTask(taskId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks", "automation"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function usePauseScheduledTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => pauseScheduledTask(taskId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks", "automation"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
    },
  });
}

export function useResumeScheduledTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => resumeScheduledTask(taskId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks", "automation"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
    },
  });
}
