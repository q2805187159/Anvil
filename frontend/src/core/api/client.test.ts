import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest, NetworkError } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiRequest", () => {
  it("wraps network fetch failures in a stable application error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(apiRequest("/health")).rejects.toBeInstanceOf(NetworkError);
    await expect(apiRequest("/health")).rejects.toThrow("Gateway is unavailable.");
  });
});
