// Node CLI + conformance-corpus runner for the KRY JS verifier (verify.mjs).
//
//   node cli.mjs <attestation.json>              # verify one; prints VALID/INVALID, exit 0/1
//   node cli.mjs --vectors <dir>                 # run the whole conformance corpus
//   node cli.mjs --batch <ndjson> [mult.json]    # one verdict per line (differential fuzz)
import { readFileSync } from "node:fs";
import { join } from "node:path";
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
      const m = raw.match(/"input":\s*([\s\S]*?),\n\s*"expected"/);
      inputText = m ? m[1] : JSON.stringify(spec.input);
    }
    const got = spec.input_anchor !== undefined
      ? verdictWithAnchor(inputText, spec.input_anchor)     // SPEC 3.8 anchor-profile vectors
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
  const text = readFileSync(arg, "utf8");
  const anchorPath = process.argv[3];             // SPEC 3.8: optional published-anchor JSON
  const v = anchorPath ? verdictWithAnchor(text, JSON.parse(readFileSync(anchorPath, "utf8"))) : verdict(text);
  console.log("VERDICT: " + v);
  process.exit(v === "VALID" ? 0 : 1);
} else {
  console.log("usage: node cli.mjs <attestation.json> [anchor.json] | --vectors <dir> | --batch <ndjson> [mult.json]");
}
