const fs = require("node:fs");
const path = require("node:path");

const frontendRoot = path.resolve(__dirname, "..");
const currentManifestPath = path.join(frontendRoot, ".next-release-current.json");

function readCurrentManifest() {
  if (!fs.existsSync(currentManifestPath)) {
    throw new Error("No frontend release build manifest exists. Run npm run build before npm start.");
  }
  const manifest = JSON.parse(fs.readFileSync(currentManifestPath, "utf8"));
  if (!manifest || manifest.version !== 1 || typeof manifest.distDir !== "string" || !manifest.distDir) {
    throw new Error(`${currentManifestPath} is not a valid frontend release build manifest`);
  }
  const buildIdPath = path.join(frontendRoot, manifest.distDir, "BUILD_ID");
  if (!fs.existsSync(buildIdPath)) {
    throw new Error(`The recorded frontend release artifact is missing BUILD_ID: ${buildIdPath}`);
  }
  return manifest;
}

function parseArgs(argv) {
  const options = {
    hostname: undefined,
    port: Number.parseInt(process.env.PORT || "3000", 10),
    keepAliveTimeout: undefined,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if ((arg === "-p" || arg === "--port") && argv[index + 1]) {
      options.port = Number.parseInt(argv[index + 1], 10);
      index += 1;
    } else if ((arg === "-H" || arg === "--hostname") && argv[index + 1]) {
      options.hostname = argv[index + 1];
      index += 1;
    } else if (arg === "--keepAliveTimeout" && argv[index + 1]) {
      options.keepAliveTimeout = Number.parseInt(argv[index + 1], 10);
      index += 1;
    } else {
      throw new Error(`Unsupported next start argument for release wrapper: ${arg}`);
    }
  }
  if (!Number.isFinite(options.port) || options.port <= 0) {
    throw new Error(`Invalid port for release frontend start: ${options.port}`);
  }
  return options;
}

async function main() {
  const manifest = readCurrentManifest();
  process.env.ANVIL_NEXT_DIST_DIR = manifest.distDir;
  process.env.NODE_ENV = process.env.NODE_ENV || "production";
  process.env.NEXT_RUNTIME = "nodejs";
  const { nextStart } = require("next/dist/cli/next-start.js");
  await nextStart(parseArgs(process.argv.slice(2)), frontendRoot);
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
