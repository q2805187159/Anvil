import { execFileSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

describe("presentation browser evidence smoke script", () => {
  it("skips clearly when no browser CDP endpoint is configured", () => {
    const output = execFileSync("node", ["scripts/smoke-presentation-browser-evidence.cjs"], {
      cwd: path.resolve(__dirname, ".."),
      encoding: "utf-8",
      env: {
        ...process.env,
        BROWSER_CDP_URL: "",
      },
    });

    expect(output).toContain("[skipped] presentation-browser-evidence-smoke");
    expect(output).toContain("BROWSER_CDP_URL is not set");
  });
});
