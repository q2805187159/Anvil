import { describe, expect, it } from "vitest";

import type { StepTranscriptMessage } from "@/src/core/threads/message-reducer";

import { buildTranscriptTurns } from "./transcript-turns";

function message(message_id: string, role: StepTranscriptMessage["role"], live = false): StepTranscriptMessage {
  return {
    message_id,
    role,
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
    live,
  };
}

describe("buildTranscriptTurns", () => {
  it("starts a new assistant-only turn when a live assistant follows completed assistant messages without a user boundary", () => {
    const turns = buildTranscriptTurns([
      message("user-1", "human"),
      message("assistant-1", "ai"),
      message("assistant-live", "ai", true),
    ]);

    expect(turns).toHaveLength(2);
    expect(turns[0]!.user?.message_id).toBe("user-1");
    expect(turns[0]!.assistantMessages.map((item) => item.message_id)).toEqual(["assistant-1"]);
    expect(turns[1]!.user).toBeNull();
    expect(turns[1]!.assistantMessages.map((item) => item.message_id)).toEqual(["assistant-live"]);
  });

  it("keeps adjacent durable assistant fragments in the same user turn", () => {
    const turns = buildTranscriptTurns([
      message("user-1", "human"),
      message("assistant-1", "ai"),
      message("assistant-2", "assistant"),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.assistantMessages.map((item) => item.message_id)).toEqual(["assistant-1", "assistant-2"]);
  });
});
