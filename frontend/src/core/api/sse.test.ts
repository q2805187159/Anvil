import { beforeEach, describe, expect, it, vi } from "vitest";

import { postEventStream } from "./sse";

describe("postEventStream", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses the standard SSE id line as the event cursor when payload omits event_id", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: message_delta\nid: run-1:000002\ndata: {"thread_id":"thread-a","run_id":"run-1","sequence":2}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const events = [];
    for await (const event of postEventStream("/threads/thread-a/runs/stream", { message: "resume" })) {
      events.push(event);
    }

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event: "message_delta",
      event_id: "run-1:000002",
      sequence: 2,
    });
    expect(events[0]?.data).toEqual({ thread_id: "thread-a", run_id: "run-1", sequence: 2 });
  });
});
