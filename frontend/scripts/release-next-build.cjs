const fs = require("node:fs");
const fsPromises = require("node:fs/promises");
const Module = require("node:module");
const path = require("node:path");

const frontendRoot = path.resolve(__dirname, "..");
const releaseBuildRoot = ".next-release-builds";
const currentManifestPath = path.join(frontendRoot, ".next-release-current.json");
const skipValue = Symbol("anvil.skip-worker-value");

function releaseTimestamp() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function chooseDistDir() {
  const explicit = process.env.ANVIL_NEXT_DIST_DIR && process.env.ANVIL_NEXT_DIST_DIR.trim();
  if (explicit) {
    return explicit.replace(/\\/g, "/");
  }
  return `${releaseBuildRoot}/release-${releaseTimestamp()}-${process.pid}`;
}

function assertRelativeDistDir(distDir) {
  if (path.isAbsolute(distDir)) {
    throw new Error(`ANVIL_NEXT_DIST_DIR must be relative to the frontend root, got ${distDir}`);
  }
  const parts = distDir.split(/[\\/]+/).filter(Boolean);
  if (parts.includes("..")) {
    throw new Error(`ANVIL_NEXT_DIST_DIR must stay inside the frontend root, got ${distDir}`);
  }
}

function normalizedPath(filePath) {
  return path.resolve(String(filePath)).replace(/\\/g, "/").toLowerCase();
}

function isPathInside(filePath, directoryPath) {
  const file = path.resolve(String(filePath));
  const directory = path.resolve(String(directoryPath));
  const relative = path.relative(directory, file);
  return relative === "" || (!!relative && !relative.startsWith("..") && !path.isAbsolute(relative));
}

function isRetryableFsSandboxError(error) {
  return !!error && ["EPERM", "EACCES", "EXDEV"].includes(error.code);
}

function makeReleaseFilesystemGuardPaths(distDir) {
  const absoluteDistDir = path.join(frontendRoot, distDir);
  return {
    distDir: absoluteDistDir,
    exportDir: path.join(absoluteDistDir, "export"),
    serverDir: path.join(absoluteDistDir, "server"),
    exportDetailPath: path.join(absoluteDistDir, "export-detail.json"),
  };
}

function shouldFallbackReleaseRename(paths, source, destination) {
  return isPathInside(source, paths.distDir) && isPathInside(destination, paths.distDir);
}

function shouldIgnoreReleaseCleanupError(paths, target) {
  return isPathInside(target, paths.distDir) || normalizedPath(target) === normalizedPath(paths.exportDetailPath);
}

function installReleaseFilesystemGuard(distDir) {
  const paths = makeReleaseFilesystemGuardPaths(distDir);
  patchFsPromisesForReleaseBuild(fs.promises, paths);
  patchFsPromisesForReleaseBuild(fsPromises, paths);
}

function patchFsPromisesForReleaseBuild(api, paths) {
  if (!api || api.__anvilReleaseFilesystemGuard) return;
  const originalRename = api.rename.bind(api);
  const originalCopyFile = api.copyFile.bind(api);
  const originalUnlink = api.unlink.bind(api);
  const originalRm = api.rm ? api.rm.bind(api) : null;

  api.rename = async (source, destination) => {
    try {
      return await originalRename(source, destination);
    } catch (error) {
      if (!isRetryableFsSandboxError(error) || !shouldFallbackReleaseRename(paths, source, destination)) {
        throw error;
      }
      const stat = await api.stat(source).catch(() => null);
      if (!stat || !stat.isFile()) throw error;
      await api.mkdir(path.dirname(destination), { recursive: true });
      await originalCopyFile(source, destination);
      try {
        await originalUnlink(source);
      } catch (unlinkError) {
        if (!isRetryableFsSandboxError(unlinkError)) throw unlinkError;
      }
      return undefined;
    }
  };

  api.unlink = async (target) => {
    try {
      return await originalUnlink(target);
    } catch (error) {
      if (isRetryableFsSandboxError(error) && shouldIgnoreReleaseCleanupError(paths, target)) return undefined;
      throw error;
    }
  };

  if (originalRm) {
    api.rm = async (target, options) => {
      try {
        return await originalRm(target, options);
      } catch (error) {
        if (isRetryableFsSandboxError(error) && shouldIgnoreReleaseCleanupError(paths, target)) return undefined;
        throw error;
      }
    };
  }

  Object.defineProperty(api, "__anvilReleaseFilesystemGuard", { value: true });
}

function isPlainObject(value) {
  if (!value || typeof value !== "object") return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function sanitizeWorkerNextConfig(value, seen = new WeakMap()) {
  if (value === null || value === undefined) return value;
  const valueType = typeof value;
  if (valueType === "function" || valueType === "symbol") return skipValue;
  if (valueType !== "object") return value;
  if (seen.has(value)) return seen.get(value);

  if (value instanceof Date) return new Date(value.getTime());
  if (value instanceof RegExp) return new RegExp(value.source, value.flags);
  if (value instanceof URL) return new URL(value.toString());
  if (value instanceof URLSearchParams) return new URLSearchParams(value.toString());
  if (ArrayBuffer.isView(value)) return value.slice ? value.slice(0) : value;
  if (value instanceof ArrayBuffer) return value.slice(0);

  if (Array.isArray(value)) {
    const clone = [];
    seen.set(value, clone);
    for (const item of value) {
      const sanitized = sanitizeWorkerNextConfig(item, seen);
      clone.push(sanitized === skipValue ? undefined : sanitized);
    }
    return clone;
  }

  if (value instanceof Map) {
    const clone = new Map();
    seen.set(value, clone);
    for (const [key, item] of value.entries()) {
      const sanitizedKey = sanitizeWorkerNextConfig(key, seen);
      const sanitizedItem = sanitizeWorkerNextConfig(item, seen);
      if (sanitizedKey !== skipValue && sanitizedItem !== skipValue) clone.set(sanitizedKey, sanitizedItem);
    }
    return clone;
  }

  if (value instanceof Set) {
    const clone = new Set();
    seen.set(value, clone);
    for (const item of value.values()) {
      const sanitized = sanitizeWorkerNextConfig(item, seen);
      if (sanitized !== skipValue) clone.add(sanitized);
    }
    return clone;
  }

  if (!isPlainObject(value)) {
    return value;
  }

  const clone = {};
  seen.set(value, clone);
  for (const [key, item] of Object.entries(value)) {
    const sanitized = sanitizeWorkerNextConfig(item, seen);
    if (sanitized !== skipValue) clone[key] = sanitized;
  }
  return clone;
}

function sanitizeWorkerPayload(payload) {
  const sanitized = sanitizeWorkerNextConfig(payload);
  if (sanitized === skipValue) return undefined;
  return sanitized;
}

function wrapNextWorkerModule(loaded) {
  if (!loaded || typeof loaded.Worker !== "function") return loaded;
  const OriginalWorker = loaded.Worker;
  if (OriginalWorker.__anvilSanitizesStaticExportPayload) return loaded;

  function SanitizedNextWorker(workerPath, options) {
    const worker = new OriginalWorker(workerPath, options);
    const methods = Array.isArray(options && options.exposedMethods) ? options.exposedMethods : [];
    if (methods.includes("exportPages") && worker && typeof worker.exportPages === "function") {
      const exportPages = worker.exportPages.bind(worker);
      worker.exportPages = (payload, ...rest) => exportPages(sanitizeWorkerPayload(payload), ...rest);
    }
    return worker;
  }

  Object.setPrototypeOf(SanitizedNextWorker, OriginalWorker);
  SanitizedNextWorker.prototype = OriginalWorker.prototype;
  Object.defineProperty(SanitizedNextWorker, "__anvilSanitizesStaticExportPayload", { value: true });
  return {
    ...loaded,
    Worker: SanitizedNextWorker,
  };
}

function installNextWorkerConfigSanitizer() {
  if (Module._load.__anvilNextWorkerConfigSanitizer) return;
  const originalLoad = Module._load;
  function wrappedLoad(request, parent, isMain) {
    const loaded = originalLoad.apply(this, arguments);
    let resolved;
    try {
      resolved = Module._resolveFilename(request, parent, isMain);
    } catch (_error) {
      return loaded;
    }
    const normalized = String(resolved).replace(/\\/g, "/");
    if (normalized.endsWith("/next/dist/lib/worker.js")) {
      return wrapNextWorkerModule(loaded);
    }
    return loaded;
  }
  Object.defineProperty(wrappedLoad, "__anvilNextWorkerConfigSanitizer", { value: true });
  Object.defineProperty(wrappedLoad, "__anvilOriginalLoad", { value: originalLoad });
  Module._load = wrappedLoad;
}

function restoreFileIfChanged(filePath, originalContent) {
  if (originalContent === null) return;
  if (!fs.existsSync(filePath)) {
    fs.writeFileSync(filePath, originalContent, "utf8");
    return;
  }
  const currentContent = fs.readFileSync(filePath, "utf8");
  if (currentContent !== originalContent) {
    fs.writeFileSync(filePath, originalContent, "utf8");
  }
}

async function main() {
  const distDir = chooseDistDir();
  assertRelativeDistDir(distDir);

  process.env.ANVIL_NEXT_DIST_DIR = distDir;
  process.env.NODE_ENV = process.env.NODE_ENV || "production";
  process.env.NEXT_RUNTIME = "nodejs";
  process.env.NEXT_TELEMETRY_DISABLED = process.env.NEXT_TELEMETRY_DISABLED || "1";

  installReleaseFilesystemGuard(distDir);
  installNextWorkerConfigSanitizer();

  const tsconfigPath = path.join(frontendRoot, "tsconfig.json");
  const nextEnvPath = path.join(frontendRoot, "next-env.d.ts");
  const tsconfigBefore = fs.existsSync(tsconfigPath) ? fs.readFileSync(tsconfigPath, "utf8") : null;
  const nextEnvBefore = fs.existsSync(nextEnvPath) ? fs.readFileSync(nextEnvPath, "utf8") : null;
  const restoreMutableNextFiles = () => {
    restoreFileIfChanged(tsconfigPath, tsconfigBefore);
    restoreFileIfChanged(nextEnvPath, nextEnvBefore);
  };
  process.once("beforeExit", restoreMutableNextFiles);
  process.once("exit", restoreMutableNextFiles);

  const { nextBuild } = require("next/dist/cli/next-build.js");
  try {
    await nextBuild(
      {
        debug: false,
        debugPrerender: false,
        experimentalDebugMemoryUsage: false,
        lint: false,
        mangling: true,
        profile: false,
        experimentalAppOnly: false,
        experimentalBuildMode: "default",
      },
      frontendRoot
    );
  } finally {
    restoreMutableNextFiles();
  }

  const absoluteDistDir = path.join(frontendRoot, distDir);
  const buildIdPath = path.join(absoluteDistDir, "BUILD_ID");
  const buildId = fs.existsSync(buildIdPath) ? fs.readFileSync(buildIdPath, "utf8").trim() : "";
  if (!buildId) {
    throw new Error(`Next build completed without a BUILD_ID at ${buildIdPath}`);
  }

  const manifest = {
    version: 1,
    distDir,
    absoluteDistDir,
    buildId,
    generatedAt: new Date().toISOString(),
    nextVersion: require("next/package.json").version,
  };
  fs.writeFileSync(currentManifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  console.log(`[release-next-build] wrote ${path.basename(currentManifestPath)} for ${distDir}`);
  process.exit(0);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error && error.stack ? error.stack : String(error));
    process.exitCode = 1;
  });
}

module.exports = {
  assertRelativeDistDir,
  chooseDistDir,
  installNextWorkerConfigSanitizer,
  installReleaseFilesystemGuard,
  isPathInside,
  isRetryableFsSandboxError,
  makeReleaseFilesystemGuardPaths,
  restoreFileIfChanged,
  sanitizeWorkerNextConfig,
  sanitizeWorkerPayload,
  shouldFallbackReleaseRename,
  shouldIgnoreReleaseCleanupError,
  wrapNextWorkerModule,
};