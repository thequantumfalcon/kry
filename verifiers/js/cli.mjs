// Node CLI + conformance-corpus runner for the KRY JS verifier (verify.mjs).
//
//   node cli.mjs <attestation.json>              # verify one; prints VALID/INVALID, exit 0/1
//   node cli.mjs --vectors <dir>                 # run the whole conformance corpus
//   node cli.mjs --batch <ndjson> [mult.json]    # one verdict per line (differential fuzz)
import { closeSync, existsSync, openSync, readFileSync, readSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { verdict, verdictWithAnchor, extractVector, canon, canonF64, parse, Num, setMultipliers, SENT_SAVINGS, JSON_MAX_BYTES, JsonSizeError } from "./verify.mjs";

function readDocument(path) {
  const fd = path === "-" ? 0 : openSync(path, "r"), chunks = [];
  let size = 0;
  try {
    while (true) {
      const chunk = Buffer.alloc(Math.min(65536, JSON_MAX_BYTES + 1 - size));
      const count = readSync(fd, chunk);
      if (!count) return Buffer.concat(chunks, size);
      size += count;
      if (size > JSON_MAX_BYTES) throw new JsonSizeError("JSON document size limit exceeded (16 MiB)");
      chunks.push(chunk.subarray(0, count));
    }
  } finally { if (path !== "-") closeSync(fd); }
}

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
    const { spec, inputText, anchorText } = extractVector(raw);
    const exp = spec.expected.verdict;
    const got = spec.input_anchor !== undefined
      ? verdictWithAnchor(inputText, anchorText)
      : verdict(inputText);
    if (got === exp) pass++;
    else { fail++; fails.push(`${v.category}/${v.id}: got ${got}, expected ${exp}`); }
  }
  console.log(`JS verifier vs corpus: ${pass} passed, ${fail} failed`);
  for (const f of fails) console.log("  FAIL " + f);
  return fail === 0;
}

const usage = "usage: node cli.mjs <attestation.json> [anchor.json | --anchor <anchor.json>] | --vectors <dir> | --batch <ndjson> [mult.json]";
function argumentError(message) {
  console.log("VERDICT: INVALID");
  console.error(message + "\n" + usage);
  process.exit(1);
}
const args = process.argv.slice(2), arg = args[0];
if (args.includes("--help") || args.includes("-h")) {
  console.log(usage);
  process.exit(0);
}
if (arg === "--vectors") {
  if (args.length !== 2 || !args[1]) argumentError("--vectors requires a directory");
  process.exit(runVectors(args[1]) ? 0 : 1);
} else if (arg === "--batch") {
  if (args.length < 2 || args.length > 3 || !args[1]) argumentError("--batch requires an ndjson path and an optional multiplier path");
  if (args.length === 3) {
    try {
      const raw = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(readDocument(process.argv[4]));
      const values = JSON.parse(raw).multipliers;
      if (!Array.isArray(values) || values.length === 0 ||
          !values.every(v => typeof v === "number" && Number.isFinite(v) && v >= 0)) {
        throw new Error("expected a non-empty array of finite non-negative numbers");
      }
      setMultipliers(values);
    } catch (e) {
      console.error(`multiplier configuration invalid: ${e.message}`);
      process.exit(1);
    }
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
  if (arg !== "-" && arg.startsWith("-")) argumentError("unknown argument: " + arg);
  let anchorPath;
  if (args.length > 1) {
    if (args[1] === "--anchor") {
      if (args.length !== 3 || args[2].startsWith("--")) argumentError("--anchor requires a path");
      anchorPath = args[2];
    } else if (args[1].startsWith("--anchor=")) {
      if (args.length !== 2) argumentError("unexpected extra argument");
      anchorPath = args[1].slice("--anchor=".length);
    } else {
      if (args.length !== 2 || (args[1] !== "-" && args[1].startsWith("-"))) argumentError("unexpected argument");
      anchorPath = args[1];
    }
  }
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
  try { bytes = readDocument(arg); } catch (e) {
    console.log("VERDICT: " + (e instanceof JsonSizeError ? "PARSE_ERROR" : "INVALID"));
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
  let anchor = null;                              // passed as TEXT: verify.mjs parses it exactly
  if (anchorPath !== undefined) {
    try { anchor = utf8.decode(readDocument(anchorPath)); } catch (e) {
      if (e instanceof JsonSizeError) {
        console.log("VERDICT: PARSE_ERROR");
        console.error(e.message);
        process.exit(1);
      }
      console.error(`anchor unreadable: ${e.message}`);
    }
  }
  const v = anchorPath !== undefined ? verdictWithAnchor(text, anchor) : verdict(text);
  console.log("VERDICT: " + v);
  process.exit(v === "VALID" ? 0 : 1);
} else {
  argumentError("an attestation path is required");
}
