const fs = require("node:fs");
const path = require("node:path");

const frontendRoot = path.resolve(__dirname, "..");

function readReleaseBuildContract(projectRoot = frontendRoot) {
  const root = path.resolve(projectRoot);
  const packageJson = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  const nextConfigText = fs.readFileSync(path.join(root, "next.config.mjs"), "utf8");
  const appLayoutText = fs.readFileSync(path.join(root, "app", "layout.tsx"), "utf8");
  const nextEnvText = fs.readFileSync(path.join(root, "next-env.d.ts"), "utf8");
  const buildWrapperText = fs.readFileSync(path.join(root, "scripts", "release-next-build.cjs"), "utf8");
  const startWrapperText = fs.readFileSync(path.join(root, "scripts", "release-next-start.cjs"), "utf8");
  return {
    buildScript: packageJson.scripts && packageJson.scripts.build,
    startScript: packageJson.scripts && packageJson.scripts.start,
    typecheckScript: packageJson.scripts && packageJson.scripts.typecheck,
    skipsNextLint: nextConfigText.includes("ignoreDuringBuilds: true"),
    skipsNextTypecheck: nextConfigText.includes("ignoreBuildErrors: true"),
    usesReleaseDistDir: nextConfigText.includes('distDir: process.env.ANVIL_NEXT_DIST_DIR || ".next-release"'),
    disablesNextClean: nextConfigText.includes("cleanDistDir: false"),
    pinsStaticWorkersToOne: nextConfigText.includes("cpus: 1"),
    usesStaticWorkerThreads: nextConfigText.includes("workerThreads: true"),
    disablesWebpackBuildWorker: nextConfigText.includes("webpackBuildWorker: false"),
    avoidsCustomWebpackConfig: !nextConfigText.includes("webpack:"),
    appShellRoutesAreDynamic: appLayoutText.includes('export const dynamic = "force-dynamic";'),
    nextEnvUsesStableReleaseTypes:
      nextEnvText.includes('./.next-release/types/routes.d.ts') && !nextEnvText.includes('.next-release-builds/release-'),
    buildUsesPerRunDistDir: buildWrapperText.includes('const releaseBuildRoot = ".next-release-builds"'),
    buildRunsNextInProcess:
      buildWrapperText.includes('require("next/dist/cli/next-build.js")') && !buildWrapperText.includes("child_process"),
    buildWritesCurrentManifest: buildWrapperText.includes('".next-release-current.json"'),
    buildSanitizesStaticExportWorkerPayload:
      buildWrapperText.includes("installNextWorkerConfigSanitizer") &&
      buildWrapperText.includes("sanitizeWorkerNextConfig") &&
      buildWrapperText.includes('methods.includes("exportPages")'),
    buildUsesReleaseFilesystemGuard:
      buildWrapperText.includes("installReleaseFilesystemGuard") &&
      buildWrapperText.includes("patchFsPromisesForReleaseBuild") &&
      buildWrapperText.includes("shouldFallbackReleaseRename"),
    buildRestoresNextTsconfigMutation: buildWrapperText.includes("restoreFileIfChanged(tsconfigPath, tsconfigBefore)"),
    buildRestoresNextEnvMutation:
      buildWrapperText.includes("next-env.d.ts") &&
      buildWrapperText.includes("restoreFileIfChanged(nextEnvPath, nextEnvBefore)"),
    startReadsCurrentManifest: startWrapperText.includes('".next-release-current.json"'),
    startRunsNextInProcess:
      startWrapperText.includes('require("next/dist/cli/next-start.js")') && !startWrapperText.includes("child_process"),
  };
}

function assertReleaseBuildContract(projectRoot = frontendRoot) {
  const contract = readReleaseBuildContract(projectRoot);
  const failures = [];
  if (contract.buildScript !== "node scripts/release-next-build.cjs") {
    failures.push("package scripts.build must use scripts/release-next-build.cjs");
  }
  if (contract.startScript !== "node scripts/release-next-start.cjs") {
    failures.push("package scripts.start must use scripts/release-next-start.cjs");
  }
  if (contract.typecheckScript !== "next typegen && tsc --noEmit") {
    failures.push("package scripts.typecheck must run next typegen before tsc --noEmit");
  }
  if (!contract.skipsNextLint) {
    failures.push("next.config.mjs must skip Next internal lint because npm test is the authoritative gate");
  }
  if (!contract.skipsNextTypecheck) {
    failures.push("next.config.mjs must skip Next internal typecheck because npm run typecheck is the authoritative gate");
  }
  if (!contract.usesReleaseDistDir) failures.push("next.config.mjs must use the ANVIL_NEXT_DIST_DIR release distDir override");
  if (!contract.disablesNextClean) failures.push("next.config.mjs must disable cleanDistDir");
  if (!contract.pinsStaticWorkersToOne) failures.push("next.config.mjs must pin static workers to one worker");
  if (!contract.usesStaticWorkerThreads) failures.push("next.config.mjs must use worker_threads for static workers");
  if (!contract.disablesWebpackBuildWorker) failures.push("next.config.mjs must disable webpackBuildWorker");
  if (!contract.avoidsCustomWebpackConfig) failures.push("next.config.mjs must avoid custom webpack functions");
  if (!contract.appShellRoutesAreDynamic) {
    failures.push("app/layout.tsx must force dynamic rendering so Next static export workers do not clone runtime config functions");
  }
  if (!contract.nextEnvUsesStableReleaseTypes) {
    failures.push("next-env.d.ts must reference the stable .next-release route types, not a per-run release build directory");
  }
  if (!contract.buildUsesPerRunDistDir) failures.push("release build wrapper must use a per-run .next-release-builds distDir");
  if (!contract.buildRunsNextInProcess) failures.push("release build wrapper must call Next build in-process without child_process");
  if (!contract.buildWritesCurrentManifest) failures.push("release build wrapper must write .next-release-current.json");
  if (!contract.buildSanitizesStaticExportWorkerPayload) {
    failures.push("release build wrapper must sanitize Next static export worker payloads for worker_threads cloning");
  }
  if (!contract.buildUsesReleaseFilesystemGuard) {
    failures.push("release build wrapper must guard release-dist rename/unlink/rm operations for restricted Windows hosts");
  }
  if (!contract.buildRestoresNextTsconfigMutation) {
    failures.push("release build wrapper must restore Next tsconfig include mutations after per-run distDir builds");
  }
  if (!contract.buildRestoresNextEnvMutation) {
    failures.push("release build wrapper must restore Next next-env.d.ts route-reference mutations after per-run distDir builds");
  }
  if (!contract.startReadsCurrentManifest) failures.push("release start wrapper must read .next-release-current.json");
  if (!contract.startRunsNextInProcess) failures.push("release start wrapper must call Next start in-process without child_process");
  if (failures.length > 0) {
    const error = new Error(`Frontend release build contract failed: ${failures.join("; ")}`);
    error.failures = failures;
    throw error;
  }
  return contract;
}

if (require.main === module) {
  try {
    assertReleaseBuildContract();
    console.log("[release-build-contract] passed");
  } catch (error) {
    console.error(error && error.stack ? error.stack : String(error));
    process.exit(1);
  }
}

module.exports = {
  assertReleaseBuildContract,
  readReleaseBuildContract,
};