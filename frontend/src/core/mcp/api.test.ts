import { beforeEach, describe, expect, it, vi } from "vitest";

import { listMcpResources, listMcpServers, renderMcpPrompt } from "./api";

describe("mcp api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("lists MCP servers and filtered resources through gateway wrappers", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    await listMcpServers();
    await listMcpResources("github");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:18000/mcp/servers", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:18000/mcp/resources?server_id=github", expect.any(Object));
  });

  it("posts prompt render arguments with the expected request body", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          server_id: "github",
          prompt_id: "repo-summary",
          title: "Repo summary",
          description: "Render repo summary prompt",
          arguments: ["repo"],
          metadata: {},
          rendered: "Summary for repo",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await renderMcpPrompt("github", "repo-summary", { arguments: { repo: "forge" } });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/mcp/servers/github/prompts/repo-summary",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ arguments: { repo: "forge" } }),
      }),
    );
  });
});
