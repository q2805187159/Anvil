"use client";

import { useMemo } from "react";

import type { MessageStepView, MessageView, RunStreamEvent } from "@/src/core/contracts";

export type ClientRunStreamEvent = RunStreamEvent & {
  receivedAt?: number;
};

export type StepSummaryState = {
  title: string;
  folded_step_count: number;
  completed: boolean;
};

export type StepTranscriptMessage = MessageView & {
  step_summary?: StepSummaryState;
  live?: boolean;
  sequence?: number;
};

export function useMessageReducer(
  messages: MessageView[],
  events: ClientRunStreamEvent[],
): StepTranscriptMessage[] {
  return useMemo(() => reduceMessageSteps(messages, events), [messages, events]);
}

export function reduceMessageSteps(
  messages: MessageView[],
  events: ClientRunStreamEvent[],
): StepTranscriptMessage[] {
  const reduced = messages.map(cloneMessage);
  const indexByMessageId = new Map(reduced.map((message, index) => [message.message_id, index]));
  const sequenceByMessageId = new Map(reduced.map((message, index) => [message.message_id, index]));
  const activeMessageIds = new Set<string>();
  const seenEventIds = new Set<string>();
  let nextSequence = reduced.length;

  function upsertMessage(messageId: string): StepTranscriptMessage {
    const currentIndex = indexByMessageId.get(messageId);
    if (currentIndex !== undefined) {
      return reduced[currentIndex]!;
    }
    const next: StepTranscriptMessage = {
      message_id: messageId,
      role: "ai",
      content: "",
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
      live: true,
    };
    sequenceByMessageId.set(messageId, nextSequence++);
    indexByMessageId.set(messageId, reduced.length);
    reduced.push(next);
    return next;
  }

  function markActiveMessage(message: StepTranscriptMessage) {
    message.live = true;
    activeMessageIds.add(message.message_id);
  }

  for (const event of events) {
    const eventId = stringValue(event.event_id ?? event.data?.event_id);
    if (eventId) {
      if (seenEventIds.has(eventId)) {
        continue;
      }
      seenEventIds.add(eventId);
    }
    if (event.event === "step_started" || event.event === "step_updated") {
      const step = normalizeIncomingStep(event.data.step);
      if (!step) {
        continue;
      }
      applyEnvelopeMetadata(step, event);
      const message = upsertMessage(step.message_id);
      markActiveMessage(message);
      upsertStep(message, step);
      syncMessageContentFromSteps(message);
      continue;
    }

    if (event.event === "step_delta") {
      const messageId = stringValue(event.data.message_id);
      const stepId = stringValue(event.data.step_id);
      if (!messageId || !stepId) {
        continue;
      }
      const message = upsertMessage(messageId);
      markActiveMessage(message);
      const step = ensureStep(message, stepId, messageId);
      applyEnvelopeMetadata(step, event);
      const rawPayload = normalizeRawPayloadForStep(
        step.type,
        step.language,
        step.tool_name,
        mergeStreamingText(rawStepPayload(step), String(event.data.payload_delta ?? "")),
      );
      step.metadata = { ...(step.metadata ?? {}), raw_payload: rawPayload };
      step.payload = sanitizeStepPayload(step.type, rawPayload);
      if (step.status === "pending") {
        step.status = "running";
      }
      syncMessageContentFromSteps(message);
      continue;
    }

    if (event.event === "summary_update") {
      const messageId = stringValue(event.data.message_id);
      if (!messageId) {
        continue;
      }
      const message = upsertMessage(messageId);
      markActiveMessage(message);
      message.step_summary = {
        title: String(event.data.title ?? "已运行 0 条消息"),
        folded_step_count: numberValue(event.data.folded_step_count) ?? 0,
        completed: false,
      };
      continue;
    }

    if (event.event === "reasoning_delta") {
      const messageId = stringValue(event.data.message_id);
      if (!messageId) {
        continue;
      }
      const message = upsertMessage(messageId);
      markActiveMessage(message);
      continue;
    }

    if (event.event === "reasoning_completed") {
      const messageId = stringValue(event.data.message_id);
      if (!messageId) {
        continue;
      }
      const message = upsertMessage(messageId);
      markActiveMessage(message);
      const durationMs = numberValue(event.data.duration_ms);
      for (const step of message.steps) {
        if (step.type !== "thinking" || !isChatVisibleStep(step) || step.status === "success") {
          continue;
        }
        step.status = "success";
        step.completed_at = step.completed_at ?? new Date().toISOString();
        if (durationMs !== null) {
          step.duration_ms = durationMs;
          step.duration = formatStepDuration(durationMs);
        }
      }
      continue;
    }

    if (event.event === "message_completed") {
      const messageId = stringValue(event.data.message_id);
      if (!messageId) {
        continue;
      }
      const message = upsertMessage(messageId);
      markActiveMessage(message);
      message.stream_status = stringValue(event.data.stream_status) ?? "complete";
      completeRunningSteps(message, message.stream_status === "interrupted" ? "error" : "success");
      const foldedCount = message.steps.filter((step) => step.type !== "content").length;
      if (foldedCount > 0 || message.step_summary) {
        message.step_summary = {
          title: message.step_summary?.title ?? `已运行 ${foldedCount} 条消息`,
          folded_step_count: message.step_summary?.folded_step_count ?? foldedCount,
          completed: true,
        };
      }
      continue;
    }

    if (event.event === "run_completed") {
      const streamStatus = stringValue(event.data.stream_status) ?? "complete";
      const finalStatus = streamStatus === "interrupted" ? "error" : "success";
      for (const message of reduced) {
        if ((message.role === "ai" || message.role === "assistant") && activeMessageIds.has(message.message_id)) {
          message.stream_status = message.stream_status ?? streamStatus;
          completeRunningSteps(message, finalStatus);
        }
      }
      return finalizeReducedMessages(reduced, sequenceByMessageId);
    }

    if (event.event === "run_failed") {
      const errorText = String(event.data.error ?? "Run failed");
      const last = [...reduced].reverse().find((message) => message.role === "ai" || message.role === "assistant");
      if (!last) {
        continue;
      }
      for (const step of last.steps) {
        if (step.status === "running" || step.status === "pending") {
          step.status = "error";
          step.error = errorText;
        }
      }
    }
  }

  return finalizeReducedMessages(reduced, sequenceByMessageId);
}

function finalizeReducedMessages(
  messages: StepTranscriptMessage[],
  sequenceByMessageId: Map<string, number>,
): StepTranscriptMessage[] {
  return deduplicateHydratedLiveMessages(messages).map((message) => ({
    ...message,
    steps: [...message.steps].sort((a, b) => stepTimelineOrder(a) - stepTimelineOrder(b)),
    sequence: sequenceByMessageId.get(message.message_id) ?? 0,
  }));
}

function completeRunningSteps(message: StepTranscriptMessage, status: "success" | "error") {
  const now = new Date().toISOString();
  for (const step of message.steps) {
    if (step.status !== "running" && step.status !== "pending") {
      continue;
    }
    step.status = status;
    step.completed_at = step.completed_at ?? now;
    if (status === "error") {
      step.error = step.error ?? "interrupted";
    }
  }
}

function cloneMessage(message: MessageView): StepTranscriptMessage {
  return {
    ...message,
    steps: [...(message.steps ?? [])].map(normalizeStep).sort((a, b) => stepTimelineOrder(a) - stepTimelineOrder(b)),
  };
}

function normalizeIncomingStep(value: unknown): MessageStepView | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  return normalizeStep(value as Partial<MessageStepView>);
}

function applyEnvelopeMetadata(step: MessageStepView, event: ClientRunStreamEvent) {
  const sequence = numberValue(event.sequence ?? event.data?.sequence);
  const eventId = stringValue(event.event_id ?? event.data?.event_id);
  const blockId = stringValue(event.block_id ?? event.data?.block_id);
  step.visibility = normalizeVisibility(event.visibility ?? event.data?.visibility ?? step.visibility);
  step.metadata = {
    ...(step.metadata ?? {}),
    ...(sequence !== null ? { sequence } : {}),
    ...(eventId ? { event_id: eventId } : {}),
    ...(blockId ? { block_id: blockId } : {}),
  };
}

function normalizeStep(step: Partial<MessageStepView>): MessageStepView {
  const stepType = normalizeStepType(step.type);
  const language = normalizeLanguage(step.language);
  const rawPayload = normalizeRawPayloadForStep(stepType, language, step.tool_name ?? null, String(step.payload ?? ""));
  const metadata = typeof step.metadata === "object" && step.metadata !== null ? step.metadata : {};
  return {
    step_id: String(step.step_id ?? ""),
    message_id: String(step.message_id ?? ""),
    type: stepType,
    title: String(step.title ?? fallbackTitle(step.type)),
    action: step.action ?? null,
    status: normalizeStepStatus(step.status),
    duration: step.duration ?? null,
    duration_ms: typeof step.duration_ms === "number" ? step.duration_ms : null,
    payload: sanitizeStepPayload(stepType, rawPayload),
    language,
    tool_name: step.tool_name ?? null,
    tool_call_id: step.tool_call_id ?? null,
    order: typeof step.order === "number" ? step.order : 0,
    started_at: step.started_at ?? null,
    completed_at: step.completed_at ?? null,
    error: step.error ?? null,
    metadata: { ...metadata, raw_payload: rawPayload },
    visibility: normalizeVisibility(step.visibility),
  };
}

function upsertStep(message: StepTranscriptMessage, step: MessageStepView) {
  const currentIndex = message.steps.findIndex((item) => item.step_id === step.step_id);
  if (currentIndex >= 0) {
    message.steps[currentIndex] = mergeStep(message.steps[currentIndex]!, step);
    return;
  }
  message.steps.push(step);
}

function rawStepPayload(step: MessageStepView): string {
  const raw = step.metadata?.raw_payload;
  return typeof raw === "string" ? raw : String(step.payload ?? "");
}

function normalizeRawPayloadForStep(
  stepType: string,
  language: string | null | undefined,
  toolName: string | null | undefined,
  payload: string,
): string {
  if (stepType !== "call" || language === "shell" || toolName === "run_command" || toolName === "process") {
    return payload;
  }
  return collapseAdjacentRepeatedJsonSnapshots(payload);
}

function collapseAdjacentRepeatedJsonSnapshots(payload: string): string {
  const trimmed = payload.trim();
  if (!trimmed || !["{", "["].includes(trimmed[0] ?? "")) {
    return payload;
  }
  const segments: string[] = [];
  let cursor = 0;
  while (cursor < trimmed.length) {
    while (cursor < trimmed.length && /\s/.test(trimmed[cursor] ?? "")) {
      cursor += 1;
    }
    if (cursor >= trimmed.length) {
      break;
    }
    const segmentEnd = findJsonValueEnd(trimmed, cursor);
    if (segmentEnd === null) {
      return payload;
    }
    segments.push(trimmed.slice(cursor, segmentEnd));
    cursor = segmentEnd;
  }
  if (segments.length < 2) {
    return payload;
  }
  const canonical = segments.map(canonicalJson);
  if (canonical.some((item) => item === null)) {
    return payload;
  }
  const first = canonical[0];
  if (!canonical.every((item) => item === first)) {
    return payload;
  }
  return segments[0] ?? payload;
}

function findJsonValueEnd(value: string, start: number): number | null {
  const opener = value[start];
  const closer = opener === "{" ? "}" : opener === "[" ? "]" : null;
  if (!closer) {
    return null;
  }
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = start; index < value.length; index += 1) {
    const char = value[index]!;
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === "\"") {
        inString = false;
      }
      continue;
    }
    if (char === "\"") {
      inString = true;
      continue;
    }
    if (char === opener) {
      depth += 1;
      continue;
    }
    if (char === closer) {
      depth -= 1;
      if (depth === 0) {
        return index + 1;
      }
    }
  }
  return null;
}

function canonicalJson(value: string): string | null {
  try {
    return JSON.stringify(JSON.parse(value));
  } catch {
    return null;
  }
}

function mergeStreamingText(previous: string, incoming: string): string {
  if (!incoming) {
    return previous;
  }
  if (!previous) {
    return incoming;
  }
  if (incoming === previous) {
    return previous;
  }
  if (isJsonSupersetSnapshot(previous, incoming)) {
    return incoming;
  }
  if (incoming.startsWith(previous)) {
    return incoming;
  }
  if (previous.endsWith(incoming)) {
    return previous;
  }
  const overlap = longestSuffixPrefixOverlap(previous, incoming);
  return `${previous}${incoming.slice(overlap)}`;
}

function longestSuffixPrefixOverlap(previous: string, incoming: string): number {
  const maxLength = Math.min(previous.length, incoming.length);
  for (let length = maxLength; length > 0; length -= 1) {
    if (previous.endsWith(incoming.slice(0, length))) {
      return length;
    }
  }
  return 0;
}

function isJsonSupersetSnapshot(previous: string, incoming: string) {
  const previousValue = parseJsonSnapshot(previous);
  const incomingValue = parseJsonSnapshot(incoming);
  if (!previousValue || !incomingValue) {
    return false;
  }
  if (Array.isArray(previousValue.value) && Array.isArray(incomingValue.value)) {
    return incomingValue.value.length >= previousValue.value.length;
  }
  if (isPlainObject(previousValue.value) && isPlainObject(incomingValue.value)) {
    return Object.entries(previousValue.value).every(([key, value]) =>
      Object.prototype.hasOwnProperty.call(incomingValue.value, key) &&
      JSON.stringify((incomingValue.value as Record<string, unknown>)[key]) === JSON.stringify(value),
    );
  }
  return false;
}

function parseJsonSnapshot(value: string): { value: unknown } | null {
  const trimmed = value.trim();
  if (!trimmed || !["{", "["].includes(trimmed[0] ?? "")) {
    return null;
  }
  try {
    return { value: JSON.parse(trimmed) };
  } catch {
    return null;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sanitizeStepPayload(stepType: string, payload: string): string {
  if (stepType === "content") {
    return stripInlineThinkingTags(payload);
  }
  return unwrapInlineThinkingTags(payload);
}

function stripInlineThinkingTags(value: string): string {
  if (!/<\/?think\b/i.test(value)) {
    return value;
  }
  const withoutCompleteBlocks = value.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, "");
  const withoutClosing = withoutCompleteBlocks.replace(/<\/think>/gi, "");
  return withoutClosing.replace(/<think\b[^>]*>[\s\S]*$/i, "").trimStart();
}

function unwrapInlineThinkingTags(value: string): string {
  if (!/<\/?think\b/i.test(value)) {
    return value;
  }
  return value
    .replace(/<think\b[^>]*>/gi, "")
    .replace(/<\/think>/gi, "")
    .trim();
}

function mergeStep(existing: MessageStepView, incoming: MessageStepView): MessageStepView {
  const existingTerminal = existing.status === "success" || existing.status === "error";
  const incomingStartsRunning = incoming.status === "running" || incoming.status === "pending";
  const existingMetadata = existing.metadata ?? {};
  const incomingMetadata = incoming.metadata ?? {};
  const mergedMetadata = { ...existingMetadata, ...incomingMetadata };
  const incomingRawPayload = incomingMetadata.raw_payload;
  const existingRawPayload = existingMetadata.raw_payload;
  if (
    typeof existingRawPayload === "string" &&
    existingRawPayload.length > 0 &&
    typeof incomingRawPayload === "string" &&
    incomingRawPayload.length === 0
  ) {
    mergedMetadata.raw_payload = existingRawPayload;
  }
  return {
    ...existing,
    ...incoming,
    status: existingTerminal && incomingStartsRunning ? existing.status : incoming.status,
    payload: incoming.payload || existing.payload || "",
    completed_at: incoming.completed_at ?? existing.completed_at,
    duration: incoming.duration ?? existing.duration,
    duration_ms: incoming.duration_ms ?? existing.duration_ms,
    error: incoming.error ?? existing.error,
    started_at: incoming.started_at ?? existing.started_at,
    metadata: mergedMetadata,
  };
}

function ensureStep(message: StepTranscriptMessage, stepId: string, messageId: string) {
  const existing = message.steps.find((step) => step.step_id === stepId);
  if (existing) {
    return existing;
  }
  const inferredType = stepId.endsWith(":content") ? "content" : "thinking";
  const inferredVisibility = inferredType === "content" ? "chat" : "hidden";
  const next = normalizeStep({
    step_id: stepId,
    message_id: messageId,
    type: inferredType,
    title: fallbackTitle(inferredType),
    status: "running",
    language: inferredType === "content" ? "markdown" : "text",
    order: message.steps.length,
    visibility: inferredVisibility,
  });
  message.steps.push(next);
  return next;
}

function nextStepOrder(message: StepTranscriptMessage) {
  if (message.steps.length === 0) {
    return 0;
  }
  return Math.max(...message.steps.map((step) => step.order ?? 0)) + 1;
}

function syncMessageContentFromSteps(message: StepTranscriptMessage) {
  const content = message.steps
    .filter((step) => step.type === "content" && isChatVisibleStep(step))
    .map((step) => step.payload ?? "")
    .join("");
  if (content) {
    message.content = content;
  }
}

function normalizeStepType(value: unknown) {
  const raw = String(value ?? "content");
  return raw === "thinking" || raw === "call" || raw === "content" ? raw : "content";
}

function normalizeStepStatus(value: unknown) {
  const raw = String(value ?? "success");
  return raw === "pending" || raw === "running" || raw === "success" || raw === "error" ? raw : "running";
}

function normalizeLanguage(value: unknown) {
  const raw = String(value ?? "text");
  return raw === "shell" || raw === "json" || raw === "markdown" || raw === "text" ? raw : "text";
}

function fallbackTitle(value: unknown) {
  const type = normalizeStepType(value);
  if (type === "thinking") {
    return "Analyzing...";
  }
  if (type === "call") {
    return "已运行工具";
  }
  return "最终回答";
}

function normalizeVisibility(value: unknown) {
  const raw = String(value ?? "chat");
  return raw === "chat" || raw === "timeline" || raw === "developer" || raw === "hidden" || raw === "model_only"
    ? raw
    : "chat";
}

function isChatVisibleStep(step: MessageStepView) {
  const visibility = normalizeVisibility(step.visibility);
  return visibility === "chat";
}

function deduplicateHydratedLiveMessages(messages: StepTranscriptMessage[]): StepTranscriptMessage[] {
  const durableProtocolKeys = new Set<string>();
  const latestDurableUserIndex = findLatestDurableUserIndex(messages);
  const latestDurableAssistantIndex = findLatestDurableAssistantIndex(messages, latestDurableUserIndex);
  let latestDurableAssistant: StepTranscriptMessage | null = null;
  if (latestDurableAssistantIndex >= 0) {
    const message = messages[latestDurableAssistantIndex]!;
    if (isDurableTerminalMessage(message)) {
      latestDurableAssistant = message;
      for (const key of terminalProtocolKeys(message)) {
        durableProtocolKeys.add(key);
      }
    }
  }

  if (latestDurableAssistant) {
    for (const message of messages) {
      if (!message.live || !isAssistantMessage(message) || !isTerminalStreamMessage(message)) {
        continue;
      }
      if (messagesShareProtocolIdentity(message, durableProtocolKeys)) {
        mergeLiveWorkIntoDurableAssistant(latestDurableAssistant, message);
      }
    }
  }

  return messages.filter((message) => {
    if (!message.live || !isAssistantMessage(message) || !isTerminalStreamMessage(message)) {
      return true;
    }
    const duplicatedByProtocol = messagesShareProtocolIdentity(message, durableProtocolKeys);
    if (duplicatedByProtocol) {
      return false;
    }
    return true;
  });
}

function mergeLiveWorkIntoDurableAssistant(durable: StepTranscriptMessage, live: StepTranscriptMessage) {
  syncDuplicateContentStepTimeline(durable, live);
  const imported = live.steps
    .filter((step) => step.type !== "content" && isChatVisibleStep(step))
    .filter((step) => step.payload || step.action || step.error || step.type === "call");
  if (imported.length === 0) {
    return;
  }
  const existingProtocolKeys = new Set(
    durable.steps
      .filter((step) => step.type !== "content" && isChatVisibleStep(step))
      .map(stepProtocolIdentity)
      .filter(Boolean),
  );
  const additions: MessageStepView[] = [];
  for (const step of imported) {
    const protocolKey = stepProtocolIdentity(step);
    if (protocolKey && existingProtocolKeys.has(protocolKey)) {
      continue;
    }
    if (protocolKey) {
      existingProtocolKeys.add(protocolKey);
    }
    additions.push({
      ...step,
      step_id: durable.steps.some((item) => item.step_id === step.step_id)
        ? `${durable.message_id}:merged:${step.step_id}`
        : step.step_id,
      message_id: durable.message_id,
      metadata: {
        ...(step.metadata ?? {}),
        merged_from_live_message_id: live.message_id,
        merged_from_live_step_id: step.step_id,
      },
    });
  }
  if (additions.length === 0) {
    return;
  }
  durable.steps = mergeStepsByProtocolSequence(durable.steps, additions).map((step, order) => ({ ...step, order }));
}

function syncDuplicateContentStepTimeline(durable: StepTranscriptMessage, live: StepTranscriptMessage) {
  const durableContentByProtocolKey = new Map<string, MessageStepView>();
  for (const step of durable.steps) {
    if (step.type !== "content" || !isChatVisibleStep(step)) {
      continue;
    }
    const protocolKey = stepProtocolIdentity(step);
    if (protocolKey) {
      durableContentByProtocolKey.set(protocolKey, step);
    }
  }
  for (const step of live.steps) {
    if (step.type !== "content" || !isChatVisibleStep(step)) {
      continue;
    }
    const protocolKey = stepProtocolIdentity(step);
    const durableStep = protocolKey ? durableContentByProtocolKey.get(protocolKey) : null;
    if (!durableStep) {
      continue;
    }
    durableStep.order = stepTimelineOrder(step);
    durableStep.metadata = {
      ...(durableStep.metadata ?? {}),
      merged_from_live_message_id: live.message_id,
      merged_from_live_step_id: step.step_id,
    };
  }
}

function mergeStepsByProtocolSequence(
  durableSteps: MessageStepView[],
  liveAdditions: MessageStepView[],
): MessageStepView[] {
  return [...durableSteps, ...liveAdditions]
    .map((step, index) => ({ step, index }))
    .sort((a, b) => {
      const orderDelta = stepTimelineOrder(a.step) - stepTimelineOrder(b.step);
      if (orderDelta !== 0) {
        return orderDelta;
      }
      return a.index - b.index;
    })
    .map((item) => item.step);
}

function stepTimelineOrder(step: MessageStepView) {
  const metadataSequence = step.metadata?.sequence;
  if (typeof metadataSequence === "number") {
    return metadataSequence;
  }
  const sequence = (step as MessageStepView & { sequence?: unknown }).sequence;
  if (typeof sequence === "number") {
    return sequence;
  }
  return step.order ?? 0;
}

function messagesShareProtocolIdentity(message: StepTranscriptMessage, durableProtocolKeys: Set<string>) {
  if (durableProtocolKeys.size === 0) {
    return false;
  }
  return terminalProtocolKeys(message).some((key) => durableProtocolKeys.has(key));
}

function terminalProtocolKeys(message: StepTranscriptMessage): string[] {
  return message.steps
    .filter(isChatVisibleStep)
    .map(stepProtocolIdentity)
    .filter((key): key is string => Boolean(key));
}

function stepProtocolIdentity(step: MessageStepView): string | null {
  const metadata = step.metadata ?? {};
  const eventId = stringValue(metadata.event_id);
  if (eventId) {
    return `event:${eventId}`;
  }
  const blockId = stringValue(metadata.block_id);
  if (blockId) {
    return `block:${blockId}`;
  }
  const sequence = numberValue(metadata.sequence);
  if (sequence !== null) {
    return `sequence:${sequence}`;
  }
  return null;
}

function isAssistantMessage(message: StepTranscriptMessage) {
  return message.role === "ai" || message.role === "assistant";
}

function findLatestDurableAssistantIndex(messages: StepTranscriptMessage[], afterIndex: number) {
  for (let index = messages.length - 1; index > afterIndex; index -= 1) {
    const message = messages[index]!;
    if (!message.live && isAssistantMessage(message)) {
      return index;
    }
  }
  return -1;
}

function findLatestDurableUserIndex(messages: StepTranscriptMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]!;
    if (!message.live && (message.role === "human" || message.role === "user")) {
      return index;
    }
  }
  return -1;
}

function isTerminalStreamMessage(message: StepTranscriptMessage) {
  if (message.stream_status === "complete" || message.stream_status === "interrupted") {
    return true;
  }
  return message.steps.length > 0 && message.steps.every((step) => step.status === "success" || step.status === "error");
}

function isDurableTerminalMessage(message: StepTranscriptMessage) {
  return isTerminalStreamMessage(message);
}

function stringValue(value: unknown) {
  return typeof value === "string" && value ? value : null;
}

function numberValue(value: unknown) {
  return typeof value === "number" ? value : null;
}

function formatStepDuration(durationMs: number) {
  const seconds = Math.max(1, Math.round(durationMs / 1000));
  return `${seconds}s`;
}
