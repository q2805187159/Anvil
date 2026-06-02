import { beforeEach, describe, expect, it, vi } from "vitest";

import { getToolCatalogEntry, listToolCatalog } from "./api";

describe("catalog api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("lists tool catalog through /tools/catalog by default", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await listToolCatalog({ query: "file", sourceKind: "builtin", capabilityGroup: "filesystem" });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18000/tools/catalog?query=file&source_kind=builtin&capability_group=filesystem",
      expect.any(Object),
    );
  });

  it("supports the catalog mirror path for the same catalog truth", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ capability_id: "cap.write_file" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    await listToolCatalog({ path: "catalog" });
    await getToolCatalogEntry("cap.write_file", "catalog");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:18000/catalog/tools", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:18000/catalog/tools/cap.write_file", expect.any(Object));
  });
});
