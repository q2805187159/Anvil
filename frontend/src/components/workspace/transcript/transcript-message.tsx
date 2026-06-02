"use client";

import React from "react";

import type { MessageView } from "@/src/core/contracts";
import { useI18n } from "@/src/core/i18n";
import { cn } from "@/src/lib/utils";
import { Badge } from "@/src/components/ui/badge";
import { WorkspaceRichContent } from "../workspace-rich-content";
import { ApprovalCard, ArtifactRefList, labelForRole } from "./common";
import { ReasoningPanel } from "./reasoning-panel";
import { ToolBlock } from "./tool-block";

function compactText(value: string | null | undefined) {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function shouldRenderMessageContent(message: MessageView) {
  if (!message.content.trim() || message.role === "tool") {
    return false;
  }
  if (message.tool_calls.length === 0) {
    return true;
  }
  return compactText(message.content) !== compactText(message.reasoning?.text);
}

function shouldRenderMessage(message: MessageView) {
  if (message.role !== "ai" && message.role !== "assistant") {
    return true;
  }
  if (message.approval || message.tool_calls.length === 0) {
    return true;
  }
  return shouldRenderMessageContent(message);
}

export function TranscriptMessage({ message }: { message: MessageView }) {
  const { t } = useI18n();
  const isUser = message.role === "human" || message.role === "user";
  const isAssistant = message.role === "ai" || message.role === "assistant";
  const showContent = shouldRenderMessageContent(message);

  if (!shouldRenderMessage(message)) {
    return null;
  }

  return (
    <div className={cn("flex w-full min-w-0 px-1 md:px-4", isUser ? "justify-end pl-6 md:pl-10" : "justify-start pr-6 md:pr-10")}>
      <div
        className={cn(
          "w-full min-w-0 max-w-[min(40rem,82%)] rounded-[0.75rem] border px-3 py-2 shadow-[0_4px_6px_-2px_rgba(0,0,0,0.05),0_2px_4px_-1px_rgba(0,0,0,0.03)]",
          isUser
            ? "border-[var(--line)] bg-[color-mix(in_srgb,var(--panel-muted)_76%,white_24%)]"
            : isAssistant
              ? "max-w-[min(42rem,80%)] border-transparent bg-transparent px-0 py-0 shadow-none"
              : "border-[var(--line)] bg-[var(--panel)]",
        )}
      >
        <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-[0.12em] text-[var(--muted)]">
          <span>{labelForRole(message.role, t)}</span>
          {message.status ? <span>{message.status}</span> : null}
          {message.stream_status === "interrupted" ? <Badge tone="warning">{t.transcript.interrupted}</Badge> : null}
          {message.name ? <span>{message.name}</span> : null}
        </div>

        {message.approval ? <ApprovalCard approval={message.approval} /> : null}
        {message.reasoning ? (
          <ReasoningPanel
            reasoning={message.reasoning.text}
            collapseWhenComplete
            durationMs={message.reasoning.duration_ms}
          />
        ) : null}
        {showContent ? <WorkspaceRichContent content={message.content} /> : null}
        {message.artifact_refs.length > 0 ? <ArtifactRefList artifactRefs={message.artifact_refs} /> : null}

        {message.tool_calls.length > 0 ? (
          <div className="mt-2 space-y-2">
            {message.tool_calls.map((toolCall, index) => (
              <ToolBlock
                key={`${toolCall.tool_call_id ?? toolCall.name ?? "tool"}-${index}`}
                tool={{
                  tool_call_id: toolCall.tool_call_id,
                  message_id: message.message_id,
                  name: toolCall.name,
                  display_name: toolCall.display_name,
                  source_kind: toolCall.source_kind,
                  source_id: toolCall.source_id,
                  capability_group: toolCall.capability_group,
                  tool_execution_mode: toolCall.tool_execution_mode,
                  args: toolCall.args,
                  status: toolCall.status ?? (toolCall.result_text ? "completed" : "running"),
                  result_text: toolCall.result_text ?? null,
                  started_at: toolCall.started_at ?? null,
                  completed_at: toolCall.completed_at ?? null,
                  duration_ms: toolCall.duration_ms ?? null,
                }}
                artifactRefs={message.artifact_refs}
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
