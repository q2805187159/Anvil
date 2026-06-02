import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { useUploadFiles } from "./hooks";

function wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("upload hooks", () => {
  it("supports explicit thread overrides when files are uploaded before the active thread hook catches up", async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const uploadSpy = vi.spyOn(api, "uploadFiles").mockResolvedValue({
      thread_id: "thread-new",
      files: [
        {
          filename: "note.txt",
          kind: "file",
          virtual_path: "/uploads/note.txt",
          artifact_url: "/artifacts/note.txt",
          companions: [],
          outline: [],
          outline_preview: [],
        },
      ],
    });

    const file = new File(["test"], "note.txt", { type: "text/plain" });
    const { result } = renderHook(() => useUploadFiles(null), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        files: [file],
        threadIdOverride: "thread-new",
      });
    });

    expect(uploadSpy).toHaveBeenCalledWith("thread-new", [file]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["uploads", "thread-new"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread-state", "thread-new"] });
  });
});
