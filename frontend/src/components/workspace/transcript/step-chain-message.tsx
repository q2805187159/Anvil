"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  BotIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  FilePenLineIcon,
  Loader2Icon,
  TerminalIcon,
  XCircleIcon,
} from "lucide-react";

import type { MessageStepView, MessageView } from "@/src/core/contracts";
import type { StepTranscriptMessage } from "@/src/core/threads/message-reducer";
import { useI18n } from "@/src/core/i18n";
import { Button } from "@/src/components/ui";
import { cn } from "@/src/lib/utils";

import { ApprovalCard, ArtifactRefList } from "./common";
import { WorkspaceRichContent } from "../workspace-rich-content";

type UserStepMessageProps = {
  message: MessageView;
  canEdit: boolean;
  isEditing: boolean;
  editDraft: string;
  onEditDraftChange(value: string): void;
  onCopy(): void;
  onStartEdit(): void;
  onCancelEdit(): void;
  onSubmitEdit(): void;
  editor: React.ReactNode;
};

export function UserStepMessage({
  message,
  canEdit,
  isEditing,
  onCopy,
  onStartEdit,
  editor,
}: UserStepMessageProps) {
  return (
    <div className="group/user-message flex w-full min-w-0 justify-end pl-[16%] md:pl-[30%]">
      <div className="w-fit min-w-0 max-w-[44rem] overflow-visible px-0 py-1.5">
        {isEditing ? (
          editor
        ) : (
          <>
            <div className="rounded-[1.25rem] bg-[var(--panel-muted)] px-4 py-2.5 text-left">
              {message.artifact_refs.length > 0 ? (
                <ArtifactRefList artifactRefs={message.artifact_refs} className={message.content ? "mb-2 mt-0" : "mt-0"} />
              ) : null}
              {message.content ? <WorkspaceRichContent content={message.content} className="text-left text-[13px] leading-[1.5]" /> : null}
            </div>
            <div className="mt-1 flex items-center justify-end gap-1 opacity-0 transition group-hover/user-message:opacity-100 focus-within:opacity-100">
              <Button variant="ghost" size="icon" className="size-7" aria-label="Copy message" onClick={onCopy}>
                <CopyIcon className="size-3.5" />
              </Button>
              {canEdit ? (
                <Button variant="ghost" size="icon" className="size-7" aria-label="Edit message" onClick={onStartEdit}>
                  <FilePenLineIcon className="size-3.5" />
                </Button>
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function StepChainMessage({
  messages,
  onCopyMessage,
}: {
  messages: StepTranscriptMessage[];
  onCopyMessage(content: string): Promise<void>;
}) {
  const { t } = useI18n();
  const steps = useMemo(
    () =>
      messages
        .flatMap((message, messageIndex) => {
          const sourceHasToolWork =
            (message.tool_calls ?? []).length > 0 || (message.steps ?? []).some((step) => step.type === "call");
          const sequence = typeof message.sequence === "number" ? message.sequence : messageIndex;
          return (message.steps ?? []).filter(isChatVisibleStep).map((step) => ({
            ...step,
            messageStatus: message.stream_status,
            sourceHasToolWork,
            messageIndex: sequence,
          }));
        })
        .sort((a, b) => a.messageIndex - b.messageIndex || stepTimelineOrder(a) - stepTimelineOrder(b)),
    [messages],
  );
  const timelineItems = useMemo(() => groupAssistantTimeline(steps), [steps]);
  const workSteps = steps.filter((step) => step.type !== "content");
  const contentStepCount = steps.filter((step) => step.type === "content" && step.payload).length;
  const hasRunning = steps.some((step) => step.status === "running" || step.status === "pending");
  const hasTerminalStreamStatus = messages.some((message) => Boolean(message.stream_status));
  const hasOpenLiveMessage = messages.some((message) => message.live && !message.stream_status);
  const isCompleted = !hasRunning && !hasOpenLiveMessage && (hasTerminalStreamStatus || workSteps.length > 0 || contentStepCount > 0);
  const interrupted = messages.some((message) => message.stream_status === "interrupted");
  if (steps.length === 0 && !messages.some((message) => message.approval)) {
    return null;
  }

  return (
    <div className="flex w-full min-w-0 justify-start">
      <div className="w-full min-w-0 overflow-hidden px-0 py-1.5">
        <div className="mb-1 flex items-center gap-2 text-[11px] text-[var(--muted)]">
          <span>{t.transcript.assistant}</span>
          {interrupted ? <span className="rounded-full border border-[var(--warning)]/25 px-2 py-0.5 text-[var(--warning)]">{t.transcript.interrupted}</span> : null}
        </div>

        <div className="min-w-0 space-y-1">
          {timelineItems.length > 0 ? <AssistantTimeline items={timelineItems} completed={isCompleted} onCopyMessage={onCopyMessage} /> : null}

          {!isCompleted ? <ThinkingPulse /> : null}

          {messages.map((message) => (message.approval ? <ApprovalCard key={`${message.message_id}-approval`} approval={message.approval} compact /> : null))}

          {messages.flatMap((message) => message.artifact_refs).length > 0 ? (
            <ArtifactRefList artifactRefs={messages.flatMap((message) => message.artifact_refs)} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function stepTimelineOrder(step: MessageStepView) {
  if (typeof step.sequence === "number") {
    return step.sequence;
  }
  const metadataSequence = step.metadata?.sequence;
  if (typeof metadataSequence === "number") {
    return metadataSequence;
  }
  return step.order ?? 0;
}

function isChatVisibleStep(step: MessageStepView) {
  const visibility = step.visibility ?? "chat";
  return visibility === "chat";
}

function FoldedStepGroup({
  title,
  expanded,
  onToggle,
  children,
}: {
  title: string;
  expanded: boolean;
  onToggle(): void;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={onToggle}
        className="flex max-w-full items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-xs text-[var(--muted)] transition hover:bg-[var(--primary-soft)] hover:text-[var(--ink)]"
      >
        {expanded ? <ChevronDownIcon className="size-3.5 shrink-0" /> : <ChevronRightIcon className="size-3.5 shrink-0" />}
        <StepTitleText>{title}</StepTitleText>
      </button>
      {expanded ? <div className="mt-1.5">{children}</div> : null}
    </div>
  );
}

function StepRow({
  step,
  defaultExpanded = false,
  compact = false,
}: {
  step: MessageStepView;
  defaultExpanded?: boolean;
  compact?: boolean;
}) {
  if (isSubagentStep(step)) {
    return <SubagentStepRow step={step} defaultExpanded={defaultExpanded} compact={compact} />;
  }
  return <NormalStepRow step={step} defaultExpanded={defaultExpanded} compact={compact} />;
}

type WorkTimelineItem =
  | { kind: "thinking"; step: MessageStepView }
  | { kind: "work"; id: string; steps: MessageStepView[] };

type AssistantTimelineItem =
  | WorkTimelineItem
  | { kind: "content"; step: MessageStepView };

function AssistantTimeline({
  items,
  completed = false,
  onCopyMessage,
}: {
  items: AssistantTimelineItem[];
  completed?: boolean;
  onCopyMessage(content: string): Promise<void>;
}) {
  return (
    <div className="space-y-1.5" data-step-timeline>
      {items.map((item, index) => {
        if (item.kind === "thinking") {
          return <ThinkingStepBlock key={item.step.step_id} step={item.step} />;
        }
        if (item.kind === "content") {
          return <ContentStepBlock key={item.step.step_id} step={item.step} onCopyMessage={onCopyMessage} />;
        }
        const hasLaterThinking = items.slice(index + 1).some((next) => next.kind === "thinking");
        const subagentOnly = item.steps.every(isSubagentStep);
        return <ToolStepSummary key={item.id} steps={item.steps} completed={completed} defaultExpanded={!completed && !hasLaterThinking && !subagentOnly} />;
      })}
    </div>
  );
}

function groupAssistantTimeline(steps: MessageStepView[]): AssistantTimelineItem[] {
  const items: AssistantTimelineItem[] = [];
  let pendingWork: MessageStepView[] = [];
  const flushWork = () => {
    if (pendingWork.length === 0) {
      return;
    }
    items.push({
      kind: "work",
      id: pendingWork.map((step) => step.step_id).join("|"),
      steps: pendingWork,
    });
    pendingWork = [];
  };
  for (const step of steps) {
    if (step.type === "content") {
      flushWork();
      if (step.payload) {
        items.push({ kind: "content", step });
      }
      continue;
    }
    if (step.type === "thinking" && !isSubagentStep(step)) {
      flushWork();
      items.push({ kind: "thinking", step });
      continue;
    }
    pendingWork.push(step);
  }
  flushWork();
  return items;
}

function ContentStepBlock({
  step,
  onCopyMessage,
}: {
  step: MessageStepView;
  onCopyMessage(content: string): Promise<void>;
}) {
  return (
    <div className="group min-w-0 py-1">
      <WorkspaceRichContent content={step.payload ?? ""} className="text-[13px] leading-[1.5]" />
      <div className="mt-1.5 flex items-center justify-start opacity-0 transition group-hover:opacity-80">
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label="Copy message"
          onClick={() => void onCopyMessage(step.payload ?? "")}
        >
          <CopyIcon className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

function ThinkingStepBlock({ step }: { step: MessageStepView }) {
  const payload = (step.payload ?? "").trim();
  const content = step.error || payload || step.action || "";
  if (!content) {
    return null;
  }
  return (
    <div className="min-w-0 py-1 text-[13px] leading-6 text-[color-mix(in_srgb,var(--ink)_88%,var(--muted)_12%)]">
      <WorkspaceRichContent content={content} className="text-[13px] leading-6" />
    </div>
  );
}

function ToolStepSummary({
  steps,
  completed = false,
  defaultExpanded = false,
}: {
  steps: MessageStepView[];
  completed?: boolean;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const stepKey = steps.map((step) => `${step.step_id}:${step.status}`).join("|");
  const resetKey = `${stepKey}:${defaultExpanded ? "open" : "closed"}`;
  const previousResetKeyRef = useRef(resetKey);
  useEffect(() => {
    if (previousResetKeyRef.current === resetKey) {
      return;
    }
    previousResetKeyRef.current = resetKey;
    setExpanded(defaultExpanded);
  }, [defaultExpanded, resetKey]);
  const running = steps.some((step) => step.status === "running" || step.status === "pending");
  const failed = steps.some((step) => step.status === "error");
  const title = summarizeWorkSteps(steps, { completed: completed || !running });
  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => setExpanded((current) => !current)}
        className="flex max-w-full items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-xs text-[var(--muted)] transition hover:bg-[var(--primary-soft)] hover:text-[var(--ink)]"
      >
        {failed ? (
          <XCircleIcon className="size-3.5 shrink-0 text-[var(--danger)]" />
        ) : running ? (
          <Loader2Icon className="size-3.5 shrink-0 animate-spin text-[var(--primary)]" />
        ) : hasEditWork(steps) ? (
          <FilePenLineIcon className="size-3.5 shrink-0 text-[var(--primary)]" />
        ) : (
          <TerminalIcon className="size-3.5 shrink-0 text-[var(--primary)]" />
        )}
        <StepTitleText className="flex-1">{title}</StepTitleText>
        {expanded ? <ChevronDownIcon className="size-3.5 shrink-0" /> : <ChevronRightIcon className="size-3.5 shrink-0" />}
      </button>
      {expanded ? (
        <div className="mt-1.5 space-y-1 border-l border-[var(--line)] pl-3">
          <StepRows steps={steps} />
        </div>
      ) : null}
    </div>
  );
}

function ThinkingPulse() {
  return (
    <div className="py-1 text-[13px] leading-6 text-[var(--muted)] motion-safe:animate-pulse" data-thinking-pulse>
      正在思考
    </div>
  );
}

function StepRows({ steps }: { steps: MessageStepView[] }) {
  return (
    <>
      {steps.map((step) => (
        <StepRow key={step.step_id} step={step} defaultExpanded={false} compact />
      ))}
    </>
  );
}

function summarizeWorkSteps(steps: MessageStepView[], options: { completed: boolean }): string {
  if (steps.length === 0) {
    return "已完成";
  }
  const commandCount = steps.filter(isCommandStep).length;
  const editedFiles = editedFileNames(steps);
  const subagentCount = steps.filter(isSubagentStep).length;
  const toolCount = steps.filter((step) => step.type === "call" && !isCommandStep(step) && !isEditStep(step) && !isSubagentStep(step)).length;
  const prefix = options.completed ? "已" : "正在";
  const parts: string[] = [];
  if (editedFiles.length > 0) {
    parts.push(`${prefix}编辑 ${editedFiles.length} 个文件`);
  }
  if (commandCount > 0) {
    parts.push(`${prefix}运行 ${commandCount} 条命令`);
  }
  if (subagentCount > 0) {
    parts.push(`${prefix}处理 ${subagentCount} 个子代理`);
  }
  if (toolCount > 0) {
    parts.push(`${prefix}调用 ${toolCount} 个工具`);
  }
  return parts.length > 0 ? parts.join(" ") : `${prefix}运行 ${steps.length} 条消息`;
}

function hasEditWork(steps: MessageStepView[]) {
  return steps.some(isEditStep);
}

function isCommandStep(step: MessageStepView) {
  const name = String(step.tool_name ?? "");
  return name === "run_command" || name === "process" || name === "bash" || step.language === "shell";
}

function isEditStep(step: MessageStepView) {
  const name = String(step.tool_name ?? "");
  return name === "write_file" || name === "patch_file" || /^已编辑\b/.test(step.title ?? "");
}

function editedFileNames(steps: MessageStepView[]) {
  const names = new Set<string>();
  for (const step of steps) {
    if (!isEditStep(step)) {
      continue;
    }
    const fromAction = parsePathFromAction(step.action);
    const fromTitle = (step.title ?? "").replace(/^已编辑\s*/, "").trim();
    const path = fromAction || fromTitle;
    names.add(path || step.step_id);
  }
  return [...names];
}

function parsePathFromAction(action: string | null | undefined) {
  if (!action) {
    return "";
  }
  try {
    const parsed = JSON.parse(action) as { path?: unknown; file_path?: unknown };
    const path = parsed.path ?? parsed.file_path;
    return typeof path === "string" ? path.trim() : "";
  } catch {
    return "";
  }
}

function NormalStepRow({
  step,
  defaultExpanded = false,
  compact = false,
}: {
  step: MessageStepView;
  defaultExpanded?: boolean;
  compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const running = step.status === "running" || step.status === "pending";
  const title = step.type === "thinking" && running ? "Analyzing..." : step.title || "Step";
  const displayTitle = running && step.type === "call" && step.tool_name !== "subagent" ? "正在运行..." : title;
  const hasDetails = Boolean(step.action || step.payload || step.error);

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((current) => !current)}
        className={cn(
          "flex w-full min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-xs text-[var(--muted)] transition hover:bg-[var(--primary-soft)] hover:text-[var(--ink)]",
          compact && "py-0.5",
        )}
      >
        <StepStatusIcon status={step.status} type={step.type} />
        <StepTitleText className="flex-1">
          {displayTitle}
          {step.duration ? <span className="ml-1 whitespace-nowrap text-[var(--muted)]">({step.duration})</span> : null}
        </StepTitleText>
        {hasDetails ? expanded ? <ChevronDownIcon className="size-3.5 shrink-0" /> : <ChevronRightIcon className="size-3.5 shrink-0" /> : null}
      </button>
      {expanded && hasDetails ? <StepCodeBlock step={step} /> : null}
    </div>
  );
}

function SubagentStepRow({
  step,
  defaultExpanded = false,
  compact = false,
}: {
  step: MessageStepView;
  defaultExpanded?: boolean;
  compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const running = step.status === "running" || step.status === "pending";
  const failed = step.status === "error";
  const prompt = metadataText(step, "prompt") || step.action || metadataText(step, "prompt_preview");
  const result = (failed ? step.error : step.payload)?.trim();
  const taskId = metadataText(step, "subagent_task_id") || step.tool_call_id || "";
  const childThreadId = metadataText(step, "child_thread_id");
  const title = subagentDisplayTitle(step);
  const hasDetails = Boolean(prompt || result || childThreadId || taskId);

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((current) => !current)}
        className={cn(
          "flex w-full min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-xs text-[var(--muted)] transition hover:bg-[var(--primary-soft)] hover:text-[var(--ink)]",
          compact && "py-0.5",
        )}
      >
        <StepStatusIcon status={step.status} type="subagent" />
        <StepTitleText className="flex-1">
          {title}
          {step.duration ? <span className="ml-1 whitespace-nowrap text-[var(--muted)]">({step.duration})</span> : null}
        </StepTitleText>
        {hasDetails ? expanded ? <ChevronDownIcon className="size-3.5 shrink-0" /> : <ChevronRightIcon className="size-3.5 shrink-0" /> : null}
      </button>
      {expanded && hasDetails ? (
        <div className="mt-1 rounded-[0.75rem] border border-[var(--line)] bg-[color-mix(in_srgb,var(--panel-muted)_82%,var(--panel)_18%)] px-2 py-1.5">
          <div className="grid gap-2">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[var(--muted)]">
              {taskId ? <span className="rounded-md border border-[var(--line)] px-1.5 py-0.5 font-[var(--mono-font)]">{taskId}</span> : null}
              {childThreadId ? <span className="rounded-md border border-[var(--line)] px-1.5 py-0.5 font-[var(--mono-font)]">{childThreadId}</span> : null}
              {running ? <span>等待子代理返回结果</span> : null}
            </div>
            {prompt ? (
              <section className="min-w-0">
                <div className="mb-1 text-[11px] font-medium text-[var(--muted)]">Prompt</div>
                <pre className="max-h-[220px] min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[var(--line)] bg-[var(--panel)] px-2 py-1.5 font-[var(--mono-font)] text-[12px] leading-5 text-[color-mix(in_srgb,var(--ink)_80%,var(--muted)_20%)] [overflow-wrap:anywhere]">
                  {prompt}
                </pre>
              </section>
            ) : null}
            {result ? (
              <section className="min-w-0">
                <div className="mb-1 text-[11px] font-medium text-[var(--muted)]">{failed ? "错误" : "最终结果"}</div>
                <div className="min-w-0 rounded-md border border-[var(--line)] bg-[var(--panel)] px-2 py-1.5">
                  <WorkspaceRichContent content={result} className="text-[12px] leading-5" />
                </div>
              </section>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StepTitleText({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      data-step-title
      className={cn(
        "min-w-0 overflow-hidden break-words text-left leading-[1.45] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] [overflow-wrap:anywhere]",
        className,
      )}
    >
      {children}
    </span>
  );
}

function isSubagentStep(step: MessageStepView) {
  return step.tool_name === "subagent" || Boolean(step.metadata?.subagent_task_id);
}

function metadataText(step: MessageStepView, key: string) {
  const value = step.metadata?.[key];
  return typeof value === "string" && value.trim() ? value : "";
}

function subagentDisplayTitle(step: MessageStepView) {
  const preview = metadataText(step, "prompt_preview");
  const suffix = preview ? ` ${preview}` : "";
  if (step.status === "error") {
    return `子代理失败${suffix}`;
  }
  if (step.status === "success") {
    return `已完成子代理${suffix}`;
  }
  return `正在运行子代理${suffix}`;
}

function StepStatusIcon({ status, type }: { status?: string; type?: string }) {
  if (status === "running" || status === "pending") {
    return <Loader2Icon className="size-3.5 shrink-0 animate-spin text-[var(--primary)]" />;
  }
  if (status === "error") {
    return <XCircleIcon className="size-3.5 shrink-0 text-[var(--danger)]" />;
  }
  if (type === "thinking") {
    return <BrainCircuitIcon className="size-3.5 shrink-0 text-[var(--primary)]" />;
  }
  if (type === "subagent") {
    return <BotIcon className="size-3.5 shrink-0 text-[var(--primary)]" />;
  }
  if (type === "call") {
    return <TerminalIcon className="size-3.5 shrink-0 text-[var(--primary)]" />;
  }
  return <CheckCircle2Icon className="size-3.5 shrink-0 text-emerald-500" />;
}

function StepCodeBlock({ step }: { step: MessageStepView }) {
  const action = step.action?.trim();
  const payload = (step.payload ?? "").trim();
  const language = step.language || "text";
  const body = [action ? shellPrefix(language, action) : "", payload].filter(Boolean).join(payload && action ? "\n\n" : "");

  if (!body && !step.error) {
    return null;
  }

  return (
    <div className="relative mt-1 max-w-full overflow-hidden rounded-[0.75rem] border border-[var(--line)] bg-[color-mix(in_srgb,var(--panel-muted)_84%,var(--panel-strong)_16%)]">
      <div className="border-b border-[var(--line)] px-2 py-0.5 text-[10px] text-[var(--muted)]">
        {language === "shell" ? "Shell" : language}
      </div>
      <pre className="max-h-[320px] max-w-full overflow-auto whitespace-pre-wrap break-words px-2 py-1.5 pb-6 font-[var(--mono-font)] text-[11px] leading-5 text-[color-mix(in_srgb,var(--ink)_78%,var(--muted)_22%)] [overflow-wrap:anywhere]">
        <code>{step.error || body}</code>
      </pre>
      <div className="absolute bottom-1.5 right-2 flex items-center gap-1 text-[12px] text-[var(--muted)]">
        <StepStatusIcon status={step.status} type={step.type} />
        <span>{step.status === "error" ? "失败" : step.status === "success" ? "成功" : "运行中"}</span>
      </div>
    </div>
  );
}

function shellPrefix(language: string, action: string) {
  if (language !== "shell") {
    return action;
  }
  return action.startsWith("$") ? action : `$ ${action}`;
}
