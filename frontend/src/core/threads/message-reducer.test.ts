import { describe, expect, it } from "vitest";

import type { MessageView, RunStreamEvent } from "@/src/core/contracts";

import { reduceMessageSteps } from "./message-reducer";

function baseMessage(partial: Partial<MessageView>): MessageView {
  return {
    message_id: partial.message_id ?? "m1",
    role: partial.role ?? "ai",
    content: partial.content ?? "",
    steps: partial.steps ?? [],
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

describe("reduceMessageSteps", () => {
  it("updates steps by step_id without duplicating payload", () => {
    const events: RunStreamEvent[] = [
      {
        event: "step_started",
        data: {
          step: {
            step_id: "s1",
            message_id: "m1",
            type: "content",
            title: "最终回答",
            status: "running",
            payload: "",
            language: "markdown",
            order: 0,
          },
        },
      },
      { event: "step_delta", data: { message_id: "m1", step_id: "s1", payload_delta: "hel" } },
      { event: "step_delta", data: { message_id: "m1", step_id: "s1", payload_delta: "lo" } },
      {
        event: "step_updated",
        data: {
          step: {
            step_id: "s1",
            message_id: "m1",
            type: "content",
            title: "最终回答",
            status: "success",
            payload: "hello",
            language: "markdown",
            order: 0,
          },
        },
      },
    ];

    const reduced = reduceMessageSteps([], events);

    expect(reduced).toHaveLength(1);
    expect(reduced[0]!.content).toBe("hello");
    expect(reduced[0]!.steps).toHaveLength(1);
    expect(reduced[0]!.steps[0]!.payload).toBe("hello");
  });

  it("records summary folding state on completion", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1" })],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "think",
              message_id: "m1",
              type: "thinking",
              title: "Analyzing...",
              status: "running",
              payload: "",
              language: "text",
              order: 0,
            },
          },
        },
        { event: "summary_update", data: { message_id: "m1", title: "已运行 1 条消息", folded_step_count: 1 } },
        { event: "message_completed", data: { message_id: "m1", stream_status: "complete" } },
      ],
    );

    expect(reduced[0]!.step_summary).toEqual({
      title: "已运行 1 条消息",
      folded_step_count: 1,
      completed: true,
    });
  });

  it("marks leftover running steps as complete when the run completes", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1" })],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "think",
              message_id: "m1",
              type: "thinking",
              title: "Analyzing...",
              status: "running",
              payload: "model thought",
              language: "text",
              order: 0,
            },
          },
        },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced[0]!.steps[0]!.status).toBe("success");
    expect(reduced[0]!.steps[0]!.completed_at).toBeTruthy();
  });

  it("does not let a new run completion mutate hydrated running steps from older messages", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m-old",
          steps: [
            {
              step_id: "m-old:thinking",
              message_id: "m-old",
              type: "thinking",
              title: "Analyzing...",
              status: "running",
              payload: "old partial thought",
              language: "text",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m-live:thinking",
              message_id: "m-live",
              type: "thinking",
              title: "Analyzing...",
              status: "running",
              payload: "new live thought",
              language: "text",
              order: 0,
            },
          },
        },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["m-old", "m-live"]);
    expect(reduced[0]!.live).toBeUndefined();
    expect(reduced[0]!.steps[0]!.status).toBe("running");
    expect(reduced[0]!.steps[0]!.completed_at).toBeNull();
    expect(reduced[1]!.live).toBe(true);
    expect(reduced[1]!.steps[0]!.status).toBe("success");
    expect(reduced[1]!.steps[0]!.completed_at).toBeTruthy();
  });

  it("keeps hidden delegation steps out of synthesized visible content", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1" })],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "hidden-tool",
              message_id: "m1",
              type: "content",
              title: "internal",
              status: "success",
              payload: "[LOOP DETECTED] internal",
              language: "text",
              order: 0,
              visibility: "hidden",
            },
          },
        },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "final",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "visible answer",
              language: "markdown",
              order: 1,
            },
          },
        },
      ],
    );

    expect(reduced[0]!.content).toBe("visible answer");
    expect(reduced[0]!.steps).toHaveLength(2);
  });

  it("keeps model-only stream blocks out of visible assistant content", () => {
    const reduced = reduceMessageSteps(
      [],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:model-only",
              message_id: "m1",
              type: "content",
              title: "Internal bridge",
              status: "success",
              payload: "Images returned by view_image are attached below for visual analysis.",
              language: "text",
              order: 0,
              visibility: "model_only",
            },
          },
        },
        { event: "message_completed", data: { message_id: "m1", stream_status: "complete" } },
      ],
    );

    expect(reduced[0]!.content).toBe("");
    expect(reduced[0]!.steps[0]!.visibility).toBe("model_only");
  });

  it("uses stream envelope sequence for step order and ignores duplicate event ids", () => {
    const reduced = reduceMessageSteps(
      [],
      [
        {
          event: "step_started",
          event_id: "evt-tool",
          sequence: 2,
          data: {
            event_id: "evt-tool",
            sequence: 2,
            step: {
              step_id: "m1:tool",
              message_id: "m1",
              type: "call",
              title: "已运行工具",
              status: "success",
              payload: "{}",
              language: "json",
              order: 0,
            },
          },
        },
        {
          event: "step_started",
          event_id: "evt-thinking",
          sequence: 1,
          data: {
            event_id: "evt-thinking",
            sequence: 1,
            step: {
              step_id: "m1:thinking",
              message_id: "m1",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "first",
              language: "text",
              order: 10,
            },
          },
        },
        {
          event: "step_started",
          event_id: "evt-thinking",
          sequence: 1,
          data: {
            event_id: "evt-thinking",
            sequence: 1,
            step: {
              step_id: "m1:thinking-duplicate",
              message_id: "m1",
              type: "thinking",
              title: "duplicate",
              status: "success",
              payload: "duplicate",
              language: "text",
              order: 11,
            },
          },
        },
      ],
    );

    expect(reduced[0]!.steps.map((step) => step.step_id)).toEqual(["m1:thinking", "m1:tool"]);
    expect(reduced[0]!.steps.map((step) => step.metadata?.event_id)).toEqual(["evt-thinking", "evt-tool"]);
  });

  it("strips provider think tags from hydrated content steps", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m1",
          content: "Visible answer",
          steps: [
            {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "<think>private reasoning</think>\n\nVisible answer",
              language: "markdown",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [],
    );

    expect(reduced[0]!.content).toBe("Visible answer");
    expect(reduced[0]!.steps[0]!.payload).toBe("Visible answer");
  });

  it("does not render split provider think deltas as final answer content", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1" })],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "",
              language: "markdown",
              order: 0,
            },
          },
        },
        { event: "step_delta", data: { message_id: "m1", step_id: "m1:content", payload_delta: "<think>private" } },
        { event: "step_delta", data: { message_id: "m1", step_id: "m1:content", payload_delta: " reasoning</think>\n\nVisible answer" } },
      ],
    );

    expect(reduced[0]!.content).toBe("Visible answer");
    expect(reduced[0]!.steps[0]!.payload).toBe("Visible answer");
  });

  it("does not synthesize out-of-order step deltas as final answer content", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1", content: "" })],
      [{ event: "step_delta", data: { message_id: "m1", step_id: "late-thinking", payload_delta: "internal thought" } }],
    );

    expect(reduced[0]!.content).toBe("");
    expect(reduced[0]!.steps[0]!.type).toBe("thinking");
    expect(reduced[0]!.steps[0]!.visibility).toBe("hidden");
    expect(reduced[0]!.steps[0]!.payload).toBe("internal thought");
  });

  it("ignores reasoning deltas as durable visible thinking steps", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1", content: "" })],
      [
        { event: "reasoning_delta", data: { message_id: "m1", delta: "first thought" } },
        { event: "reasoning_completed", data: { message_id: "m1", duration_ms: 2_000 } },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:call:skill",
              message_id: "m1",
              type: "call",
              title: "已运行 Skill Content",
              status: "success",
              payload: "",
              language: "json",
              tool_name: "skill_content",
              tool_call_id: "call-skill",
              order: 1,
            },
          },
        },
        { event: "reasoning_delta", data: { message_id: "m1", delta: "second thought" } },
        { event: "message_completed", data: { message_id: "m1", stream_status: "complete" } },
      ],
    );

    expect(reduced[0]!.steps.map((step) => [step.type, step.payload, step.visibility])).toEqual([
      ["call", "", "chat"],
    ]);
  });

  it("does not synthesize thinking segments around tools from reasoning deltas", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1", content: "" })],
      [
        { event: "reasoning_delta", data: { message_id: "m1", delta: "first thought" } },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:call:file-info",
              message_id: "m1",
              type: "call",
              title: "已检查文件",
              status: "success",
              payload: "{}",
              language: "json",
              tool_name: "file_info",
              tool_call_id: "call-file-info",
              order: 1,
            },
          },
        },
        { event: "reasoning_delta", data: { message_id: "m1", delta: "second thought" } },
        { event: "message_completed", data: { message_id: "m1", stream_status: "complete" } },
      ],
    );

    expect(reduced[0]!.steps.map((step) => [step.type, step.payload, step.status])).toEqual([
      ["call", "{}", "success"],
    ]);
  });

  it("keeps reasoning deltas out of message steps even when repeated or cumulative", () => {
    const reduced = reduceMessageSteps(
      [baseMessage({ message_id: "m1", content: "" })],
      [
        { event: "reasoning_delta", data: { message_id: "m1", delta: "first" } },
        { event: "reasoning_delta", data: { message_id: "m1", delta: "first" } },
        { event: "reasoning_delta", data: { message_id: "m1", delta: "first second" } },
        { event: "reasoning_delta", data: { message_id: "m1", delta: " second" } },
      ],
    );

    expect(reduced[0]!.steps).toEqual([]);
  });

  it("treats repeated or cumulative step deltas as idempotent stream updates", () => {
    const reduced = reduceMessageSteps(
      [],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:call:file-info",
              message_id: "m1",
              type: "call",
              title: "已检查文件",
              status: "running",
              payload: "",
              language: "json",
              order: 0,
            },
          },
        },
        { event: "step_delta", data: { message_id: "m1", step_id: "m1:call:file-info", payload_delta: "{\"path\":\"a\"}" } },
        { event: "step_delta", data: { message_id: "m1", step_id: "m1:call:file-info", payload_delta: "{\"path\":\"a\"}" } },
        { event: "step_delta", data: { message_id: "m1", step_id: "m1:call:file-info", payload_delta: "{\"path\":\"a\",\"ok\":true}" } },
      ],
    );

    expect(reduced[0]!.steps[0]!.payload).toBe("{\"path\":\"a\",\"ok\":true}");
  });

  it("collapses adjacent repeated JSON tool payload snapshots from legacy durable data", () => {
    const payload = '{"path":"/mnt/user-data/outputs/plan.json","operation":"created"}';
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m1",
          steps: [
            {
              step_id: "m1:call:write-file",
              message_id: "m1",
              type: "call",
              title: "已编辑文件",
              status: "success",
              payload: `${payload}${payload}`,
              language: "json",
              tool_name: "write_file",
              tool_call_id: "call-write-file",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [],
    );

    expect(reduced[0]!.steps[0]!.payload).toBe(payload);
  });

  it("does not collapse distinct adjacent JSON tool payloads", () => {
    const first = '{"path":"a","operation":"created"}';
    const second = '{"path":"b","operation":"created"}';
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m1",
          steps: [
            {
              step_id: "m1:call:write-file",
              message_id: "m1",
              type: "call",
              title: "已编辑文件",
              status: "success",
              payload: `${first}${second}`,
              language: "json",
              tool_name: "write_file",
              tool_call_id: "call-write-file",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [],
    );

    expect(reduced[0]!.steps[0]!.payload).toBe(`${first}${second}`);
  });

  it("updates subagent steps by stable step id", () => {
    const reduced = reduceMessageSteps(
      [],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:subagent:task-1",
              message_id: "m1",
              type: "call",
              title: "正在运行子代理 inspect",
              status: "running",
              payload: "",
              language: "text",
              tool_name: "subagent",
              tool_call_id: "task-1",
              order: 0,
              metadata: { subagent_task_id: "task-1" },
            },
          },
        },
        {
          event: "step_updated",
          data: {
            step: {
              step_id: "m1:subagent:task-1",
              message_id: "m1",
              type: "call",
              title: "已完成子代理 inspect",
              status: "success",
              payload: "done",
              language: "text",
              tool_name: "subagent",
              tool_call_id: "task-1",
              order: 0,
              metadata: { subagent_task_id: "task-1" },
            },
          },
        },
      ],
    );

    expect(reduced).toHaveLength(1);
    expect(reduced[0]!.steps).toHaveLength(1);
    expect(reduced[0]!.steps[0]!.status).toBe("success");
    expect(reduced[0]!.steps[0]!.payload).toBe("done");
  });

  it("keeps hydrated terminal steps idempotent when duplicate updates arrive", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m1",
          content: "durable answer",
          steps: [
            {
              step_id: "m1:thinking",
              message_id: "m1",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "durable thought",
              language: "text",
              order: 0,
              completed_at: "2026-05-03T12:00:00.000Z",
              metadata: {},
              visibility: "chat",
            },
            {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "durable answer",
              language: "markdown",
              order: 1,
              completed_at: "2026-05-03T12:00:01.000Z",
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_updated",
          data: {
            step: {
              step_id: "m1:thinking",
              message_id: "m1",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "durable thought",
              language: "text",
              order: 0,
              completed_at: "2026-05-03T12:00:00.000Z",
            },
          },
        },
        {
          event: "step_updated",
          data: {
            step: {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "durable answer",
              language: "markdown",
              order: 1,
              completed_at: "2026-05-03T12:00:01.000Z",
            },
          },
        },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced[0]!.content).toBe("durable answer");
    expect(reduced[0]!.steps).toHaveLength(2);
    expect(reduced[0]!.steps.map((step) => step.step_id)).toEqual(["m1:thinking", "m1:content"]);
    expect(reduced[0]!.steps.map((step) => step.status)).toEqual(["success", "success"]);
    expect(reduced[0]!.steps.map((step) => step.completed_at)).toEqual([
      "2026-05-03T12:00:00.000Z",
      "2026-05-03T12:00:01.000Z",
    ]);
  });

  it("does not reopen hydrated terminal steps when replayed start events arrive", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m1",
          content: "durable answer",
          steps: [
            {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "durable answer",
              language: "markdown",
              order: 0,
              completed_at: "2026-05-03T12:00:01.000Z",
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "",
              language: "markdown",
              order: 0,
            },
          },
        },
      ],
    );

    expect(reduced[0]!.content).toBe("durable answer");
    expect(reduced[0]!.steps).toHaveLength(1);
    expect(reduced[0]!.steps[0]!.status).toBe("success");
    expect(reduced[0]!.steps[0]!.payload).toBe("durable answer");
    expect(reduced[0]!.steps[0]!.completed_at).toBe("2026-05-03T12:00:01.000Z");
  });

  it("preserves hydrated running step payload when reconnect starts the same step", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m1",
          steps: [
            {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "partial answer",
              language: "markdown",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m1:content",
              message_id: "m1",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "",
              language: "markdown",
              order: 0,
            },
          },
        },
        { event: "step_delta", data: { message_id: "m1", step_id: "m1:content", payload_delta: " continues" } },
      ],
    );

    expect(reduced[0]!.content).toBe("partial answer continues");
    expect(reduced[0]!.steps).toHaveLength(1);
    expect(reduced[0]!.steps[0]!.payload).toBe("partial answer continues");
    expect(reduced[0]!.steps[0]!.status).toBe("running");
  });

  it("preserves message sequence when later live assistant messages arrive", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "m1",
          steps: [
            {
              step_id: "m1-thinking",
              message_id: "m1",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "first",
              language: "text",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "m2-thinking",
              message_id: "m2",
              type: "thinking",
              title: "Analyzing...",
              status: "running",
              payload: "second",
              language: "text",
              order: 0,
              visibility: "chat",
            },
          },
        },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["m1", "m2"]);
    expect(reduced[0]!.sequence).toBe(0);
    expect(reduced[1]!.sequence).toBe(1);
  });

  it("keeps terminal live assistant messages without protocol identity even when content matches durable hydration", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Done.",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Done.",
              language: "markdown",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "",
              language: "markdown",
              order: 0,
            },
          },
        },
        { event: "step_delta", data: { message_id: "stream-final", step_id: "stream-final:content", payload_delta: "Done." } },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final", "stream-final"]);
    expect(reduced[1]!.content).toBe("Done.");
    expect(reduced[1]!.steps).toHaveLength(1);
    expect(reduced[2]!.content).toBe("Done.");
  });

  it("does not merge a new terminal live message into durable history only because content matches", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Done.",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Done.",
              language: "markdown",
              order: 0,
              metadata: { event_id: "run-old:000010", block_id: "old-content", sequence: 10 },
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          event_id: "run-new:000001",
          sequence: 1,
          data: {
            event_id: "run-new:000001",
            sequence: 1,
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Done.",
              language: "markdown",
              order: 0,
            },
          },
        },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final", "stream-final"]);
    expect(reduced[2]!.content).toBe("Done.");
  });

  it("treats run_completed as the terminal boundary for late stream events", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "running",
              payload: "",
              language: "markdown",
              order: 0,
            },
          },
        },
        { event: "step_delta", data: { message_id: "stream-final", step_id: "stream-final:content", payload_delta: "Done." } },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
        { event: "step_delta", data: { message_id: "stream-final", step_id: "stream-final:content", payload_delta: "Done." } },
      ],
    );

    const assistant = reduced.find((message) => message.message_id === "stream-final");
    expect(assistant?.content).toBe("Done.");
    expect(assistant?.steps.find((step) => step.type === "content")?.payload).toBe("Done.");
  });

  it("keeps terminal live thinking chains without protocol identity even when text matches durable hydration", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Visible answer",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:thinking",
              message_id: "durable-final",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "Need to inspect state.",
              language: "text",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Visible answer",
              language: "markdown",
              order: 1,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:thinking",
              message_id: "stream-final",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "Need to inspect state.",
              language: "text",
              order: 0,
            },
          },
        },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Visible answer",
              language: "markdown",
              order: 1,
            },
          },
        },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final", "stream-final"]);
    expect(reduced[1]!.steps.map((step) => step.payload)).toEqual(["Need to inspect state.", "Visible answer"]);
    expect(reduced[2]!.steps.map((step) => step.payload)).toEqual(["Need to inspect state.", "Visible answer"]);
  });

  it("does not merge terminal live thinking and tools into durable output without protocol identity", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Final answer.",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        { event: "reasoning_delta", data: { message_id: "stream-final", delta: "Need tools." } },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:call:one",
              message_id: "stream-final",
              type: "call",
              title: "已运行 Tool",
              status: "success",
              payload: "tool result",
              language: "json",
              tool_name: "tool",
              order: 1,
            },
          },
        },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 2,
            },
          },
        },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final", "stream-final"]);
    expect(reduced[1]!.steps.map((step) => [step.type, step.payload, step.message_id])).toEqual([
      ["content", "Final answer.", "durable-final"],
    ]);
    expect(reduced[2]!.steps.map((step) => [step.type, step.payload, step.message_id])).toEqual([
      ["call", "tool result", "stream-final"],
      ["content", "Final answer.", "stream-final"],
    ]);
  });

  it("does not hydrate terminal stream work into an existing assistant without protocol identity", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Final answer.",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:thinking:1",
              message_id: "durable-final",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "First durable thought.",
              language: "text",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 1,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        { event: "reasoning_delta", data: { message_id: "stream-final", delta: "Second live thought." } },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 1,
            },
          },
        },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final", "stream-final"]);
    expect(reduced[1]!.steps.map((step) => [step.type, step.payload])).toEqual([
      ["thinking", "First durable thought."],
      ["content", "Final answer."],
    ]);
    expect(reduced[2]!.steps.map((step) => [step.type, step.payload])).toEqual([
      ["content", "Final answer."],
    ]);
  });

  it("does not merge live thinking before hydrated durable tools without protocol identity", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Final answer.",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:call:one",
              message_id: "durable-final",
              type: "call",
              title: "已运行 Tool",
              status: "success",
              payload: "tool result",
              language: "json",
              tool_name: "tool",
              order: 1,
              metadata: {},
              visibility: "chat",
            },
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 2,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:thinking",
              message_id: "stream-final",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "I need to inspect the tool results first.",
              language: "text",
              order: 0,
            },
          },
        },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 2,
            },
          },
        },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final", "stream-final"]);
    expect(reduced[1]!.steps.map((step) => [step.type, step.payload])).toEqual([
      ["call", "tool result"],
      ["content", "Final answer."],
    ]);
    expect(reduced[2]!.steps.map((step) => [step.type, step.payload])).toEqual([
      ["thinking", "I need to inspect the tool results first."],
      ["content", "Final answer."],
    ]);
  });

  it("keeps later terminal live work separate from durable content without protocol identity", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Text before tool.",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Text before tool.",
              language: "markdown",
              order: 0,
              metadata: {},
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:call:late",
              message_id: "stream-final",
              type: "call",
              title: "已运行 Tool",
              status: "success",
              payload: "late tool result",
              language: "json",
              tool_name: "tool",
              order: 1,
            },
          },
        },
        {
          event: "step_started",
          data: {
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Text before tool.",
              language: "markdown",
              order: 0,
            },
          },
        },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final", "stream-final"]);
    expect(reduced[1]!.steps.map((step) => [step.type, step.payload])).toEqual([
      ["content", "Text before tool."],
    ]);
    expect(reduced[2]!.steps.map((step) => [step.type, step.payload])).toEqual([
      ["content", "Text before tool."],
      ["call", "late tool result"],
    ]);
  });

  it("merges durable and live work strictly by protocol sequence when present", () => {
    const reduced = reduceMessageSteps(
      [
        baseMessage({
          message_id: "user-1",
          role: "human",
          content: "Run the task",
        }),
        baseMessage({
          message_id: "durable-final",
          role: "ai",
          content: "Final answer.",
          stream_status: "complete",
          steps: [
            {
              step_id: "durable-final:content",
              message_id: "durable-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 0,
              metadata: { event_id: "run-1:000003", block_id: "content", sequence: 3 },
              visibility: "chat",
            },
          ],
        }),
      ],
      [
        {
          event: "step_started",
          event_id: "run-1:000001",
          sequence: 1,
          data: {
            event_id: "run-1:000001",
            sequence: 1,
            step: {
              step_id: "stream-final:thinking",
              message_id: "stream-final",
              type: "thinking",
              title: "已思考",
              status: "success",
              payload: "Think first.",
              language: "text",
              order: 100,
            },
          },
        },
        {
          event: "step_started",
          event_id: "run-1:000002",
          sequence: 2,
          data: {
            event_id: "run-1:000002",
            sequence: 2,
            step: {
              step_id: "stream-final:call:tool",
              message_id: "stream-final",
              type: "call",
              title: "已运行 Tool",
              status: "success",
              payload: "tool result",
              language: "json",
              tool_name: "tool",
              order: 101,
            },
          },
        },
        {
          event: "step_started",
          event_id: "run-1:000003",
          sequence: 3,
          data: {
            event_id: "run-1:000003",
            sequence: 3,
            step: {
              step_id: "stream-final:content",
              message_id: "stream-final",
              type: "content",
              title: "最终回答",
              status: "success",
              payload: "Final answer.",
              language: "markdown",
              order: 0,
            },
          },
        },
        { event: "message_completed", data: { message_id: "stream-final", stream_status: "complete" } },
        { event: "run_completed", data: { thread_id: "thread-a", stream_status: "complete", status: "completed" } },
      ],
    );

    expect(reduced.map((message) => message.message_id)).toEqual(["user-1", "durable-final"]);
    expect(reduced[1]!.steps.map((step) => [step.type, step.payload, step.metadata?.sequence])).toEqual([
      ["thinking", "Think first.", 1],
      ["call", "tool result", 2],
      ["content", "Final answer.", 3],
    ]);
  });
});
