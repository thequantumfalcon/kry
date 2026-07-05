// KRY verifier — independent second implementation (JavaScript / Node/Deno).
//
// Written to KRY-SPEC v1.0 (../../SPEC.md); verifies both the savings and action
// attestation profiles and is checked against the shared conformance corpus
// (../../vectors/) — the SC2 implementation-independence gate.
//
// The one cross-language subtlety (SPEC §2.1 / §3.1): the OUTER attestation_hash
// binds raw JSON numbers, and CPython's json preserves int-vs-float by the presence
// of a decimal point, which JSON.parse discards. We therefore parse with a
// number-PRESERVING parser (keep each number's exact source text) and emit that text
// verbatim in canonical output — reproducing CPython byte-for-byte without emulating
// its float repr. The INNER chain never needs this: every economic number is bound
// through canon_f64 (IEEE-754 big-endian hex), which is language-neutral by design.
//
// Usage:
//   node verify.mjs <attestation.json>     # verify one; prints VALID/INVALID, exit 0/1
//   node verify.mjs --vectors <dir>        # run the whole conformance corpus

import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

// ── number-preserving JSON ────────────────────────────────────────────────────
class Num {                       // preserves the exact source literal of a number
  constructor(raw) { this.raw = raw; this.val = Number(raw); }
}
function parse(text) {
  let i = 0;
  const ws = () => { while (i < text.length && " \t\n\r".includes(text[i])) i++; };
  function val() {
    ws();
    const c = text[i];
    if (c === "{") return obj();
    if (c === "[") return arr();
    if (c === '"') return str();
    if (c === "t") { expect("true"); return true; }
    if (c === "f") { expect("false"); return false; }
    if (c === "n") { expect("null"); return null; }
    if (c === "N" || c === "I" || (c === "-" && text[i + 1] === "I"))
      throw new Error("non-standard JSON constant rejected"); // NaN / Infinity / -Infinity
    return num();
  }
  function expect(w) { if (text.slice(i, i + w.length) !== w) throw new Error("bad literal"); i += w.length; }
  function num() {
    const start = i;
    if (text[i] === "-") i++;
    while (i < text.length && "0123456789.eE+-".includes(text[i])) i++;
    const raw = text.slice(start, i);
    if (!/^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$/.test(raw)) throw new Error("bad number " + raw);
    return new Num(raw);
  }
  function str() {
    i++; let s = "";
    while (text[i] !== '"') {
      if (text[i] === "\\") {
        const e = text[++i];
        if (e === "u") { s += String.fromCharCode(parseInt(text.slice(i + 1, i + 5), 16)); i += 5; }
        else { s += { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" }[e]; i++; }
      } else s += text[i++];
    }
    i++; return s;
  }
  function arr() { i++; ws(); const a = []; if (text[i] === "]") { i++; return a; } while (true) { a.push(val()); ws(); if (text[i] === ",") { i++; continue; } if (text[i] === "]") { i++; return a; } throw new Error("bad array"); } }
  function obj() { i++; ws(); const o = new Map(); if (text[i] === "}") { i++; return o; } while (true) { ws(); const k = str(); ws(); if (text[i++] !== ":") throw new Error("bad object"); o.set(k, val()); ws(); if (text[i] === ",") { i++; continue; } if (text[i] === "}") { i++; return o; } throw new Error("bad object"); } }
  const v = val(); ws();
  if (i !== text.length) throw new Error("trailing data");
  return v;
}

// ── canonical serialization (SPEC §2.1) ───────────────────────────────────────
function escStr(s) {                       // Python json ensure_ascii=True escaping
  let out = '"';
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (cp === 0x08) out += "\\b";
    else if (cp === 0x09) out += "\\t";
    else if (cp === 0x0a) out += "\\n";
    else if (cp === 0x0c) out += "\\f";
    else if (cp === 0x0d) out += "\\r";
    else if (cp < 0x20) out += "\\u" + cp.toString(16).padStart(4, "0");
    else if (cp < 0x7f) out += ch;
    else if (cp <= 0xffff) out += "\\u" + cp.toString(16).padStart(4, "0");
    else { // astral -> UTF-16 surrogate pair, matching CPython
      const v = cp - 0x10000;
      out += "\\u" + (0xd800 + (v >> 10)).toString(16).padStart(4, "0");
      out += "\\u" + (0xdc00 + (v & 0x3ff)).toString(16).padStart(4, "0");
    }
  }
  return out + '"';
}
function canon(v) {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (v instanceof Num) return v.raw;                 // preserve exact literal
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(v);
  if (typeof v === "string") return escStr(v);
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  if (v instanceof Map) {
    const keys = [...v.keys()].sort();                // lexicographic by code unit
    return "{" + keys.map((k) => escStr(k) + ":" + canon(v.get(k))).join(",") + "}";
  }
  if (typeof v === "object") { // plain object built internally (blocks / payloads)
    const keys = Object.keys(v).sort();
    return "{" + keys.map((k) => escStr(k) + ":" + canon(v[k])).join(",") + "}";
  }
  throw new Error("uncanonicalizable");
}
const sha = (s) => createHash("sha256").update(s, "utf8").digest("hex");

// ── canon_f64 (SPEC §2.2) ─────────────────────────────────────────────────────
function canonF64(x, sentinel) {
  const n = x instanceof Num ? x.val : (typeof x === "number" ? x : Number.NaN);
  if (x instanceof Num) { /* val */ } else if (typeof x !== "number") return sentinel;
  if (!Number.isFinite(n)) return sentinel;
  const b = Buffer.alloc(8); b.writeDoubleBE(n, 0); return b.toString("hex");
}
const SENT_SAVINGS = "nonfinite";
const SENT_ACTION = "ffffffffffffffff";

// helpers to read Map-or-object fields
const get = (m, k, d = undefined) => (m instanceof Map ? (m.has(k) ? m.get(k) : d) : (k in m ? m[k] : d));
const numval = (x, d = undefined) => (x instanceof Num ? x.val : (typeof x === "number" ? x : d));
const isNumLike = (x) => x instanceof Num || (typeof x === "number" && !Number.isNaN(x));
const r4 = (x) => Math.round(x * 1e4) / 1e4;
const r6 = (x) => Math.round(x * 1e6) / 1e6;

// ── published price multipliers (SPEC §3.4.1) ─────────────────────────────────
const EARN_RATES = { cache_hit: 1.0, l3_semantic_match: 0.8, short_circuit: 1.0, compression: 0.6, feed_bag_deposit: 0.7, cache_creation: 0.0, continuity_capsule: 0.1 };
let MULTIPLIERS = null; // loaded from vectors/primitives/legal_multipliers.json when available
function multiplierLegal(m) {
  const set = MULTIPLIERS ?? [1.0];
  return set.some((v) => Math.abs(v - m) <= 1e-3);
}

const ANCHORED_SAVINGS = new Set(["holdout_validated", "provider_metered", "tee_attested", "tlsn_attested"]);

// ── savings verification (SPEC §3) ────────────────────────────────────────────
function publicBlock(link) {
  const hv = numval(get(link, "hash_version", 1));
  const B = {};
  if (hv >= 5) {
    B.hash_version = new Num(String(hv));
    B.tokens_saved = canonF64(get(link, "tokens_saved", 0), SENT_SAVINGS);
    B.ts = canonF64(get(link, "ts"), SENT_SAVINGS);
    B.evidence_tier = get(link, "evidence_tier", "self_reported");
    B.metered_tokens = get(link, "metered_tokens", null) ?? null;
    B.kry_minted = canonF64(get(link, "kry_minted"), SENT_SAVINGS);
    B.earn_rate = canonF64(get(link, "earn_rate", 0), SENT_SAVINGS);
  } else {
    B.hash_version = new Num(String(hv));
    B.tokens_saved = get(link, "tokens_saved", new Num("0"));
    B.ts = get(link, "ts", null) ?? null;
    B.evidence_tier = get(link, "evidence_tier", "self_reported");
    B.metered_tokens = get(link, "metered_tokens", null) ?? null;
    B.kry_minted = get(link, "kry_minted", null) ?? null;
    B.earn_rate = get(link, "earn_rate", new Num("0"));
  }
  const sup = get(link, "supersedes", null);
  if (sup !== null && sup !== undefined) B.supersedes = sup;
  if (hv >= 6) B.receipt_id = get(link, "receipt_id", "") || "";
  if (hv >= 7) B.event_type = get(link, "event_type", "") || "";
  return canon(B);
}

function verifySavings(att) {
  const errs = [];
  const links = get(att, "links", []);
  if (!Array.isArray(links)) return ["links must be a list"];
  const receipts = get(att, "receipts");
  if (numval(receipts, -1) !== links.length) errs.push("receipts != len(links)");
  if (get(att, "chain_valid") !== true) errs.push("chain_valid not true");

  let prev = "0".repeat(64), prevVer = 0, total = 0;
  const counts = {}, byTier = {};
  for (const link of links) {
    const seq = get(link, "seq"), rh = get(link, "receipt_hash"), ch = get(link, "chain_hash");
    const et = get(link, "event_type"), km = get(link, "kry_minted");
    if (!(seq instanceof Num) || !Number.isInteger(numval(seq)) || numval(seq) < 0) errs.push("bad seq");
    if (typeof rh !== "string" || !rh) { errs.push("bad receipt_hash"); continue; }
    if (typeof ch !== "string" || !ch) { errs.push("bad chain_hash"); continue; }
    if (typeof et !== "string" || !et) { errs.push("bad event_type"); continue; }
    if (!isNumLike(km) || numval(km) < 0) { errs.push("bad kry_minted"); continue; }
    let hv = numval(get(link, "hash_version", 1), 1);
    if (!Number.isInteger(hv)) hv = 1;
    if (hv < prevVer) errs.push("version downgrade");
    prevVer = Math.max(prevVer, hv);
    const expected = hv >= 4 ? sha(`${prev}:${rh}:${publicBlock(link)}`) : sha(`${prev}:${rh}`);
    if (ch !== expected) errs.push(`seq ${numval(seq)}: chain broken`);
    let tier = get(link, "evidence_tier", "self_reported");
    if (typeof tier !== "string") { errs.push("tier not string"); tier = "self_reported"; }
    if (hv < 4 && tier !== "self_reported") { errs.push("pre-v4 anchored tier"); tier = "self_reported"; }
    total += numval(km);
    counts[et] = (counts[et] || 0) + 1;
    byTier[tier] = (byTier[tier] || 0) + numval(km);
    errs.push(...magnitudeErrors(link));
    errs.push(...tierSchemaErrors(link));
    prev = expected;
  }
  // envelope
  if (isNumLike(get(att, "total_kry")) && Math.abs(numval(get(att, "total_kry")) - r4(total)) > 1e-9) errs.push("total_kry mismatch");
  if (isNumLike(get(att, "usd_equivalent")) && Math.abs(numval(get(att, "usd_equivalent")) - r6(r4(total) * 0.000025)) > 1e-9) errs.push("usd_equivalent mismatch");
  const declHead = get(att, "chain_head");
  if (declHead !== prev) errs.push("chain_head mismatch");
  // veracity (a non-object veracity, e.g. null, is treated as empty — matches the reference)
  const _ver = get(att, "veracity");
  const ver = _ver instanceof Map ? _ver : new Map();
  const anchored = r4(Object.entries(byTier).filter(([t]) => ANCHORED_SAVINGS.has(t)).reduce((a, [, v]) => a + v, 0));
  const selfRep = r4(byTier["self_reported"] || 0);
  if (Math.abs(numval(get(ver, "anchored_kry", 0), 0) - anchored) > 1e-4) errs.push("anchored_kry mismatch");
  if (Math.abs(numval(get(ver, "self_reported_kry", 0), 0) - selfRep) > 1e-4) errs.push("self_reported_kry mismatch");
  const floor = total > 0 ? r4(anchored / total) : 0;
  if (isNumLike(get(ver, "veracity_floor")) && Math.abs(numval(get(ver, "veracity_floor")) - floor) > 1e-4) errs.push("veracity_floor mismatch");
  // attestation_hash — canonicalize the whole attestation with the field blanked
  const declAH = get(att, "attestation_hash");
  const clone = cloneWith(att, "attestation_hash", "");
  if (declAH !== sha(canon(clone))) errs.push("attestation_hash mismatch");
  return errs;
}

function magnitudeErrors(link) {
  const declares = has(link, "earn_rate") && has(link, "tokens_saved");
  const km = numval(get(link, "kry_minted"), NaN), ts = numval(get(link, "tokens_saved", 0), 0), rate = numval(get(link, "earn_rate", 0), 0);
  if (!(km >= 0) || !(ts >= 0) || !(rate >= 0)) return ["magnitude: bad number"];
  if (ts <= 0 || rate <= 0) return (declares && km > 0) ? ["magnitude: kry from zero inputs"] : [];
  const et = get(link, "event_type", "");
  const pub = et in EARN_RATES ? EARN_RATES[et] : 0.5;
  const out = [];
  if (Math.abs(rate - pub) > 1e-6) out.push("non-standard rate");
  const implied = km / (ts * rate);
  if (!multiplierLegal(implied)) out.push("illegal multiplier");
  return out;
}
function tierSchemaErrors(link) {
  if (get(link, "evidence_tier", "self_reported") !== "provider_metered") return [];
  if (!isNumLike(get(link, "ts")) || numval(get(link, "ts")) < 0) return ["metered: bad ts"];
  const m = get(link, "metered_tokens");
  if (!Array.isArray(m) || m.length !== 2) return ["metered: missing metered_tokens"];
  for (const x of m) { if (!(x instanceof Num) || !Number.isInteger(x.val) || x.val < 0) return ["metered: bad tokens"]; }
  return [];
}
const has = (m, k) => (m instanceof Map ? m.has(k) : k in m);
function cloneWith(m, key, value) { // shallow map clone with one key overridden
  const c = new Map(m instanceof Map ? m : Object.entries(m)); c.set(key, value); return c;
}

// ── action verification (SPEC §4) ─────────────────────────────────────────────
const ANCHORED_ACTION = new Set(["server_witnessed", "attested"]);
function actionPayload(link) {
  return {
    action_hash_version: new Num("1"),
    tool: get(link, "tool", ""),
    args_commit: get(link, "args_commit", ""),
    result_commit: get(link, "result_commit", null) ?? null,
    status: get(link, "status", ""),
    ts: canonF64(get(link, "ts"), SENT_ACTION),
    agent_id: get(link, "agent_id", ""),
    evidence_tier: get(link, "evidence_tier", ""),
    server_evidence_commit: get(link, "server_evidence_commit", null) ?? null,
  };
}
function verifyAction(att) {
  const errs = [];
  if (get(att, "kind") !== "kry_action_attestation") return ["not an action attestation"];
  if (numval(get(att, "action_hash_version"), -1) !== 1) return ["unsupported action_hash_version"];
  const links = get(att, "links");
  if (!Array.isArray(links)) return ["links missing"];
  let prev = "0".repeat(64); const seen = new Set();
  for (const link of links) {
    if (!(link instanceof Map)) return ["link not an object"];
    const rid = get(link, "receipt_id", "");
    if (typeof rid !== "string") return ["receipt_id not a string"];
    if (seen.has(rid)) return ["duplicate receipt_id"];
    seen.add(rid);
    const rh = sha(canon(actionPayload(link)));
    if (get(link, "receipt_hash") !== rh) return ["receipt_hash mismatch"];
    const ch = sha(`${prev}:${rh}`);
    if (get(link, "chain_hash") !== ch) return ["chain_hash mismatch"];
    prev = ch;
  }
  if (get(att, "chain_tip") !== prev) return ["chain_tip mismatch"];
  if (numval(get(att, "action_count"), -1) !== links.length) return ["action_count mismatch"];
  const total = links.length;
  const anchored = links.filter((l) => ANCHORED_ACTION.has(get(l, "evidence_tier")) && get(l, "server_evidence_commit")).length;
  const derived = total > 0 ? r4(anchored / total) : 0;
  const _ver = get(att, "veracity");
  const ver = _ver instanceof Map ? _ver : new Map();
  const claimed = get(ver, "veracity_floor");
  if (isNumLike(claimed) && Math.abs(numval(claimed) - derived) > 0.01) return ["veracity_floor mismatch"];
  return errs;
}

// ── top-level verdict ─────────────────────────────────────────────────────────
export function verdict(rawText) {
  let att;
  try { att = parse(rawText); } catch { return "PARSE_ERROR"; }
  try {
    const kind = att instanceof Map ? get(att, "kind") : undefined;
    const errs = kind === "kry_action_attestation" ? verifyAction(att) : verifySavings(att);
    return errs.length === 0 ? "VALID" : "INVALID";
  } catch { return "INVALID"; }   // fail closed on any unexpected shape (SPEC §1)
}

// ── corpus runner + CLI ───────────────────────────────────────────────────────
function runVectors(dir) {
  const man = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));
  try { MULTIPLIERS = JSON.parse(readFileSync(join(dir, "primitives", "legal_multipliers.json"), "utf8")).multipliers; } catch {}
  let pass = 0, fail = 0; const fails = [];
  // primitives (exact bytes)
  const prim = JSON.parse(readFileSync(join(dir, "primitives", "canon_f64.json"), "utf8"));
  for (const c of prim.cases) {
    if (c.input_number === undefined) continue;
    const got = canonF64(new Num(String(c.input_number)), SENT_SAVINGS);
    if (got === c.expected_hex) pass++; else { fail++; fails.push(`canon_f64/${c.label}: ${got} != ${c.expected_hex}`); }
  }
  const cj = JSON.parse(readFileSync(join(dir, "primitives", "canonical_json.json"), "utf8"));
  for (const c of cj.cases) {
    const got = canon(parse(JSON.stringify(c.input_object)));
    if (got === c.expected_bytes) pass++; else { fail++; fails.push(`canonical_json/${c.label}: ${got} != ${c.expected_bytes}`); }
  }
  // attestation vectors
  for (const v of man.vectors) {
    if (v.category.startsWith("primitives")) continue;
    const raw = readFileSync(join(dir, v.category, v.id + ".json"), "utf8");
    const spec = JSON.parse(raw);
    const exp = spec.expected.verdict;
    let inputText;
    if (spec.input_raw_text !== undefined) inputText = spec.input_raw_text;
    else { // re-extract the exact input JSON text so numbers are preserved verbatim
      const m = raw.match(/"input":\s*([\s\S]*?),\n\s*"expected"/);
      inputText = m ? m[1] : JSON.stringify(spec.input);
    }
    const got = verdict(inputText);
    if (got === exp) pass++; else { fail++; fails.push(`${v.category}/${v.id}: got ${got}, expected ${exp}`); }
  }
  console.log(`JS verifier vs corpus: ${pass} passed, ${fail} failed`);
  for (const f of fails) console.log("  FAIL " + f);
  return fail === 0;
}

const arg = process.argv[2];
if (arg === "--vectors") {
  process.exit(runVectors(process.argv[3]) ? 0 : 1);
} else if (arg === "--batch") {
  // one attestation JSON per line -> one verdict per line (for differential fuzzing).
  if (process.argv[4]) { try { MULTIPLIERS = JSON.parse(readFileSync(process.argv[4], "utf8")).multipliers; } catch {} }
  const lines = readFileSync(process.argv[3], "utf8").split("\n");
  const out = [];
  for (const line of lines) { if (line.length === 0) continue; let v; try { v = verdict(line); } catch { v = "CRASH"; } out.push(v); }
  process.stdout.write(out.join("\n") + "\n");
} else if (arg) {
  const v = verdict(readFileSync(arg, "utf8"));
  console.log("VERDICT: " + v);
  process.exit(v === "VALID" ? 0 : 1);
}
