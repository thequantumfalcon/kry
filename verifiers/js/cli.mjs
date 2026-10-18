// Node CLI + conformance-corpus runner for the KRY JS verifier (verify.mjs).
//
//   node cli.mjs <attestation.json>              # verify one; prints VALID/INVALID, exit 0/1
//   node cli.mjs --vectors <dir>                 # run the whole conformance corpus
//   node cli.mjs --batch <ndjson> [mult.json]    # one verdict per line (differential fuzz)
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { verdict, verdictWithAnchor, canon, canonF64, parse, Num, setMultipliers, SENT_SAVINGS } from "./verify.mjs";

function runVectors(dir) {
  const man = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));
  try {
    setMultipliers(JSON.parse(readFileSync(join(dir, "primitives", "legal_multipliers.json"), "utf8")).multipliers);
  } catch { /* fall back to the {1.0} default */ }
  let pass = 0;
  let fail = 0;
  const fails = [];
  const prim = JSON.parse(readFileSync(join(dir, "primitives", "canon_f64.json"), "utf8"));
  for (const c of prim.cases) {
    if (c.input_number === undefined) continue;
    const got = canonF64(new Num(String(c.input_number)), SENT_SAVINGS);
    if (got === c.expected_hex) pass++;
    else { fail++; fails.push(`canon_f64/${c.label}: ${got} != ${c.expected_hex}`); }
  }
  const cj = JSON.parse(readFileSync(join(dir, "primitives", "canonical_json.json"), "utf8"));
  for (const c of cj.cases) {
    const got = canon(parse(JSON.stringify(c.input_object)));
    if (got === c.expected_bytes) pass++;
    else { fail++; fails.push(`canonical_json/${c.label}: ${got} != ${c.expected_bytes}`); }
  }
  for (const v of man.vectors) {
    if (v.category.startsWith("primitives")) continue;
    const raw = readFileSync(join(dir, v.category, v.id + ".json"), "utf8");
    const spec = JSON.parse(raw);
    const exp = spec.expected.verdict;
    let inputText;
    if (spec.input_raw_text !== undefined) {
      inputText = spec.input_raw_text;
    } else {
      // re-extract the exact input JSON text so numbers are preserved verbatim
      const m = raw.match(/"input":\s*([\s\S]*?),\r?\n\s*"expected"/);   // \r?: a CRLF checkout must not fall back
      inputText = m ? m[1] : JSON.stringify(spec.input);
    }
    // SPEC 3.8 anchor-profile vectors: hand over the anchor's exact text too, so its literal types and
    // large integers survive (generate.py writes input_anchor immediately before input).
    const anchorText = raw.match(/"input_anchor":\s*([\s\S]*?),\r?\n\s*"input"/);
    const got = spec.input_anchor !== undefined
      ? verdictWithAnchor(inputText, anchorText ? anchorText[1] : spec.input_anchor)
      : verdict(inputText);
    if (got === exp) pass++;
    else { fail++; fails.push(`${v.category}/${v.id}: got ${got}, expected ${exp}`); }
  }
  console.log(`JS verifier vs corpus: ${pass} passed, ${fail} failed`);
  for (const f of fails) console.log("  FAIL " + f);
  return fail === 0;
}

const arg = process.argv[2];
if (arg === "--vectors") {
  process.exit(runVectors(process.argv[3]) ? 0 : 1);
} else if (arg === "--batch") {
  if (process.argv[4]) {
    try { setMultipliers(JSON.parse(readFileSync(process.argv[4], "utf8")).multipliers); } catch { /* default */ }
  }
  const lines = readFileSync(process.argv[3], "utf8").split("\n");
  const out = [];
  for (const line of lines) {
    if (line.length === 0) continue;
    let v;
    try { v = verdict(line); } catch { v = "CRASH"; }
    out.push(v);
  }
  process.stdout.write(out.join("\n") + "\n");
} else if (arg) {
  // SPEC 3.4.1: magnitude checks MUST use the published multiplier set. Load it from this checkout's
  // corpus, as --vectors does; without it verify.mjs falls back to {1.0} and rejects real attestations.
  const multPath = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "vectors", "primitives", "legal_multipliers.json");
  if (existsSync(multPath)) {
    setMultipliers(JSON.parse(readFileSync(multPath, "utf8")).multipliers);
  } else {
    console.error(`warning: ${multPath} not found; magnitude checks use the default multiplier set {1.0}`);
  }
  // A verdict is always printed: an unreadable attestation is INVALID (as in the Python CLI), and an
  // unreadable or malformed anchor is a malformed anchor (SPEC 3.8), not an uncaught exception.
  let bytes;
  try { bytes = readFileSync(arg); } catch (e) {
    console.log("VERDICT: INVALID");
    console.error(`attestation unreadable: ${e.message}`);
    process.exit(1);
  }
  // JSON text is UTF-8. A lenient decode turns invalid bytes into U+FFFD and verifies a document
  // that is not the one on disk; ignoreBOM keeps a BOM in the text, where it fails to parse.
  const utf8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  let text;
  try { text = utf8.decode(bytes); } catch {
    console.log("VERDICT: PARSE_ERROR");
    console.error("attestation is not valid UTF-8");
    process.exit(1);
  }
  const anchorPath = process.argv[3];             // SPEC 3.8: optional published-anchor JSON
  let anchor = null;                              // passed as TEXT: verify.mjs parses it exactly
  if (anchorPath) {
    try { anchor = utf8.decode(readFileSync(anchorPath)); } catch (e) {
      console.error(`anchor unreadable: ${e.message}`);
    }
  }
  const v = anchorPath ? verdictWithAnchor(text, anchor) : verdict(text);
  console.log("VERDICT: " + v);
  process.exit(v === "VALID" ? 0 : 1);
} else {
  console.log("usage: node cli.mjs <attestation.json> [anchor.json] | --vectors <dir> | --batch <ndjson> [mult.json]");
}
