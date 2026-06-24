import { createRequire } from "node:module";

import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { assertReleaseBuildContract, readReleaseBuildContract } = require("../scripts/release-build-contract.cjs") as {
  assertReleaseBuildContract: () => Record<string, boolean | string>;
  readReleaseBuildContract: () => Record<string, boolean | string>;
};
const {
  makeReleaseFilesystemGuardPaths,
  sanitizeWorkerNextConfig,
  shouldFallbackReleaseRename,
  shouldIgnoreReleaseCleanupError,
} = require("../scripts/release-next-build.cjs") as {
  makeReleaseFilesystemGuardPaths: (distDir: string) => { distDir: string; exportDir: string; serverDir: string };
  sanitizeWorkerNextConfig: <T>(value: T) => T;
  shouldFallbackReleaseRename: (paths: { distDir: string }, source: string, destination: string) => boolean;
  shouldIgnoreReleaseCleanupError: (paths: { distDir: string }, target: string) => boolean;
};

describe("frontend release build contract", () => {
  it("uses package scripts that run Next release build and start in-process", () => {
    const contract = assertReleaseBuildContract();

    expect(contract.buildScript).toBe("node scripts/release-next-build.cjs");
    expect(contract.startScript).toBe("node scripts/release-next-start.cjs");
    expect(contract.typecheckScript).toBe("next typegen && tsc --noEmit");
    expect(contract.buildRunsNextInProcess).toBe(true);
    expect(contract.startRunsNextInProcess).toBe(true);
  });

  it("uses a sandbox-safe per-run Next production output path", () => {
    const contract = readReleaseBuildContract();

    expect(contract.skipsNextLint).toBe(true);
    expect(contract.skipsNextTypecheck).toBe(true);
    expect(contract.usesReleaseDistDir).toBe(true);
    expect(contract.disablesNextClean).toBe(true);
    expect(contract.buildUsesPerRunDistDir).toBe(true);
    expect(contract.buildWritesCurrentManifest).toBe(true);
    expect(contract.startReadsCurrentManifest).toBe(true);
  });

  it("keeps static generation on worker threads without custom webpack config", () => {
    const contract = readReleaseBuildContract();

    expect(contract.pinsStaticWorkersToOne).toBe(true);
    expect(contract.usesStaticWorkerThreads).toBe(true);
    expect(contract.disablesWebpackBuildWorker).toBe(true);
    expect(contract.avoidsCustomWebpackConfig).toBe(true);
    expect(contract.appShellRoutesAreDynamic).toBe(true);
    expect(contract.buildSanitizesStaticExportWorkerPayload).toBe(true);
    expect(contract.buildUsesReleaseFilesystemGuard).toBe(true);
    expect(contract.buildRestoresNextTsconfigMutation).toBe(true);
    expect(contract.buildRestoresNextEnvMutation).toBe(true);
  });

  it("scopes release filesystem fallback to the current production distDir", () => {
    const paths = makeReleaseFilesystemGuardPaths(".next-release-builds/release-test-123");

    expect(shouldFallbackReleaseRename(paths, `${paths.exportDir}/500.html`, `${paths.serverDir}/pages/500.html`)).toBe(
      true
    );
    expect(shouldIgnoreReleaseCleanupError(paths, `${paths.distDir}/export`)).toBe(true);
    expect(shouldFallbackReleaseRename(paths, `${paths.distDir}/../outside.html`, `${paths.serverDir}/500.html`)).toBe(
      false
    );
  });

  it("keeps the checked-in Next route reference stable", () => {
    const contract = readReleaseBuildContract();

    expect(contract.nextEnvUsesStableReleaseTypes).toBe(true);
  });

  it("removes function-valued Next config fields before worker-thread static export", () => {
    const input = {
      generateBuildId: () => "dev",
      experimental: {
        workerThreads: true,
        nested: [1, () => "drop"],
      },
      validPattern: /anvil/i,
      validDate: new Date("2026-06-24T00:00:00.000Z"),
      serializable: "kept",
    };

    const sanitized = sanitizeWorkerNextConfig(input) as typeof input;

    expect("generateBuildId" in sanitized).toBe(false);
    expect(sanitized.experimental.workerThreads).toBe(true);
    expect(sanitized.experimental.nested).toEqual([1, undefined]);
    expect(sanitized.validPattern).toBeInstanceOf(RegExp);
    expect(sanitized.validDate).toBeInstanceOf(Date);
    expect(sanitized.serializable).toBe("kept");
  });
});