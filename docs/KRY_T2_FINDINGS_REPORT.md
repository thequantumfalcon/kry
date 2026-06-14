# ARCHIVE — KRY T2 Veracity Findings Report

> Historical TLSNotary/host-integration report. It records prototype
> evidence and host wiring notes, including host-repo-specific paths, but it is not the
> current standalone release checklist. Use `README.md`,
> `docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md`, and `docs/RELEASE_CHECKLIST.md` for
> current commands.

**Date:** 2026-06-04 · **Status:** T2 proven LIVE end-to-end — authed 200 notarized + minted (`tlsn_attested`)
**Scope:** closing the highest rung of KRY's veracity ladder (`tee_attested` / TLS-notary)

---

## Executive summary

KRY's credibility rests on a single distinction: a hash chain proves a balance is **intact**
(*integrity*), but not that the savings **happened** (*veracity*). KRY makes that gap explicit
with a per-receipt evidence tier and a published `veracity_floor`. Two of the three tiers were
already shipped (T0 self-reported, T1 provider-metered). The top rung — **T2**, an *external*
cryptographic anchor — was a documented slot with no implementation.

This work **closed that gap to a working prototype.** Using [TLSNotary](https://github.com/tlsnotary/tlsn),
we produced a cryptographic, **third-party-verifiable proof that `openrouter.ai` returned a
specific response over a genuine TLS session** — verified by a stranger using real CA roots and
the notary's signature, with no trust in the operator's runtime.

| Question | Answer |
|---|---|
| Can a provider's API response be notarized without trusting the operator? | **Yes — demonstrated against production openrouter.ai.** |
| Does it verify against real public CA roots (not a test fixture)? | **Yes** — `CryptoProvider::default()`, Google Trust Services chain. |
| Is this trustless to a stranger *today*? | **Not yet** — the notary is self-hosted (see [Trust ceiling](#trust-ceiling)). |
| What does it cost in hardware? | **$0** — runs in WSL2 on existing lab hardware. |

---

## 1 · The problem

```text
 INTEGRITY (what the hash chain proves)
 ───────────────────────────────────────
 "no receipt was inserted, removed, or altered,
 and the conserved balance follows from the chain"
 │
 │ does NOT imply
 ▼
 VERACITY (what the chain CANNOT prove)
 ───────────────────────────────────────
 "the efficiency events actually happened"
```

An operator can author a perfectly conserved chain of **fabricated** receipts and it verifies as
intact. The veracity ladder answers "how much must I trust the operator?" with a per-receipt label:

| Tier | Anchor | Before this work |
|------|--------|------------------|
| T0 `self_reported` | operator runtime | shipped |
| T1 `provider_metered` | the provider's own usage record | shipped + reconcilable (F1) |
| **T2** `tee_attested` / TLS-notary | hardware / a TLS-notary signature | **slot only — no implementation** |

T2 is the only honest anchor for **counterfactual** savings (e.g. cache hits), which leave zero
provider-side footprint. Without it, a cache-dominated balance reads `veracity_floor = 0.0`.

---

## 2 · What was proven

The full TLSNotary flow — **prove → present → verify** — ran against production `openrouter.ai`,
and an independent verifier confirmed the notarized bytes:

```text
Successfully verified data from a TLS session with openrouter.ai:

HTTP/1.1 401 Unauthorized
Date: Fri, 05 Jun 2026 03:39:15 GMT
Content-Type: application/json
Server: cloudflare
CF-RAY: a06c262dff230d15-SLC

{"error":{"message":"No cookie auth credentials found","code":401}}
```

This is a cryptographic proof — bound to the real TLS session keys, signed by the notary — that
`openrouter.ai` returned exactly these bytes. With an API key, the identical flow on
`/api/v1/credits` returns a **200 with real provider usage**, which is the provider-attested
quantity a KRY **T2 receipt** mints against.

### Architecture (what runs where)

```text
 ┌─────────────────────────── the notary host (WSL2 Ubuntu-22.04) ───────────────────────────┐
 │ │
 │ PROVER ───MPC-TLS───► (in-process) NOTARY │
 │ │ real TLS 1.2 session │ signs an attestation over │
 │ │ to the real server │ the session transcript (k256) │
 │ ▼ ▼ │
 │ openrouter.ai ◄──────── TCP/443 ───── attestation.tlsn + secrets.tlsn │
 │ (Google Trust Services cert) │ │
 └────────────────────────────────────────────────── │ ─────────────────────────────┘
 ▼
 PRESENT — selective disclosure (reveal/redact)
 │
 ▼
 presentation.tlsn ──► VERIFY (any stranger)
 real webpki CA roots + notary pubkey
 │
 ▼
 KRY T2 mint (scripts/kry_tlsn_verify.py — next step)
 provider-attested usage → tee_attested receipt
```

The notary runs **in-process** with the prover for the prototype — no separate server to deploy.

---

## 3 · Engineering finding (the bug, and why it mattered)

The first OpenRouter run produced a valid attestation but **failed verification** with
*"certificate is valid for provided server name"* — even though openrouter.ai's certificate
genuinely covers `openrouter.ai`.

Root cause, isolated by inspecting the binary artifacts (`strings *.tlsn` showed **both**
`test-server.io` and `openrouter.ai`): the example records the server identity in **two** places —
the prover's TLS connection **and** the attestation request the notary signs. Only the first was
updated; the notary bound the *fixture's* name (`test-server.io`) while the captured certificate
was openrouter's, so the verifier's name check failed (the path check passed).

```text
 prover TLS conn .server_name(openrouter.ai) ✅ fixed first → SNI/cert correct
 AttestationRequest.server_name(test-server.io) ❌ missed → recorded identity wrong
 → verify name-check fails
```

A secondary hypothesis (a CDN serving a different certificate to the constrained MPC-TLS
ClientHello) was **falsified** directly: openrouter.ai serves the same `CN=openrouter.ai`
certificate even for a forced TLS-1.2 / single-ECDSA-cipher handshake. The lesson — *verify the
recorded identity, not just the connection* — is captured in `tlsnotary/openrouter-t2.patch`.

---

## 4 · Infrastructure decision

```text
 ┌────────┬───────────────┬──────────┬───────────────────┬──────────────────────────┐
 │ Node │ OS │ RAM │ Linux path │ Role │
 ├────────┼───────────────┼──────────┼───────────────────┼──────────────────────────┤
 │ Mac │ macOS 26.5 │ 48 GB │ native Unix │ prover-side (bridge lives │
 │ │ │ │ (no Rust yet) │ here); dev box (sleeps) │
 │ the notary host ★ │ Windows 11 │ 98 GB │ WSL2 Ubuntu-22.04 │ NOTARY HOST (always-on) │
 │ node-b │ Windows 11 │ 32 GB │ WSL2 Ubuntu-22.04 │ warm-standby notary │
 │ node-c │ Windows 11 │ 32 GB │ WSL2 Ubuntu-22.04 │ Ollama / displacement │
 └────────┴───────────────┴──────────┴───────────────────┴──────────────────────────┘
```

There is no bare-metal Linux box, but **all three Windows nodes expose WSL2 Ubuntu-22.04**, which
runs the Linux-native `tlsn` toolchain. the notary host is the machine (always-on, most RAM); **node-b**
is an identical warm standby for redundancy. The TLS-1.2 prerequisite was confirmed on Google,
OpenRouter, OpenAI, and Google AI Studio before any build.

---

## 5 · Trust ceiling

A **self-hosted notary you control is not a neutral third party.** This prototype upgrades a claim
from *"operator self-report"* to *"the bytes are cryptographically bound to a real provider TLS
session"* — materially stronger, because the operator can no longer fabricate the response freely.
But it is **not trustless-to-a-stranger** until an *independent* party runs the notary (a public
TLSNotary notary, or a counterparty's). The prototype proves the mechanism; neutrality is a
deliberate later step, stated so it is never overclaimed.

---

## 6 · Roadmap to production T2

1. ✅ **Authed call (done 2026-06-04)** — `AUTH_HEADER="Bearer $OPENROUTER_API_KEY"` (env-driven,
 added to `prove.rs` in `tlsnotary/openrouter-t2.patch`) on `/api/v1/generation?id=<id>` →
 a **notarized 200 with real per-request token usage** (`native_tokens_prompt:23,
 native_tokens_completion:12`, nvidia free leg, $0). The key is **redacted** by selective
 disclosure — the notary signs the `Authorization` header without revealing it.
2. ✅ **`scripts/kry_tlsn_adapter.py` + `scripts/kry_tlsn_verify.py` (shipped 2026-06-04)** —
 the adapter turns the verifier's stdout into a presentation JSON; verify parses it, fail-closed
 (refuses non-verified / wrong-server / non-200), evidence-bound (replay can't double-mint), and
 mints a `tlsn_attested` (T2) receipt — F1-reconcilable when the body carries a generation id.
 Honest tier: anchors the displacement call that *happened*, not a cache-hit counterfactual
 (those stay `tee_attested`). 24 tests (`test_tlsn_verify.py` + `test_tlsn_adapter.py`).
 **Proven live end-to-end:** minted `KRY-00000001` (12 KRY, `tlsn_attested`), `veracity_floor`
 0.0 → 1.0, against the notarized openrouter.ai 200 above. *Caveat:* the avoided model on that
 demo mint was **declared**, not a real routing decision — see step 4.
3. **Independent notary** — move the notary off the prover host to earn true third-party trust
 (the remaining trust-ceiling gap, §5).
4. ✅ **Real displacement context (done 2026-06-04)** — `kry_tlsn_verify.py` now resolves the
 legs from genuine sources instead of a declared value: **served** = the model named in the
 notarized body (cryptographically attested); **avoided** = the host's recorded routing decision
 for that gen id (a prior `/openrouter:<id>`+`avoided_model` receipt — the existing `kry_or_fetch`
 contract, so no bridge change needed). `--avoided-model`/`--served-model` are overrides.
 **Honest gate:** with neither a routing record nor an explicit flag, it **refuses to mint**
 (`NO_DISPLACEMENT_CONTEXT`) — because `value_multiplier(None)=1.0` would otherwise silently
 credit full value off a counterfactual we can't substantiate. The remaining piece is operational:
 ensure the host actually routes T2-notarized calls through its displacement path so the routing
 receipt exists.

Separately, the **multi-node settlement** design (HOLE D corollary: lease/nonce/TTL > signed-sync
log > primary-node > consensus) lives in `KRY_VERACITY_BINDING.md` and is a roadmap `BUILD` item —
orthogonal to the veracity ladder, not yet implemented.

---

## 7 · Two open items — investigated, runbook (fresh-session work)

### 7a · Independent off-prover notary — RESOLVED + PROVEN (2026-06-06)
Previously the notary ran **in-process** with the prover (`prove.rs` wires it via
`tokio::io::duplex`) signing with a dummy `[1u8; 32]` key, so the operator controlled it — not
trustless-to-a-stranger. Now closed, end-to-end, on **node-b** (warm standby per §4):

**Correction to the original runbook:** this tlsn checkout (`28614ef`) has **no `notary-server`
crate** — the workspace has no such package, so `cargo run -p notary-server` does not apply. In this
version the notary is the in-process verifier role inside `prove.rs`. The version-appropriate fix is
to split that role into its own TCP-listening binary:

1. **`notary_tcp` example** (`tlsnotary/notary_tcp.rs`) — a verbatim copy of `prove.rs::notary()`
 wrapped in a `TcpListener`, signing with a **real key from `NOTARY_KEY_HEX`** (a 32-byte hex
 seed) instead of the dummy, and printing its compressed k256 public key at startup. Built into
 the examples crate (`tlsnotary/notary-tcp-itemB.diff` adds the `[[example]]` entry).
2. **`prove.rs` gate** (same diff) — when `NOTARY_ADDR` is set, the prover opens a
 `TcpStream::connect(addr)` to the remote notary and runs the MPC-TLS session over it; otherwise
 the original in-process duplex path is **unchanged** (so the §7b item-A flow still works as-is).
3. **Published key.** The notary's k256 verifying key is printed at startup and recorded here
 out-of-band: a verifier trusts the *notary* (whoever holds that key), not the prover operator.

**Live proof (2026-06-06).** Binary built on the notary host, copied to node-b (sha256 verified), key generated
**on node-b** (`openssl rand -hex 32`, so the prover never sees it). node-b reachable from the notary host via a
`netsh portproxy` rule (`0.0.0.0:7047 → <node-b-WSL-IP>:7047`) + firewall allow, since WSL2 NAT does not
expose the port on the LAN by default. The prover on the notary host ran with `NOTARY_ADDR=<notary-ip>:7047`
against a fresh authed-200 `GET /api/v1/generation?id=…` to openrouter.ai; MPC-TLS ran the notary host↔node-b over
the LAN. `attestation_verify` reported:

```
Verifying presentation with k256 key: 03e8c048ef848dcbc4e50c98bb5b6753e2495b823ec40efb6b2bd0c0fcff7302cd
Successfully verified … session with openrouter.ai
```

That key (`03e8c048…7302cd`) is **node-b's independent notary key** — distinct from the in-process dummy
(`031b84c5…`). The resulting presentation flows through `kry_tlsn_adapter.py` → `kry_tlsn_verify.py`
identically (mints `tlsn_attested`, chain valid), so the KRY pipeline is notary-agnostic: it trusts
the published key, whoever signs.

**Reproduce:** build `--example notary_tcp` (after applying `tlsnotary/notary-tcp-itemB.diff` and
dropping in `tlsnotary/notary_tcp.rs`); on the notary host `NOTARY_KEY_HEX=$(cat notary_key.hex)
NOTARY_BIND=0.0.0.0:7047 ./notary_tcp`; portproxy+firewall the host port; on the prover set
`NOTARY_ADDR=<notary-host>:7047` alongside the §7b appendix env.

**Reproducibility — 10/10 (2026-06-06).** Ran 10 consecutive rounds against one running node-b notary:
each round a fresh MPC-TLS session (fresh `accept()`, fresh signature) → `prove`/`present`/`verify`
→ adapter → scratch mint. All 10 passed every assertion: `PROVE_RC=0`, "Successfully verified",
notary key **constant** at `03e8c048…7302cd`, `Authorization` redacted, and a valid `tlsn_attested`
scratch mint with a valid chain. The notary's own log recorded exactly 10 "attestation issued"
(bidirectional confirmation). The notary loop is correct and serves repeatedly.

**Deployment caveat the repro test surfaced (real, worth pinning):** under WSL2 a `nohup`'d notary
gets reaped when the VM idles — it must be held by a **live session or a real service** (systemd /
Windows service), not a detached background process. (First repro attempt scored 1/10 because the
notary, launched detached and orphaned, was reaped after one round; with a session holding it open
it was 10/10.)

**Hardening — notary-key pin DONE (2026-06-06):** `kry_tlsn_verify` now accepts `--notary-key` to
PIN the expected notary's hex k256 verifying key; `validate_presentation` refuses (fail-closed) any
presentation whose `notary_key` doesn't exactly match (and refuses a presentation that carries no
notary key when a pin is demanded). This closes the gap that the signature/CA-chain check alone left
open — it proves a notary vouched, not *which* notary; without the pin a presentation from any notary
the operator stands up would mint. Exact full-key match only (normalized for `0x`/case); no prefix
match (a prefix pin is a weaker guarantee). Covered by 4 tests in `tests/test_tlsn_verify.py`
(match / mismatch / pin-but-absent / no-pin-unchanged). Pin the node-b key `03e8c048…7302cd` at verify
time.

**Pin validated LIVE against fresh notarized data (2026-06-06).** A fresh authed-200 on
`openai/gpt-oss-20b:free` (gen `gen-1780713650-…`, native prompt 76 / completion 20, $0) was
notarized end-to-end TODAY on the notary host (WSL) (prove → present → verify, in-process notary key
`031b84c5…`, Authorization redacted), adapted, and run through `kry_tlsn_verify`: a WRONG
`--notary-key` was REJECTED (fail-closed) and the CORRECT key minted `tlsn_attested` (19 KRY,
`veracity_floor 0→1`, chain valid) — into a **scratch** ledger; production untouched. This re-proves
the §7b live pipeline freshly and exercises the new pin on real bytes, not a fixture.

**Managed service — DELIVERED as a deployable artifact (§7a item b).** `tlsnotary/notary_service/`
holds a systemd unit installer (`install_notary_service.sh`, `Restart=always`, boot-persistent —
closes the WSL2 reap caveat above), a LAN-expose helper (`expose_notary_lan.cmd`, netsh portproxy +
firewall), and a runbook (`README.md`). The operator deploys it on node-b with one command (it needs
sudo; the password is the operator's to type, never piped). Verified prerequisites on node-b: WSL has
`systemd=true`, `~/notary_tcp` + `~/notary_key.hex` present, published key `03e8c048…7302cd`.
Remaining hardening (still genuinely open): the persistent service must be **deployed** by the
operator (one privileged command), and item **(c)** — a **third party** operating the notary for true
social neutrality — is organizational, not code.

### 7b · Host routing → T2 mint — RESOLVED + WIRED (2026-06-05)

**Found:** the host's routing bridge already mints a displacement receipt
stamping `detail = displacement/<served>/openrouter:<id>` + `avoided_model` + `served_model`. So
`kry_tlsn_verify`'s routing lookup *will* find it. The producing side was untouched (it already
stamps everything needed); both gaps were resolved on the **consuming** side:

- **Ledger split — resolved operationally.** Run the verify with `KRY_DATA_DIR=<the host system>/data`. The
 host's `_MINT_LOG_PATH` is the hardcoded `kry_data/kry_mint_log.jsonl`; the standalone's
 `_kry_data_dir()` honors `KRY_DATA_DIR`, so that env points the standalone at the **identical
 file**. Both modules then read/write one log. `src/kry/kry_mint.py` got a minimal port
 (`TIER_TLSN_ATTESTED` into `_VALID_TIERS`/`_ANCHORED_TIERS` + the promotion overlay below) so the
 host `/kry` surface reads that unified log consistently. Cross-module chain validity confirmed.
- **Double-credit — RESOLVED via option (iii): net-out + tier-upgrade** (operator decision
 2026-06-05). The host *already* credits the displacement at **T1 `provider_metered`**; the saving
 is credited ONCE. When `kry_tlsn_verify` finds a prior host receipt for the gen id
 (`_find_t1_receipt_for_gen`), it takes the **promotion path exclusively**: `promote_to_tlsn()`
 appends a **zero-value** `tier_promotion` receipt (`supersedes=<T1 id>`, tlsn evidence binding,
 **no `earn()`**) — supply/balance unchanged. `veracity_breakdown` **re-tiers** the superseded T1
 value from its tier onto `tlsn_attested`, and exposes a new **`tlsn_attested_fraction`**. The
 binary `veracity_floor` is **unchanged** when the T1 was already anchored (both tiers are anchored)
 and rises honestly if the T1 was `self_reported`. Only when **no** prior host receipt exists
 (standalone/manual run) does T2 mint fresh value — no double to avoid then. The path is
 **idempotent** (a re-run is an `ALREADY_UPGRADED` no-op, never a second mint).

 *Friction note:* under the binary floor, a T1 `provider_metered` OpenRouter call is **already**
 anchored, so host→T2 does not move the floor *number* for it — T2 only strengthens the *kind* of
 anchoring (operator-retained-payload → can't-fabricate-the-bytes). `tlsn_attested_fraction`
 surfaces that upgrade without inventing tier weights (Avoid A15).

 **Tests:** `tests/test_tlsn_verify.py::test_avoided_model_from_routing_log_upgrades_not_double_credits`
 (kry) and `tests/test_token/test_veracity_tier.py::test_tlsn_promotion_overlay_retiers_without_double_count`
 (the host system). Proven end-to-end on real files: seed host T1 → `KRY_DATA_DIR=<uni> kry_tlsn_verify` →
 `UPGRADED … re-tiered 1140 KRY → tlsn_attested`, `tlsn_attested_fraction 0.0→1.0`, total unchanged,
 host `/kry` agrees, chain valid.

 **Operational runbook (the live flip):** after a real authed-200 notarization (the notary host (WSL), §7a flow),
 run `KRY_DATA_DIR=<host-data-dir> PYTHONPATH=src python scripts/kry_tlsn_verify.py presentation.json
 --server openrouter.ai` (no `--avoided-model`; it resolves from the host routing log). That is the
 only remaining step and it needs a fresh notarized presentation.

### 7c · Redundancy
Mirror the notary build on node-b (warm standby), once 7a lands.

---

## Appendix · Reproduction

Environment + patch: `tlsnotary/README.md`. In a `tlsn` clone at commit `28614ef`:

```bash
git apply tlsnotary/openrouter-t2.patch
cargo build --release -p tlsn-examples
# authed, per-request token usage (key redacted by the notary):
SERVER_HOST=openrouter.ai SERVER_PORT=443 SERVER_DOMAIN=openrouter.ai \
 URI="/api/v1/generation?id=<gen-id>" AUTH_HEADER="Bearer $OPENROUTER_API_KEY" \
 cargo run --release --example attestation_prove
cargo run --release --example attestation_present
cargo run --release --example attestation_verify \
 | python scripts/kry_tlsn_adapter.py - --out presentation.json # then kry_tlsn_verify.py
```

A real verified presentation from the 2026-06-04 live run (notary key public, `Authorization`
redacted, the nvidia free-leg generation record disclosed) is captured verbatim at
`tlsnotary/example_verified_openrouter_200.txt` — it doubles as a real-format adapter fixture.

Local KRY lifecycle (no network): `bash examples/demo.sh` (see the live demo).
