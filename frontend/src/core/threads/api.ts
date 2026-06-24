import type {
  ApprovalCancelRequest,
  ApprovalResumeRequest,
  EvaluationThreadReportView,
  ProcessLogView,
  ProcessResizeRequest,
  ProcessSessionView,
  ProcessStdinRequest,
  QueuedFollowUpCreateRequest,
  QueuedFollowUpUpdateRequest,
  QueuedFollowUpView,
  ScheduledTaskAdminResponse,
  ScheduledTaskAutomationRunResponse,
  ScheduledTaskAutomationStatusResponse,
  ScheduledTaskRunView,
  ScheduledTaskView,
  TerminalBackendCapabilitiesView,
  MessageEditResendRequest,
  RunCompletedView,
  RunEventReplayView,
  RunRequestBody,
  RunStreamEvent,
  SubagentTaskView,
  ThreadDetailView,
  ThreadDeleteResult,
  ThreadSettingsUpdateRequest,
  ThreadSettingsView,
  ThreadStateView,
  ThreadView,
  UserInteractionResumeRequest,
} from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";
import { postEventStream } from "@/src/core/api/sse";

export function listThreads() {
  return apiRequest<ThreadView[]>("/threads");
}

export function createThread(threadId?: string, workspaceRoot?: string | null) {
  return apiRequest<ThreadView>("/threads", {
    method: "POST",
    body: JSON.stringify({
      ...(threadId ? { thread_id: threadId } : {}),
      ...(workspaceRoot ? { workspace_root: workspaceRoot } : {}),
    }),
  });
}

export function deleteThread(threadId: string) {
  return apiRequest<ThreadDeleteResult>(`/threads/${threadId}`, {
    method: "DELETE",
  });
}

export type ThreadStateOptions = {
  stateScope?: "chat" | "full";
  stateSource?: "snapshot" | "event_log";
  runId?: string | null;
};

export function getThreadState(threadId: string, options: ThreadStateOptions = {}) {
  const params = new URLSearchParams();
  if (options.stateScope) {
    params.set("state_scope", options.stateScope);
  }
  if (options.stateSource) {
    params.set("state_source", options.stateSource);
  }
  if (options.runId) {
    params.set("run_id", options.runId);
  }
  const query = params.toString();
  return apiRequest<ThreadStateView>(`/threads/${threadId}/state${query ? `?${query}` : ""}`);
}

export type ThreadDetailOptions = {
  messageOffset?: number | null;
  messageLimit?: number | null;
  stateScope?: "chat" | "full";
  stateSource?: "snapshot" | "event_log" | "auto";
};

export function getThreadDetail(threadId: string, options: ThreadDetailOptions = {}) {
  const params = new URLSearchParams();
  if (typeof options.messageOffset === "number") {
    params.set("message_offset", String(options.messageOffset));
  }
  if (typeof options.messageLimit === "number") {
    params.set("message_limit", String(options.messageLimit));
  }
  if (options.stateScope) {
    params.set("state_scope", options.stateScope);
  }
  if (options.stateSource) {
    params.set("state_source", options.stateSource);
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  return apiRequest<ThreadDetailView>(`/threads/${threadId}/detail${suffix}`);
}

export function getThreadEvaluationReport(threadId: string) {
  return apiRequest<EvaluationThreadReportView>(`/threads/${threadId}/evaluation-report`);
}

export function getThreadSettings(threadId: string) {
  return apiRequest<ThreadSettingsView>(`/threads/${threadId}/settings`);
}

export function updateThreadSettings(threadId: string, body: ThreadSettingsUpdateRequest) {
  return apiRequest<ThreadSettingsView>(`/threads/${threadId}/settings`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function runThread(threadId: string, body: RunRequestBody) {
  return apiRequest<RunCompletedView>(`/threads/${threadId}/runs`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function approveThread(threadId: string, body: ApprovalResumeRequest = {}) {
  return apiRequest<RunCompletedView>(`/threads/${threadId}/approvals/approve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function cancelApproval(threadId: string, body: ApprovalCancelRequest = {}) {
  return apiRequest<ThreadStateView>(`/threads/${threadId}/approvals/cancel`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function interruptThreadRun(threadId: string, body: ApprovalCancelRequest = {}) {
  return apiRequest<ThreadStateView>(`/threads/${threadId}/runs/interrupt`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function enqueueThreadFollowup(threadId: string, body: QueuedFollowUpCreateRequest) {
  return apiRequest<QueuedFollowUpView>(`/threads/${threadId}/followups`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateThreadFollowup(threadId: string, queueId: string, body: QueuedFollowUpUpdateRequest) {
  return apiRequest<QueuedFollowUpView>(`/threads/${threadId}/followups/${queueId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteThreadFollowup(threadId: string, queueId: string) {
  return apiRequest<QueuedFollowUpView>(`/threads/${threadId}/followups/${queueId}`, {
    method: "DELETE",
  });
}

export function popNextThreadFollowup(threadId: string) {
  return apiRequest<QueuedFollowUpView | null>(`/threads/${threadId}/followups/next`, {
    method: "POST",
  });
}

export function streamThreadRun(threadId: string, body: RunRequestBody): AsyncGenerator<RunStreamEvent> {
  return postEventStream(`/threads/${threadId}/runs/stream`, body);
}

export function streamThreadRunWithSignal(
  threadId: string,
  body: RunRequestBody,
  signal?: AbortSignal,
  lastEventId?: string | null,
): AsyncGenerator<RunStreamEvent> {
  return postEventStream(`/threads/${threadId}/runs/stream`, body, { signal, lastEventId });
}

export function listThreadRunEvents(
  threadId: string,
  options: {
    runId?: string | null;
    afterSequence?: number | null;
    limit?: number;
  } = {},
) {
  const params = new URLSearchParams();
  if (options.runId) {
    params.set("run_id", options.runId);
  }
  if (typeof options.afterSequence === "number") {
    params.set("after_sequence", String(options.afterSequence));
  }
  if (typeof options.limit === "number") {
    params.set("limit", String(options.limit));
  }
  const query = params.toString();
  return apiRequest<RunEventReplayView>(`/threads/${threadId}/runs/events${query ? `?${query}` : ""}`);
}

export function streamThreadApprovalWithSignal(
  threadId: string,
  body: ApprovalResumeRequest,
  signal?: AbortSignal,
): AsyncGenerator<RunStreamEvent> {
  return postEventStream(`/threads/${threadId}/approvals/approve/stream`, body, { signal });
}

export function streamThreadUserInteractionWithSignal(
  threadId: string,
  body: UserInteractionResumeRequest,
  signal?: AbortSignal,
): AsyncGenerator<RunStreamEvent> {
  return postEventStream(`/threads/${threadId}/interactions/resume/stream`, body, { signal });
}

export function editLatestMessageAndResend(threadId: string, messageId: string, body: MessageEditResendRequest) {
  return apiRequest<RunCompletedView>(`/threads/${threadId}/messages/${messageId}/edit-latest-and-resend`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function streamEditLatestMessageAndResendWithSignal(
  threadId: string,
  messageId: string,
  body: MessageEditResendRequest,
  signal?: AbortSignal,
): AsyncGenerator<RunStreamEvent> {
  return postEventStream(`/threads/${threadId}/messages/${messageId}/edit-latest-and-resend/stream`, body, { signal });
}

export function listSubagentTasks(threadId: string) {
  return apiRequest<SubagentTaskView[]>(`/threads/${threadId}/subagents`);
}

export function getSubagentTask(threadId: string, taskId: string) {
  return apiRequest<SubagentTaskView>(`/threads/${threadId}/subagents/${taskId}`);
}

export function waitSubagentTask(threadId: string, taskId: string, timeoutSeconds?: number) {
  const suffix = typeof timeoutSeconds === "number" ? `?timeout_seconds=${timeoutSeconds}` : "";
  return apiRequest<SubagentTaskView>(`/threads/${threadId}/subagents/${taskId}/wait${suffix}`, {
    method: "POST",
  });
}

export function cancelSubagentTask(threadId: string, taskId: string) {
  return apiRequest<SubagentTaskView>(`/threads/${threadId}/subagents/${taskId}/cancel`, {
    method: "POST",
  });
}

export function listProcessSessions(threadId: string) {
  return apiRequest<ProcessSessionView[]>(`/threads/${threadId}/processes`);
}

export function listScheduledTasks() {
  return apiRequest<ScheduledTaskAdminResponse>("/scheduled-tasks");
}

export function getScheduledTaskAutomation() {
  return apiRequest<ScheduledTaskAutomationStatusResponse>("/scheduled-tasks/automation");
}

export function runScheduledTaskAutomation() {
  return apiRequest<ScheduledTaskAutomationRunResponse>("/scheduled-tasks/automation/run", {
    method: "POST",
  });
}

export function runScheduledTask(taskId: string) {
  return apiRequest<ScheduledTaskRunView>(`/scheduled-tasks/${taskId}/run`, {
    method: "POST",
  });
}

export function pauseScheduledTask(taskId: string) {
  return apiRequest<ScheduledTaskView>(`/scheduled-tasks/${taskId}/pause`, {
    method: "POST",
  });
}

export function resumeScheduledTask(taskId: string) {
  return apiRequest<ScheduledTaskView>(`/scheduled-tasks/${taskId}/resume`, {
    method: "POST",
  });
}

export function getProcessCapabilities(threadId: string) {
  return apiRequest<TerminalBackendCapabilitiesView>(`/threads/${threadId}/processes/capabilities`);
}

export function getProcessSession(threadId: string, sessionId: string) {
  return apiRequest<ProcessSessionView>(`/threads/${threadId}/processes/${sessionId}`);
}

export function waitProcessSession(threadId: string, sessionId: string, timeoutSeconds?: number) {
  const suffix = typeof timeoutSeconds === "number" ? `?timeout_seconds=${timeoutSeconds}` : "";
  return apiRequest<ProcessSessionView>(`/threads/${threadId}/processes/${sessionId}/wait${suffix}`, {
    method: "POST",
  });
}

export function killProcessSession(threadId: string, sessionId: string) {
  return apiRequest<ProcessSessionView>(`/threads/${threadId}/processes/${sessionId}/kill`, {
    method: "POST",
  });
}

export function writeProcessStdin(threadId: string, sessionId: string, body: ProcessStdinRequest) {
  return apiRequest<ProcessSessionView>(`/threads/${threadId}/processes/${sessionId}/stdin`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function closeProcessStdin(threadId: string, sessionId: string) {
  return apiRequest<ProcessSessionView>(`/threads/${threadId}/processes/${sessionId}/stdin/close`, {
    method: "POST",
  });
}

export function interruptProcessSession(threadId: string, sessionId: string) {
  return apiRequest<ProcessSessionView>(`/threads/${threadId}/processes/${sessionId}/interrupt`, {
    method: "POST",
  });
}

export function resizeProcessSession(threadId: string, sessionId: string, body: ProcessResizeRequest) {
  return apiRequest<ProcessSessionView>(`/threads/${threadId}/processes/${sessionId}/resize`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getProcessLog(threadId: string, sessionId: string, options: { offset?: number; cursor?: number; limit?: number } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 200));
  if (typeof options.cursor === "number") {
    params.set("cursor", String(options.cursor));
  } else {
    params.set("offset", String(options.offset ?? 0));
  }
  return apiRequest<ProcessLogView>(`/threads/${threadId}/processes/${sessionId}/log?${params.toString()}`);
}
