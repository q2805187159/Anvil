const childProcess = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const frontendRoot = path.resolve(__dirname, "..");

function fail(check, error) {
  const message = error && error.stack ? error.stack : String(error);
  console.error(`[failed] ${check}`);
  console.error(message);
  console.error(
    "Frontend release gates require the package-script Vitest entrypoint, native config loading, " +
      "a sandbox-safe Next release build directory, worker-thread static generation, and resolvable dependencies."
  );
  process.exit(1);
}

function pass(check) {
  console.log(`[passed] ${check}`);
}

function warn(check, message) {
  console.warn(`[warning] ${check}: ${message}`);
}

function readJson(filePath, check) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    fail(check, error);
  }
}

function resolvePackage(specifier, check) {
  try {
    require.resolve(specifier, { paths: [frontendRoot] });
    pass(check);
  } catch (error) {
    fail(check, error);
  }
}

function requireConfigText(snippet, check, detail) {
  if (!nextConfigText.includes(snippet)) {
    fail(check, detail || `next.config.mjs must contain: ${snippet}`);
  }
}

const packageJson = readJson(path.join(frontendRoot, "package.json"), "package-json");
const testScript = packageJson.scripts && packageJson.scripts.test;
if (typeof testScript !== "string") {
  fail("frontend-test-script", "package.json has no scripts.test entry");
}
if (!testScript.includes("./scripts/vitest-node-pipe-shim.cjs")) {
  fail("frontend-test-script", "scripts.test must load vitest-node-pipe-shim.cjs");
}
if (!testScript.includes("--configLoader native")) {
  fail("frontend-test-script", "scripts.test must use Vitest native config loading");
}
pass("frontend-test-script");

const buildScript = packageJson.scripts && packageJson.scripts.build;
if (typeof buildScript !== "string") {
  fail("frontend-build-script", "package.json has no scripts.build entry");
}
if (buildScript !== "node scripts/release-next-build.cjs") {
  fail(
    "frontend-build-script",
    "scripts.build must run the release-next-build wrapper; npm test and npm run typecheck are separate release gates"
  );
}
pass("frontend-build-script");

const startScript = packageJson.scripts && packageJson.scripts.start;
if (startScript !== "node scripts/release-next-start.cjs") {
  fail("frontend-start-script", "scripts.start must run the release-next-start wrapper so it uses the current release artifact manifest");
}
pass("frontend-start-script");

const nextConfigText = fs.readFileSync(path.join(frontendRoot, "next.config.mjs"), "utf8");
const appLayoutText = fs.readFileSync(path.join(frontendRoot, "app", "layout.tsx"), "utf8");
requireConfigText('distDir: process.env.ANVIL_NEXT_DIST_DIR || ".next-release"', "next-release-dist-dir");
requireConfigText("ignoreDuringBuilds: true", "next-eslint-build-check");
requireConfigText("ignoreBuildErrors: true", "next-typescript-build-check");
requireConfigText("cleanDistDir: false", "next-clean-dist-dir");
requireConfigText("cpus: 1", "next-worker-count");
requireConfigText("workerThreads: true", "next-static-worker-threads");
requireConfigText("webpackBuildWorker: false", "next-webpack-build-worker");
try {
  require(path.join(frontendRoot, "scripts", "release-build-contract.cjs")).assertReleaseBuildContract();
  pass("release-build-contract");
} catch (error) {
  fail("release-build-contract", error);
}
if (nextConfigText.includes("webpack:")) {
  fail(
    "next-webpack-config",
    "next.config.mjs must not define a custom webpack function; worker_threads static generation must receive a structured-cloneable config"
  );
}
pass("next-release-build-config");
if (!appLayoutText.includes('export const dynamic = "force-dynamic";')) {
  fail(
    "next-dynamic-app-shell",
    "app/layout.tsx must force dynamic rendering so worker_threads static generation does not clone runtime-only Next config functions"
  );
}
pass("next-dynamic-app-shell");
const buildWrapperText = fs.readFileSync(path.join(frontendRoot, "scripts", "release-next-build.cjs"), "utf8");
if (!buildWrapperText.includes("installNextWorkerConfigSanitizer") || !buildWrapperText.includes("sanitizeWorkerNextConfig")) {
  fail(
    "next-static-worker-payload-sanitizer",
    "release-next-build.cjs must sanitize Next static export worker payloads before worker_threads cloning"
  );
}
if (!buildWrapperText.includes("installReleaseFilesystemGuard") || !buildWrapperText.includes("shouldFallbackReleaseRename")) {
  fail(
    "next-release-filesystem-guard",
    "release-next-build.cjs must guard release-dist rename/unlink/rm operations for restricted Windows hosts"
  );
}
if (!buildWrapperText.includes("restoreFileIfChanged(tsconfigPath, tsconfigBefore)")) {
  fail(
    "next-tsconfig-mutation-restore",
    "release-next-build.cjs must restore Next tsconfig include mutations after per-run distDir builds"
  );
}
pass("next-static-worker-payload-sanitizer");
pass("next-release-filesystem-guard");
pass("next-tsconfig-mutation-restore");

try {
  require(path.join(frontendRoot, "scripts", "vitest-node-pipe-shim.cjs"));
  pass("vitest-node-pipe-shim");
} catch (error) {
  fail("vitest-node-pipe-shim", error);
}

resolvePackage("vitest/vitest.mjs", "vitest-entrypoint");
resolvePackage("typescript", "typescript-package");
resolvePackage("next/package.json", "next-package");
resolvePackage("esbuild/package.json", "esbuild-package");

const spawnResult = childProcess.spawnSync(process.execPath, ["-e", "process.stdout.write('ok')"], {
  encoding: "utf8",
});
if (spawnResult.error) {
  if (spawnResult.error.code === "EPERM") {
    warn(
      "node-child-process-spawn",
      "nested Node child_process spawn is blocked by this host; npm test/typecheck/build remain the authoritative gates"
    );
  } else {
    fail("node-child-process-spawn", spawnResult.error);
  }
} else if (spawnResult.status !== 0 || spawnResult.stdout !== "ok") {
  fail("node-child-process-spawn", spawnResult.stderr || `unexpected status ${spawnResult.status}`);
} else {
  pass("node-child-process-spawn");
}

console.log("Frontend process preflight passed.");
