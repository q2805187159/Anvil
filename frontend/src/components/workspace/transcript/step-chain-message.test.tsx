import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MessageView } from "@/src/core/contracts";
import { I18nProvider } from "@/src/core/i18n";

import { StepChainMessage } from "./step-chain-message";

function assistantMessage(steps: MessageView["steps"]): MessageView {
  return {
    message_id: "assistant-1",
    role: "ai",
    content: "",
    steps,
    content_blocks: [],
    reasoning: null,
    tool_calls: [],
    tool_call_id: null,
    name: null,
    status: null,
    stream_status: null,
    artifact_refs: [],
    approval: null,
  };
}

describe("StepChainMessage rows", () => {
  it("renders subagents as clean expandable rows", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            assistantMessage([
              {
                step_id: "assistant-1:subagent:task-1",
                message_id: "assistant-1",
                type: "thinking",
                title: "正在运行子代理 生成任务数据",
                action: "创建 tasks.json 示例数据",
                status: "running",
                duration: null,
                duration_ms: null,
                payload: "",
                language: "text",
                tool_name: "subagent",
                tool_call_id: "task-1",
                order: 0,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: { subagent_task_id: "task-1", prompt_preview: "生成任务数据", prompt: "创建 tasks.json 示例数据" },
                visibility: "chat",
              },
              {
                step_id: "assistant-1:subagent:task-2",
                message_id: "assistant-1",
                type: "thinking",
                title: "已完成子代理 分析任务",
                action: "读取 tasks.json 并生成分析报告",
                status: "success",
                duration: "4s",
                duration_ms: 4_000,
                payload: "分析报告已生成。",
                language: "text",
                tool_name: "subagent",
                tool_call_id: "task-2",
                order: 1,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: { subagent_task_id: "task-2", prompt_preview: "分析任务", prompt: "读取 tasks.json 并生成分析报告" },
                visibility: "chat",
              },
              {
                step_id: "assistant-1:call:subagent-control",
                message_id: "assistant-1",
                type: "call",
                title: "已运行 Subagent Control",
                action: "{\"action\":\"join\"}",
                status: "success",
                duration: "1s",
                duration_ms: 1_000,
                payload: "internal polling",
                language: "json",
                tool_name: "subagent",
                tool_call_id: "call-join",
                order: 2,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: {},
                visibility: "hidden",
              },
            ]),
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("button", { name: /正在处理 2 个子代理/i })).toBeInTheDocument();
    expect(screen.queryByText(/Subagent Control/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /正在处理 2 个子代理/i }));
    expect(screen.getByRole("button", { name: /正在运行子代理 生成任务数据/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成子代理 分析任务/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /已完成子代理 分析任务/i }));

    expect(screen.getByText("Prompt")).toBeInTheDocument();
    expect(screen.getByText(/读取 tasks\.json 并生成分析报告/i)).toBeInTheDocument();
    expect(screen.getByText("最终结果")).toBeInTheDocument();
    expect(screen.getByText(/分析报告已生成/i)).toBeInTheDocument();
  });

  it("clamps long step headers to two lines", () => {
    const longCommand = `已运行 python3 -c "${"from pptx import Presentation; ".repeat(18)}"`;

    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            assistantMessage([
              {
                step_id: "assistant-1:call:long-command",
                message_id: "assistant-1",
                type: "call",
                title: longCommand,
                action: longCommand.replace(/^已运行\s+/, ""),
                status: "success",
                duration: "4s",
                duration_ms: 4_000,
                payload: "",
                language: "shell",
                tool_name: "run_command",
                tool_call_id: "call-long",
                order: 0,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: {},
                visibility: "chat",
              },
            ]),
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /已运行 1 条命令/i }));

    const row = screen.getByRole("button", { name: /已运行 python3 -c/i });
    const title = row.querySelector("[data-step-title]");

    expect(title).not.toBeNull();
    expect(title?.className).toContain("[-webkit-line-clamp:2]");
    expect(title?.className).toContain("overflow-hidden");
  });

  it("renders hydrated terminal steps folded with one final answer", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            assistantMessage([
              {
                step_id: "assistant-1:thinking",
                message_id: "assistant-1",
                type: "thinking",
                title: "已思考 <1 秒",
                action: null,
                status: "success",
                duration: "<1s",
                duration_ms: 0,
                payload: "durable thought",
                language: "text",
                tool_name: null,
                tool_call_id: null,
                order: 0,
                started_at: "2026-05-03T12:00:00.000Z",
                completed_at: "2026-05-03T12:00:00.000Z",
                error: null,
                metadata: {},
                visibility: "chat",
              },
              {
                step_id: "assistant-1:call:list-dir",
                message_id: "assistant-1",
                type: "call",
                title: "已运行 List Directory",
                action: "{\"path\":\"/mnt/user-data/workspace\"}",
                status: "success",
                duration: "<1s",
                duration_ms: 0,
                payload: "workspace listed",
                language: "json",
                tool_name: "list_dir",
                tool_call_id: "call-1",
                order: 1,
                started_at: "2026-05-03T12:00:00.000Z",
                completed_at: "2026-05-03T12:00:00.000Z",
                error: null,
                metadata: {},
                visibility: "chat",
              },
              {
                step_id: "assistant-1:content",
                message_id: "assistant-1",
                type: "content",
                title: "最终回答",
                action: null,
                status: "success",
                duration: "<1s",
                duration_ms: 0,
                payload: "Durable final answer.",
                language: "markdown",
                tool_name: null,
                tool_call_id: null,
                order: 2,
                started_at: "2026-05-03T12:00:00.000Z",
                completed_at: "2026-05-03T12:00:01.000Z",
                error: null,
                metadata: {},
                visibility: "chat",
              },
            ]),
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    expect(screen.getByText(/durable thought/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已调用 1 个工具/i })).toBeInTheDocument();
    expect(screen.getByText("Durable final answer.")).toBeInTheDocument();
    expect(screen.getAllByText("Durable final answer.")).toHaveLength(1);
    expect(screen.queryByText(/Analyzing/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/正在运行/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /已调用 1 个工具/i }));
    expect(screen.getByRole("button", { name: /已运行 List Directory/i })).toBeInTheDocument();
  });

  it("keeps thinking and tool groups in original order", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            assistantMessage([
              {
                step_id: "assistant-1:thinking:first",
                message_id: "assistant-1",
                type: "thinking",
                title: "已思考",
                action: null,
                status: "success",
                duration: null,
                duration_ms: null,
                payload: "first thought",
                language: "text",
                tool_name: null,
                tool_call_id: null,
                order: 0,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: {},
                visibility: "chat",
              },
              {
                step_id: "assistant-1:call:one",
                message_id: "assistant-1",
                type: "call",
                title: "已运行 first command",
                action: "first command",
                status: "success",
                duration: null,
                duration_ms: null,
                payload: "",
                language: "shell",
                tool_name: "run_command",
                tool_call_id: "call-1",
                order: 1,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: {},
                visibility: "chat",
              },
              {
                step_id: "assistant-1:thinking:second",
                message_id: "assistant-1",
                type: "thinking",
                title: "已思考",
                action: null,
                status: "success",
                duration: null,
                duration_ms: null,
                payload: "second thought",
                language: "text",
                tool_name: null,
                tool_call_id: null,
                order: 2,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: {},
                visibility: "chat",
              },
              {
                step_id: "assistant-1:call:two",
                message_id: "assistant-1",
                type: "call",
                title: "已运行 second command",
                action: "second command",
                status: "success",
                duration: null,
                duration_ms: null,
                payload: "",
                language: "shell",
                tool_name: "run_command",
                tool_call_id: "call-2",
                order: 3,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: {},
                visibility: "chat",
              },
            ]),
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    const timelineTexts = Array.from(document.querySelectorAll("[data-step-timeline] > *"))
      .map((element) => element.textContent?.replace(/\s+/g, " ").trim())
      .filter(Boolean);

    expect(timelineTexts.slice(0, 4)).toEqual([
      "first thought",
      "已运行 1 条命令",
      "second thought",
      "已运行 1 条命令",
    ]);
  });

  it("keeps content before later tool work when sequence says so", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            assistantMessage([
              {
                step_id: "assistant-1:content",
                message_id: "assistant-1",
                type: "content",
                title: "最终回答",
                action: null,
                status: "success",
                duration: null,
                duration_ms: null,
                payload: "Visible text before tool.",
                language: "markdown",
                tool_name: null,
                tool_call_id: null,
                order: 99,
                sequence: 1,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: { sequence: 1 },
                visibility: "chat",
              },
              {
                step_id: "assistant-1:call:late",
                message_id: "assistant-1",
                type: "call",
                title: "已运行 late command",
                action: "late command",
                status: "success",
                duration: null,
                duration_ms: null,
                payload: "",
                language: "shell",
                tool_name: "run_command",
                tool_call_id: "call-late",
                order: 0,
                sequence: 2,
                started_at: null,
                completed_at: null,
                error: null,
                metadata: { sequence: 2 },
                visibility: "chat",
              },
            ]),
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    const timelineTexts = Array.from(document.querySelectorAll("[data-step-timeline] > *"))
      .map((element) => element.textContent?.replace(/\s+/g, " ").trim())
      .filter(Boolean);

    expect(timelineTexts[0]).toContain("Visible text before tool.");
    expect(timelineTexts[1]).toContain("已运行 1 条命令");
  });

  it("does not render hidden provider reasoning in the main chat", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            assistantMessage([
              {
                step_id: "assistant-1:thinking:hidden",
                message_id: "assistant-1",
                type: "thinking",
                title: "Provider reasoning",
                action: null,
                status: "success",
                duration: "<1s",
                duration_ms: 0,
                payload: "private provider thought",
                language: "text",
                tool_name: null,
                tool_call_id: null,
                order: 0,
                started_at: "2026-05-03T12:00:00.000Z",
                completed_at: "2026-05-03T12:00:00.000Z",
                error: null,
                metadata: {},
                visibility: "hidden",
              },
              {
                step_id: "assistant-1:content",
                message_id: "assistant-1",
                type: "content",
                title: "最终回答",
                action: null,
                status: "success",
                duration: "<1s",
                duration_ms: 0,
                payload: "Visible final answer.",
                language: "markdown",
                tool_name: null,
                tool_call_id: null,
                order: 1,
                started_at: "2026-05-03T12:00:00.000Z",
                completed_at: "2026-05-03T12:00:01.000Z",
                error: null,
                metadata: {},
                visibility: "chat",
              },
            ]),
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    expect(screen.getByText("Visible final answer.")).toBeInTheDocument();
    expect(screen.queryByText(/private provider thought/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Provider reasoning/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Analyzing/i)).not.toBeInTheDocument();
  });

  it("keeps the latest live tool group expanded until a later thinking chunk arrives", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            {
              ...assistantMessage([
                {
                  step_id: "assistant-live:call:list-dir",
                  message_id: "assistant-live",
                  type: "call",
                  title: "已运行 List Directory",
                  action: "{\"path\":\"/mnt/user-data/workspace\"}",
                  status: "success",
                  duration: "<1s",
                  duration_ms: 0,
                  payload: "workspace listed",
                  language: "json",
                  tool_name: "list_dir",
                  tool_call_id: "call-1",
                  order: 0,
                  started_at: "2026-05-03T12:00:00.000Z",
                  completed_at: "2026-05-03T12:00:00.000Z",
                  error: null,
                  metadata: {},
                  visibility: "chat",
                },
              ]),
              live: true,
              sequence: 0,
            },
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("button", { name: /已调用 1 个工具/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已运行 List Directory/i })).toBeInTheDocument();
    expect(screen.getByText("正在思考")).toBeInTheDocument();
  });

  it("folds an earlier tool group when a later thinking chunk appears", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            {
              ...assistantMessage([
                {
                  step_id: "assistant-live:thinking:first",
                  message_id: "assistant-live",
                  type: "thinking",
                  title: "已思考",
                  action: null,
                  status: "success",
                  duration: null,
                  duration_ms: null,
                  payload: "first thought",
                  language: "text",
                  tool_name: null,
                  tool_call_id: null,
                  order: 0,
                  started_at: null,
                  completed_at: null,
                  error: null,
                  metadata: {},
                  visibility: "chat",
                },
                {
                  step_id: "assistant-live:call:list-dir",
                  message_id: "assistant-live",
                  type: "call",
                  title: "已运行 List Directory",
                  action: "{\"path\":\"/mnt/user-data/workspace\"}",
                  status: "success",
                  duration: "<1s",
                  duration_ms: 0,
                  payload: "workspace listed",
                  language: "json",
                  tool_name: "list_dir",
                  tool_call_id: "call-1",
                  order: 1,
                  started_at: null,
                  completed_at: null,
                  error: null,
                  metadata: {},
                  visibility: "chat",
                },
                {
                  step_id: "assistant-live:thinking:second",
                  message_id: "assistant-live",
                  type: "thinking",
                  title: "已思考",
                  action: null,
                  status: "running",
                  duration: null,
                  duration_ms: null,
                  payload: "second thought",
                  language: "text",
                  tool_name: null,
                  tool_call_id: null,
                  order: 2,
                  started_at: null,
                  completed_at: null,
                  error: null,
                  metadata: {},
                  visibility: "chat",
                },
              ]),
              live: true,
              sequence: 0,
            },
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    expect(screen.getByText("first thought")).toBeInTheDocument();
    expect(screen.getByText("second thought")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已调用 1 个工具/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /已运行 List Directory/i })).not.toBeInTheDocument();
  });

  it("folds hydrated terminal subagent-only steps after refresh", () => {
    render(
      <I18nProvider>
        <StepChainMessage
          messages={[
            assistantMessage([
              {
                step_id: "assistant-1:subagent:task-1",
                message_id: "assistant-1",
                type: "thinking",
                title: "已完成子代理 inspect config",
                action: "Inspect the repo layout.",
                status: "success",
                duration: "4s",
                duration_ms: 4_000,
                payload: "Subagent result is durable.",
                language: "text",
                tool_name: "subagent",
                tool_call_id: "task-1",
                order: 0,
                started_at: "2026-05-03T12:00:00.000Z",
                completed_at: "2026-05-03T12:00:04.000Z",
                error: null,
                metadata: {
                  subagent_task_id: "task-1",
                  prompt_preview: "inspect config",
                  prompt: "Inspect the repo layout.",
                },
                visibility: "chat",
              },
            ]),
          ]}
          onCopyMessage={vi.fn()}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("button", { name: /已处理 1 个子代理/i })).toBeInTheDocument();
    expect(screen.queryByText("Prompt")).not.toBeInTheDocument();
    expect(screen.queryByText("最终结果")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /已处理 1 个子代理/i }));

    expect(screen.getByRole("button", { name: /已完成子代理 inspect config/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /已完成子代理 inspect config/i }));
    expect(screen.getByText("Prompt")).toBeInTheDocument();
    expect(screen.getByText(/Inspect the repo layout\./i)).toBeInTheDocument();
    expect(screen.getByText("最终结果")).toBeInTheDocument();
    expect(screen.getByText(/Subagent result is durable\./i)).toBeInTheDocument();
  });
});
