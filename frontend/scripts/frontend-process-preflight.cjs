const childProcess = require("node:child_process");

function fail(check, error) {
  const message = error && error.stack ? error.stack : String(error);
  console.error(`[failed] ${check}`);
  console.error(message);
  console.error(
    "Frontend tests require Node child_process spawn for Vitest/Vite/esbuild. " +
      "Run this gate in an environment that allows Node to spawn child processes."
  );
  process.exit(1);
}

const spawnResult = childProcess.spawnSync(process.execPath, ["-e", "process.stdout.write('ok')"], {
  encoding: "utf8",
});
if (spawnResult.error) {
  fail("node-child-process-spawn", spawnResult.error);
}
if (spawnResult.status !== 0 || spawnResult.stdout !== "ok") {
  fail("node-child-process-spawn", spawnResult.stderr || `unexpected status ${spawnResult.status}`);
}

let esbuild;
try {
  esbuild = require("esbuild");
} catch (error) {
  fail("esbuild-require", error);
}

esbuild
  .transform("let x = 1", { loader: "js" })
  .then((result) => {
    if (!result.code.includes("let x = 1")) {
      fail("esbuild-transform", "unexpected transform output");
    }
    console.log("Frontend process preflight passed.");
  })
  .catch((error) => fail("esbuild-transform", error));
