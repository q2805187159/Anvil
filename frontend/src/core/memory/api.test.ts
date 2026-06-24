import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deletePublicHcmsMemory,
  getPublicHcmsMemory,
  getPublicHcmsMemoryRelations,
  getPublicHcmsMemoryVersions,
  listPublicHcmsMemories,
} from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockJsonResponse(payload: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("memory API public HCMS aliases", () => {
  it("targets the release-facing top-level HCMS memory routes", async () => {
    const fetchMock = vi.fn().mockImplementation(() => mockJsonResponse({ engine_notes: [], items: [], versions: [], relations: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await listPublicHcmsMemories({
      query: "Northstar",
      state: "active",
      category: "project_context",
      layerId: "workspace",
      limit: 7,
      offset: 2,
    });
    await getPublicHcmsMemory("mem/alpha");
    await getPublicHcmsMemoryVersions("mem/alpha");
    await getPublicHcmsMemoryRelations("mem/alpha");
    await deletePublicHcmsMemory("mem/alpha");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:18000/memory/list?query=Northstar&state=active&category=project_context&layer_id=workspace&limit=7&offset=2",
      expect.objectContaining({ headers: expect.objectContaining({ "Content-Type": "application/json" }) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:18000/memory/mem%2Falpha",
      expect.objectContaining({ headers: expect.objectContaining({ "Content-Type": "application/json" }) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:18000/memory/mem%2Falpha/versions",
      expect.objectContaining({ headers: expect.objectContaining({ "Content-Type": "application/json" }) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "http://127.0.0.1:18000/memory/mem%2Falpha/relations",
      expect.objectContaining({ headers: expect.objectContaining({ "Content-Type": "application/json" }) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      "http://127.0.0.1:18000/memory/mem%2Falpha",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
  });
});
