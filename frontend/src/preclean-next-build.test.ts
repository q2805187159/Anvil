import { createRequire } from "node:module";

import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { assertReleaseBuildContract } = require("../scripts/release-build-contract.cjs") as {
  assertReleaseBuildContract: () => Record<string, boolean | string>;
};

describe("legacy frontend release build contract alias", () => {
  it("points at the release build contract", () => {
    const contract = assertReleaseBuildContract();

    expect(contract.buildScript).toBe("node scripts/release-next-build.cjs");
    expect(contract.startScript).toBe("node scripts/release-next-start.cjs");
    expect(contract.buildUsesPerRunDistDir).toBe(true);
    expect(contract.buildRunsNextInProcess).toBe(true);
  });
});
