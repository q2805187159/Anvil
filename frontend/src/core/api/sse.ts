import type { RunStreamEvent } from "@/src/core/contracts";

import { ApiError, getGatewayBaseUrl, NetworkError } from "./client";

function parseEventChunk(chunk: string): RunStreamEvent | null {
  const lines = chunk.split("\n");
  let event = "message";
  let sseEventId: string | undefined;
  const data: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("id:")) {
      sseEventId = line.slice(3).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).trim());
    }
  }

  if (!data.length) {
    return null;
  }

  const payload = JSON.parse(data.join("\n")) as Record<string, unknown>;
  const payloadEventId = typeof payload.event_id === "string" ? payload.event_id : undefined;
  return {
    event,
    data: payload,
    event_id: sseEventId || payloadEventId,
    sequence: typeof payload.sequence === "number" ? payload.sequence : undefined,
    message_id: typeof payload.message_id === "string" ? payload.message_id : undefined,
    block_id: typeof payload.block_id === "string" ? payload.block_id : undefined,
    visibility: typeof payload.visibility === "string" ? payload.visibility : undefined,
    source: typeof payload.source === "string" ? payload.source : undefined,
  };
}

export async function* postEventStream(
  path: string,
  body: Record<string, unknown>,
  options?: {
    signal?: AbortSignal;
    lastEventId?: string | null;
  },
): AsyncGenerator<RunStreamEvent> {
  let response: Response;
  try {
    response = await fetch(`${getGatewayBaseUrl()}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(options?.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}),
      },
      body: JSON.stringify(body),
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new NetworkError("Gateway stream is unavailable.");
  }

  if (!response.ok) {
    const payload = (await response.json()) as { error: string; detail?: string | null; kind?: string | null };
    throw new ApiError(response.status, payload);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const parsed = parseEventChunk(chunk.trim());
      if (parsed) {
        yield parsed;
      }
    }
  }

  if (buffer.trim()) {
    const parsed = parseEventChunk(buffer.trim());
    if (parsed) {
      yield parsed;
    }
  }
}

export async function* getEventStream(
  path: string,
  options?: {
    signal?: AbortSignal;
  },
): AsyncGenerator<RunStreamEvent> {
  let response: Response;
  try {
    response = await fetch(`${getGatewayBaseUrl()}${path}`, {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
      },
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new NetworkError("Gateway event stream is unavailable.");
  }

  if (!response.ok) {
    const payload = (await response.json()) as { error: string; detail?: string | null; kind?: string | null };
    throw new ApiError(response.status, payload);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const parsed = parseEventChunk(chunk.trim());
      if (parsed) {
        yield parsed;
      }
    }
  }

  if (buffer.trim()) {
    const parsed = parseEventChunk(buffer.trim());
    if (parsed) {
      yield parsed;
    }
  }
}
