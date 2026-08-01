// Smoke-check the browser verify page's wiring to the module and the corpus.
//
// verifiers/web/index.html is the stranger-facing demo the README points at, but it is a static
// page: nothing executed it, so a renamed export or a moved vector broke it silently and only a
// human opening the page would notice.
//
// SCOPE, stated plainly: this exercises the page's WIRING, not its DOM. It reads index.html,
// extracts the names it imports from ../js/verify.mjs and the vector paths it fetches, and checks
// that each name is really exported and each vector still verifies to the verdict the page's story
// text depends on. A DOM harness would need a browser dependency, which this repo does not carry —
// so button handlers and rendering are still unexercised here.
//
// Run: node verifiers/web/check_demos.mjs     (exit 0 = wired, 1 = broken)

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = resolve(HERE, "index.html");
const html = readFileSync(PAGE, "utf8");
const fail = [];

// 1. Every name the page imports from verify.mjs must actually be exported by it.
const importMatch = html.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/js\/verify\.mjs["']/);
if (!importMatch) {
  fail.push("index.html no longer imports from ../js/verify.mjs — the page and the module are unwired");
} else {
  const names = importMatch[1].split(",").map((s) => s.trim()).filter(Boolean);
  const mod = await import("../js/verify.mjs");
  for (const n of names) {
    if (typeof mod[n] === "undefined") fail.push(`index.html imports { ${n} } which verify.mjs does not export`);
  }
  console.log(`  page imports: ${names.join(", ")} — all exported`);
}

// 2. Every corpus vector the page fetches must exist and still carry the verdict the demo narrates.
const paths = [...new Set([...html.matchAll(/["'](\.\.\/\.\.\/vectors\/[^"']+)["']/g)].map((m) => m[1]))];
if (paths.length === 0) fail.push("index.html fetches no corpus vectors — the anchor demos are gone");
const { explain } = await import("../js/verify.mjs");
for (const rel of paths) {
  const abs = resolve(HERE, rel);
  let vec;
  try {
    vec = JSON.parse(readFileSync(abs, "utf8"));
  } catch (e) {
    fail.push(`index.html fetches ${rel}, which is missing or unreadable (${e.message})`);
    continue;
  }
  // The page calls explain(text) after setAnchor(vector.input_anchor), i.e. the anchor profile.
  const got = explain(JSON.stringify(vec.input), vec.input_anchor).verdict;
  const want = vec.expected?.verdict;
  if (got !== want) fail.push(`${rel}: page path yields ${got}, vector expects ${want}`);
  else console.log(`  demo vector ${rel.replace("../../", "")} -> ${got} (as narrated)`);
}

if (fail.length) {
  console.error("\nbrowser demo wiring BROKEN:");
  for (const f of fail) console.error(`  - ${f}`);
  process.exit(1);
}
console.log("browser demo wiring: OK");
