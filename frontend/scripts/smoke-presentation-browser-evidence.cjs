const fs = require("node:fs");
const path = require("node:path");

const FRONTEND_URL = (process.env.ANVIL_FRONTEND_URL || "http://127.0.0.1:13200").replace(/\/$/, "");
const CDP_URL = (process.env.BROWSER_CDP_URL || "").replace(/\/$/, "");
const OUTPUT_DIR = path.resolve(process.env.ANVIL_SMOKE_OUTPUT_DIR || path.join("..", "_tmp_debug", "frontend-smoke"));

function skip(message) {
  console.log(`[skipped] presentation-browser-evidence-smoke: ${message}`);
  process.exit(0);
}

function fail(message) {
  console.error(`[failed] presentation-browser-evidence-smoke: ${message}`);
  process.exit(1);
}

if (!CDP_URL) {
  skip("BROWSER_CDP_URL is not set; start Chrome/Edge with remote debugging to run this smoke.");
}

main().catch((error) => fail(error && error.stack ? error.stack : String(error)));

async function main() {
  const targetUrl = `${FRONTEND_URL}/smoke/presentation-browser-evidence`;
  await assertReachable(targetUrl);
  const tab = await createTab(targetUrl);
  try {
    await waitForPageLoad(tab.webSocketDebuggerUrl);
    const evidence = await evaluate(tab.webSocketDebuggerUrl, smokeExpression());
    if (!evidence || evidence.ok !== true) {
      throw new Error(`smoke page did not render expected evidence: ${JSON.stringify(evidence)}`);
    }
    const screenshot = await captureScreenshot(tab.webSocketDebuggerUrl);
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    const screenshotPath = path.join(OUTPUT_DIR, "presentation-browser-evidence-smoke.png");
    fs.writeFileSync(screenshotPath, Buffer.from(screenshot, "base64"));
    console.log(
      JSON.stringify(
        {
          status: "passed",
          url: targetUrl,
          screenshot_path: screenshotPath,
          evidence,
        },
        null,
        2,
      ),
    );
  } finally {
    await closeTab(tab.id).catch(() => undefined);
  }
}

async function assertReachable(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`frontend smoke page returned HTTP ${response.status}; start the frontend and set ANVIL_FRONTEND_URL if needed`);
  }
}

async function createTab(url) {
  const encoded = encodeURIComponent(url);
  for (const method of ["PUT", "GET"]) {
    const response = await fetch(`${CDP_URL}/json/new?${encoded}`, { method }).catch(() => null);
    if (response && response.ok) {
      return response.json();
    }
  }
  throw new Error("could not create CDP tab through /json/new");
}

async function closeTab(id) {
  if (!id) {
    return;
  }
  await fetch(`${CDP_URL}/json/close/${encodeURIComponent(id)}`);
}

async function waitForPageLoad(webSocketDebuggerUrl) {
  const started = Date.now();
  while (Date.now() - started < 10000) {
    const ready = await evaluate(webSocketDebuggerUrl, "() => document.readyState");
    if (ready === "complete") {
      break;
    }
    await sleep(120);
  }
  const startedRender = Date.now();
  while (Date.now() - startedRender < 10000) {
    const evidence = await evaluate(webSocketDebuggerUrl, smokeExpression()).catch(() => null);
    if (evidence && evidence.ok === true) {
      return;
    }
    await sleep(160);
  }
  throw new Error("presentation browser evidence UI did not render within timeout");
}

function smokeExpression() {
  return `() => {
    const text = document.body ? document.body.innerText : "";
    const images = [...document.images].map((image) => ({
      alt: image.alt || "",
      complete: image.complete,
      width: image.naturalWidth || image.width || 0,
      height: image.naturalHeight || image.height || 0,
    }));
    const required = [
      "Presentation browser evidence",
      "Browser diff",
      "Overlay diff",
      "Changed cells",
      "Cell 3, 2",
    ];
    const missingText = required.filter((item) => !text.includes(item));
    const overlay = images.find((image) => image.alt === "Overlay diff");
    return {
      ok: missingText.length === 0 && Boolean(overlay && overlay.complete && overlay.width > 0 && overlay.height > 0),
      missingText,
      imageCount: images.length,
      overlay,
      hasHorizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  }`;
}

async function evaluate(webSocketDebuggerUrl, expression) {
  const payload = await cdpCall(webSocketDebuggerUrl, "Runtime.evaluate", {
    expression: `(${expression})()`,
    returnByValue: true,
    awaitPromise: true,
  });
  const result = payload.result && payload.result.result;
  if (!result) {
    return undefined;
  }
  return Object.prototype.hasOwnProperty.call(result, "value") ? result.value : result.description;
}

async function captureScreenshot(webSocketDebuggerUrl) {
  const payload = await cdpCall(webSocketDebuggerUrl, "Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: true,
    fromSurface: true,
  });
  const data = payload.result && payload.result.data;
  if (!data) {
    throw new Error("CDP screenshot returned no data");
  }
  return data;
}

function cdpCall(webSocketDebuggerUrl, method, params) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(webSocketDebuggerUrl);
    const id = Math.floor(Math.random() * 1_000_000_000);
    const timer = setTimeout(() => {
      ws.close();
      reject(new Error(`CDP ${method} timed out`));
    }, 10000);
    ws.addEventListener("open", () => {
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.addEventListener("message", (event) => {
      const data = JSON.parse(event.data);
      if (data.id !== id) {
        return;
      }
      clearTimeout(timer);
      ws.close();
      if (data.error) {
        reject(new Error(`CDP ${method} failed: ${JSON.stringify(data.error)}`));
        return;
      }
      resolve(data);
    });
    ws.addEventListener("error", () => {
      clearTimeout(timer);
      reject(new Error(`CDP websocket error for ${method}`));
    });
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
