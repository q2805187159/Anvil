import { describe, expect, it } from "vitest";

import type { ThreadView } from "@/src/core/contracts";

import { formatThreadActivityAge, formatThreadUpdatedAge, sortThreadsByRecency, threadActivityAt } from "./recency";

function thread(threadId: string, updatedAt: string): ThreadView {
  return {
    thread_id: threadId,
    title: threadId,
    status: "ready",
    updated_at: updatedAt,
    last_message_at: null,
    last_user_message_preview: null,
    has_pending_approval: false,
    has_active_subagent_tasks: false,
  };
}

describe("sortThreadsByRecency", () => {
  it("orders threads by latest updated_at and keeps deterministic ties", () => {
    expect(
      sortThreadsByRecency([
        thread("thread-b", "2026-05-23T09:00:00.000Z"),
        thread("thread-c", "2026-05-23T11:00:00.000Z"),
        thread("thread-a", "2026-05-23T09:00:00.000Z"),
      ]).map((item) => item.thread_id),
    ).toEqual(["thread-c", "thread-a", "thread-b"]);
  });

  it("uses last_message_at before settings-only updated_at changes", () => {
    expect(
      sortThreadsByRecency([
        {
          ...thread("thread-settings-edited", "2026-05-23T12:00:00.000Z"),
          last_message_at: "2026-05-23T09:00:00.000Z",
        },
        {
          ...thread("thread-latest-message", "2026-05-23T11:00:00.000Z"),
          last_message_at: "2026-05-23T11:00:00.000Z",
        },
      ]).map((item) => item.thread_id),
    ).toEqual(["thread-latest-message", "thread-settings-edited"]);
  });
});

describe("threadActivityAt", () => {
  it("uses latest message activity for active message threads", () => {
    expect(
      threadActivityAt({
        ...thread("thread-settings-edited", "2026-05-23T12:00:00.000Z"),
        last_message_at: "2026-05-23T09:00:00.000Z",
      }),
    ).toBe("2026-05-23T09:00:00.000Z");
  });

  it("falls back to updated_at for empty threads", () => {
    expect(threadActivityAt(thread("thread-empty", "2026-05-23T12:00:00.000Z"))).toBe(
      "2026-05-23T12:00:00.000Z",
    );
  });
});

describe("formatThreadUpdatedAge", () => {
  it("formats compact Chinese thread ages", () => {
    const now = Date.parse("2026-05-23T12:00:00.000Z");

    expect(formatThreadUpdatedAge("2026-05-23T11:59:30.000Z", "zh-CN", now)).toBe("刚刚");
    expect(formatThreadUpdatedAge("2026-05-23T10:00:00.000Z", "zh-CN", now)).toBe("2 小时");
    expect(formatThreadUpdatedAge("2026-05-19T12:00:00.000Z", "zh-CN", now)).toBe("4 天");
    expect(formatThreadUpdatedAge("2026-05-09T12:00:00.000Z", "zh-CN", now)).toBe("2 周");
  });

  it("formats compact English thread ages", () => {
    const now = Date.parse("2026-05-23T12:00:00.000Z");

    expect(formatThreadUpdatedAge("2026-05-23T11:59:30.000Z", "en-US", now)).toBe("now");
    expect(formatThreadUpdatedAge("2026-05-23T10:00:00.000Z", "en-US", now)).toBe("2h");
    expect(formatThreadUpdatedAge("2026-05-19T12:00:00.000Z", "en-US", now)).toBe("4d");
    expect(formatThreadUpdatedAge("2026-05-09T12:00:00.000Z", "en-US", now)).toBe("2w");
  });

  it("keeps the activity formatter alias stable", () => {
    const now = Date.parse("2026-05-23T12:00:00.000Z");

    expect(formatThreadActivityAge("2026-05-23T11:00:00.000Z", "en-US", now)).toBe("1h");
  });
});
