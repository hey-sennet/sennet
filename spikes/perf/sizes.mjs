// Raw, gzip and brotli sizes of each file in a built client directory.
// Usage: node sizes.mjs <web dir>
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { gzipSync, brotliCompressSync, constants } from "node:zlib";

const root = process.argv[2];
const walk = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

const kb = (n) => (n / 1024).toFixed(1).padStart(7);
const rows = walk(root).map((path) => {
  const data = readFileSync(path);
  return {
    file: relative(root, path),
    raw: data.length,
    gzip: gzipSync(data, { level: 9 }).length,
    br: brotliCompressSync(data, { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } }).length,
  };
}).sort((a, b) => b.raw - a.raw);

console.log("     raw KB  gzip KB    br KB  file");
for (const r of rows) console.log(`${kb(r.raw)}  ${kb(r.gzip)}  ${kb(r.br)}  ${r.file}`);
const sum = (pick, test) => rows.filter((r) => test.test(r.file)).reduce((n, r) => n + r[pick], 0);
for (const [label, test] of [["js+css+html", /\.(js|css|html)$/], ["fonts", /\.woff2$/], ["all", /./]]) {
  console.log(`${kb(sum("raw", test))}  ${kb(sum("gzip", test))}  ${kb(sum("br", test))}  TOTAL ${label}`);
}
