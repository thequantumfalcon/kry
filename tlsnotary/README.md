# TLSNotary T2 prototype — provider-attested veracity for KRY

This directory holds the **T2 (TLS-notary attested) prototype** for KRY's veracity ladder.
T2 is the only honest external anchor for counterfactual/efficiency claims: a third party
cryptographically verifies what a provider's API returned over a real TLS session, without
trusting the operator.

## Status — proven LIVE end-to-end, authed + minted (2026-06-04)

`prove → present → verify` runs the full [TLSNotary](https://github.com/tlsnotary/tlsn)
flow against **production `openrouter.ai`**, and a stranger's verifier confirms the
notarized bytes using real CA roots + the notary's signature. The **authed 200** path is
now proven, with the API key **redacted** by selective disclosure (the notary signs the
`Authorization` header without revealing it) and the provider's own per-request token counts
disclosed:

```text
Successfully verified that the data below came from a session with openrouter.ai at <time>.
...
authorization: XXXXXXXXXXXXXXXXXXXX (redacted — signed, not revealed)
...
HTTP/1.1 200 OK
{"data":{... "native_tokens_prompt":23,"native_tokens_completion":12,"total_cost":0,
 "provider_responses":[{"provider_name":"Nvidia","status":200}], "id":"gen-…"}}
```

That verified presentation fed `scripts/kry_tlsn_adapter.py | scripts/kry_tlsn_verify.py`
and minted the first live **`tlsn_attested` (T2)** receipt, lifting `veracity_floor`. The
earlier unauthed run (`401 No cookie auth credentials found`) proved the mechanism; this
authed run is the real provider-attested anchor. `/api/v1/generation?id=` gives per-request
tokens (used here); `/api/v1/credits` attests dollars, not tokens.

## What runs where (decided 2026-06-04)

- **Notary + prover**: the notary host, inside **WSL2 Ubuntu-22.04** (the only Linux environment in the
 lab; `tlsn` is Rust/Linux-native). The attestation example runs the notary **in-process**,
 so no separate notary server is needed for the prototype. Rust 1.96.0 (matches `tlsn` HEAD).
- **The Rust `tlsn` toolchain is NOT vendored here** — it's a clone on the notary host. This patch is
 the durable record of the changes that make the example target a real provider.
- **The KRY-side glue** (parsing a verified presentation → a T2 mint) lives in this repo as
 Python (see "Next") — stdlib-only, consistent with the rest of KRY.

## `openrouter-t2.patch`

A git diff against `tlsnotary/tlsn` @ `28614ef` (the HEAD that pins Rust 1.96.0). It changes
the `attestation` example to notarize a real CA-signed server instead of the bundled
self-signed fixture:

- **prove.rs** — `SERVER_DOMAIN`/`URI` env overrides; real webpki roots
 (`webpki-root-certs`) instead of the fixture CA; drop fixture client-auth; relax the
 `== OK` assert. **Critically**, the attestation request's recorded server name
 (`AttestationRequest::builder().server_name`, ~line 224) must use the runtime domain too —
 leaving it as the fixture default makes verify fail the cert **name** check while the path
 check passes (the bug we hit and fixed).
- **present.rs** — `reveal_all = true` (shape-agnostic; the fixture-specific JSON field
 selection panics on other response shapes).
- **verify.rs** — `CryptoProvider::default()` (real webpki roots), per the file's own
 "in production, use this" note.
- **prove.rs** (authed) — an env-driven `AUTH_HEADER` is appended to the request, so a real
 `Authorization: Bearer <key>` can be supplied without hardcoding a secret (the stock
 `Authenticated` example only sends a fixed dummy token). Unset → unauthenticated request.
- **Cargo.toml** — adds `webpki-root-certs = "1"`.

### Apply + run

```bash
# in a tlsn clone at commit 28614ef
git apply openrouter-t2.patch
cargo build --release -p tlsn-examples

# authed (the real T2 anchor): per-request token usage for a past generation.
# AUTH_HEADER is redacted in the presentation — signed by the notary, never revealed.
SERVER_HOST=openrouter.ai SERVER_PORT=443 SERVER_DOMAIN=openrouter.ai \
 URI="/api/v1/generation?id=<gen-id>" AUTH_HEADER="Bearer $OPENROUTER_API_KEY" \
 cargo run --release --example attestation_prove
cargo run --release --example attestation_present
cargo run --release --example attestation_verify # stranger's check, real CA roots
```

### Pipe a verified run into a KRY T2 mint

The verifier prints prose; the two KRY-side scripts (this repo, stdlib-only) turn that
into a mint:

```bash
# capture the verifier's output, adapt it to a presentation JSON, then mint:
cargo run --release --example attestation_verify | \
 python3 scripts/kry_tlsn_adapter.py - --out presentation.json
python3 scripts/kry_tlsn_verify.py presentation.json --server openrouter.ai \
 --avoided-model gh/claude-opus-4.8 # --dry-run to inspect without minting
```

`kry_tlsn_adapter.py` parses the `Successfully verified … session with <server>` line
(the proof signal) + the revealed `Data received:` transcript; `kry_tlsn_verify.py`
validates it fail-closed and mints a `tlsn_attested` (T2) receipt that folds into
`veracity_floor`. A per-request token anchor needs `URI=/api/v1/generation?id=<id>`
(a 200 carrying `native_tokens_*`); `/api/v1/credits` attests dollars, not tokens.

Note: `/api/v1/models` (412 KB) exceeds the example's `MAX_RECV_DATA` (16 KB) and MPC-TLS is
per-byte expensive — use a small endpoint like `/api/v1/credits` (67 B).

## Trust ceiling (state this plainly)

A notary **in-process with the prover** is not a neutral third party. This upgrades the claim
from "operator self-report (T0)" to "the bytes are cryptographically bound to a real
provider TLS session" — genuinely stronger, but **not** trustless-to-a-stranger until an
*independent* party runs the notary.

**Off-prover notary now demonstrated (2026-06-06):** `notary_tcp.rs` runs the notary as its own
TCP-listening process on a **separate host** with its **own published key** (the prover never holds
it); the prover dials it via `NOTARY_ADDR`. Proven 10/10 against a remote notary (key
`03e8c048…7302cd`) — see `notary_tcp.rs`, `notary-tcp-itemB.diff`, and §7a of
`docs/KRY_T2_FINDINGS_REPORT.md`. The remaining neutrality step is purely *who* runs it: a
counterparty's or a public TLSNotary notary, trusted by its out-of-band-published key.

## Next

1. ✅ **Authed call** (done 2026-06-04) — `AUTH_HEADER="Bearer $OPENROUTER_API_KEY"` on
 `/api/v1/generation?id=` → a notarized 200 with real per-request token usage (key redacted).
2. ✅ **`scripts/kry_tlsn_adapter.py` + `scripts/kry_tlsn_verify.py`** (done) — parse the
 verified presentation → mint a live `tlsn_attested` (T2) receipt; folds into `veracity_floor`.
3. ✅ **Independent off-prover notary** (done 2026-06-06) — `notary_tcp.rs` + `notary-tcp-itemB.diff`
 run the notary as a separate-host TCP process with its own published key; the prover connects via
 `NOTARY_ADDR`. Proven 10/10 (§7a of `docs/KRY_T2_FINDINGS_REPORT.md`). Remaining: have a
 *third party* (counterparty / public TLSNotary) run it, trusted by its published key.
4. **Real displacement context** — this run minted against a free-model leg with a *declared*
 avoided model; wire the host so the avoided/served models on a T2 mint are the genuine routing
 decision, not a demo value.
