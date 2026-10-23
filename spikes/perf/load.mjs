// Cold load and reload of the client in Chromium on a phone profile, throttled to
// DevTools' Slow 4G: requests, bytes on the wire, and time to the load event.
// Usage: node load.mjs <base url> [token file]
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { chromium, devices } = require("@playwright/test");

const [base, tokenFile] = process.argv.slice(2);
// Chrome DevTools' "Slow 4G" preset.
const SLOW_4G = { offline: false, latency: 562.5, downloadThroughput: (1.6e6 / 8) * 0.9, uploadThroughput: (750e3 / 8) * 0.9 };

const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["Pixel 7"] });
if (tokenFile) {
  const { hostname, port } = new URL(base);
  await context.addCookies([{ name: "sennet_device", value: readFileSync(tokenFile, "utf8").trim(),
                              domain: hostname, path: "/", httpOnly: true }]);
}
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send("Network.enable");
await cdp.send("Network.emulateNetworkConditions", SLOW_4G);

let requests = new Map();
cdp.on("Network.requestWillBeSent", (e) => requests.set(e.requestId, { url: e.request.url, bytes: 0 }));
cdp.on("Network.responseReceived", (e) => {
  const r = requests.get(e.requestId);
  if (r) Object.assign(r, { status: e.response.status, cached: e.response.fromDiskCache || e.response.fromMemoryCache,
                            encoding: e.response.headers["content-encoding"] ?? "" });
});
cdp.on("Network.loadingFinished", (e) => { const r = requests.get(e.requestId); if (r) r.bytes = e.encodedDataLength; });
cdp.on("Network.webSocketCreated", (e) => requests.set(e.requestId, { url: e.url, bytes: 0, status: "ws", encoding: "" }));

async function run(label, go) {
  requests = new Map();
  const started = Date.now();
  await go();
  const loaded = Date.now() - started;
  await page.waitForTimeout(3000); // let the API calls and sockets that follow load finish
  const rows = [...requests.values()];
  const total = rows.reduce((n, r) => n + r.bytes, 0);
  console.log(`\n${label}: load event ${(loaded / 1000).toFixed(1)} s, ${rows.length} requests, ${(total / 1024).toFixed(0)} KB`);
  for (const r of rows.sort((a, b) => b.bytes - a.bytes)) {
    const path = new URL(r.url).pathname;
    console.log(`  ${(r.bytes / 1024).toFixed(1).padStart(7)} KB  ${String(r.status).padEnd(3)} ${r.cached ? "cache" : "     "} ${(r.encoding ?? "").padEnd(4)} ${path}`);
  }
}

await run("cold load", () => page.goto(base, { waitUntil: "load" }));
await run("reload", () => page.reload({ waitUntil: "load" }));
await run("new visit (navigate again)", () => page.goto(base, { waitUntil: "load" }));
await browser.close();
