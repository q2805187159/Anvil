import { createRequire } from "node:module";

import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { skipMessage } = require("../scripts/smoke-presentation-browser-evidence.cjs") as {
  skipMessage: (message: string) => string;
};

describe("presentation browser evidence smoke script", () => {
  it("skips clearly when no browser CDP endpoint is configured", () => {
    const output = skipMessage("BROWSER_CDP_URL is not set; start Chrome/Edge with remote debugging to run this smoke.");

    expect(output).toContain("[skipped] presentation-browser-evidence-smoke");
    expect(output).toContain("BROWSER_CDP_URL is not set");
  });
});