"""kry_action — tamper-evident, stranger-verifiable receipts for agent ACTIONS.

The savings layer (kry_mint/kry_attest) answers "did this efficiency event happen
and is the ledger intact?". This layer answers the analogous question for what an
LLM agent *does*: "did this agent take exactly these actions, in this order, and
has the log been edited after the fact?".

It reuses the savings layer's machinery exactly, so an action chain is verifiable
byte-for-byte by the SAME kind of stdlib-only, trust-nothing stranger verifier
(see scripts/kry_action_verify.py), including from a non-Python language:

  * canonical JSON          json.dumps(allow_nan=False, sort_keys=True, separators=(",",":"))
  * language-neutral floats struct.pack(">d", f).hex()  (IEEE-754 big-endian)
  * receipt_hash = SHA-256(canonical receipt payload)
  * chain_hash   = SHA-256(prev_chain_hash : receipt_hash)        # both fixed-len hex
  * genesis      = "0" * 64

integrity != veracity, applied to actions
-----------------------------------------
The hash chain proves the action LOG is intact, ordered, and append-only — and,
against a *published* chain-head anchor (scripts/kry_action_verify.py --anchor),
that nobody re-minted it from genesis. It does NOT prove the action *happened* in
the world. Every receipt is classified by how the action was witnessed:

    T0  self_reported     the agent runtime asserts the call (a PERMANENT floor)
    T1  server_witnessed  bound to the tool SERVER's own response (a real result_commit
                          + a server_evidence_commit the auditor can reconcile)
    T2  attested          a TEE / TLS-notary signature over the call (server_evidence_commit)

`veracity_floor` is the fraction of actions backed by something stronger than bare
self-report (T1 + T2). A pure-T0 action log reads veracity_floor = 0.0 — the honest
label for "this is the agent's own word for what it did". It is published as-is.

privacy / content-sealing
-------------------------
Raw arguments and results are NEVER persisted or exposed — only their SHA-256
COMMITMENTS (`args_commit`, `result_commit`). A holder of the original arguments
proves they match by recomputing the commitment; a stranger sees only commitments
plus metadata (tool name, status, timestamp, agent id, tier). Nothing leaks.

Stdlib only. No third-party imports.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── version + tiers ───────────────────────────────────────────────────────────

ACTION_HASH_VERSION = 1  # bump only on a breaking change to the hash preimage

TIER_SELF_REPORTED = "self_reported"
TIER_SERVER_WITNESSED = "server_witnessed"
TIER_ATTESTED = "attested"
_VALID_TIERS = {TIER_SELF_REPORTED, TIER_SERVER_WITNESSED, TIER_ATTESTED}
_ANCHORED_TIERS = {TIER_SERVER_WITNESSED, TIER_ATTESTED}  # stronger than self-report

_VALID_STATUS = {"ok", "error", "pending"}

GENESIS = "0" * 64
_V5_BAD = "ff" * 8  # canonical sentinel for a non-numeric / NaN / inf ts (only a TAMPERED link)

# ── canonical helpers (byte-identical to kry_mint / kry_verify) ────────────────


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _canon(value) -> str:
    """Canonical JSON: sorted keys, tight separators, NaN/inf rejected. The one
    serialization every hash preimage and every verifier must agree on."""
    return _json_dumps(value, sort_keys=True, separators=(",", ":"))


def _canon_f64(x) -> str:
    """Language-neutral number: the EXACT IEEE-754 double in big-endian hex.
    Python struct.pack('>d'); JS DataView.setFloat64(0,x,false); Rust f64::to_be_bytes;
    Go math.Float64bits+BigEndian. No float->string formatting, no precision loss.
    A non-numeric / NaN / inf value (only a tampered link) -> a fixed sentinel, so a
    verifier yields a clean hash MISMATCH rather than crashing. Mirrors kry_mint._canon_f64."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return _V5_BAD
    if f != f or f in (float("inf"), float("-inf")):
        return _V5_BAD
    return struct.pack(">d", f).hex()


def commit(value) -> str:
    """SHA-256 commitment to an argument/result value (or any JSON-serializable
    object). This is what gets stored — never the raw value. Recompute it over your
    retained raw data to prove the data matches a published receipt."""
    return hashlib.sha256(_canon(value).encode()).hexdigest()


def _kry_data_dir() -> Path:
    return Path(os.environ.get("KRY_DATA_DIR", "./kry_data"))


def _action_log_path() -> Path:
    return _kry_data_dir() / "kry_action_log.jsonl"


# ── the receipt ───────────────────────────────────────────────────────────────


@dataclass
class ActionReceipt:
    """A content-free, hash-chained receipt that an agent took one action.

    receipt_hash = SHA-256(canonical receipt payload)   # all fields below, content-free
    chain_hash   = SHA-256(previous_chain_hash : receipt_hash)
    """
    receipt_id: str
    tool: str                       # action / tool name (exposed + bound — audit-relevant)
    args_commit: str                # SHA-256 commitment to the arguments (never the raw args)
    result_commit: Optional[str]    # SHA-256 commitment to the result, or None (pending / no result)
    status: str                     # "ok" | "error" | "pending"
    ts: float                       # unix timestamp
    agent_id: str                   # which agent / session took the action (bound)
    evidence_tier: str              # self_reported | server_witnessed | attested
    server_evidence_commit: Optional[str]  # commitment to the external witness (T1/T2 only)
    receipt_hash: str
    chain_hash: str
    action_hash_version: int = ACTION_HASH_VERSION

    # The exact preimage every verifier (incl. non-Python) reconstructs.
    @staticmethod
    def _payload(*, tool: str, args_commit: str, result_commit: Optional[str],
                 status: str, ts: float, agent_id: str, evidence_tier: str,
                 server_evidence_commit: Optional[str]) -> dict:
        return {
            "action_hash_version": ACTION_HASH_VERSION,
            "tool": tool,
            "args_commit": args_commit,
            "result_commit": result_commit,
            "status": status,
            "ts": _canon_f64(ts),  # bound as language-neutral hex; stored as a float below
            "agent_id": agent_id,
            "evidence_tier": evidence_tier,
            "server_evidence_commit": server_evidence_commit,
        }

    @classmethod
    def create(
        cls,
        receipt_id: str,
        tool: str,
        args_commit: str,
        previous_chain_hash: str,
        *,
        result_commit: Optional[str] = None,
        status: str = "ok",
        ts: Optional[float] = None,
        agent_id: str = "default",
        evidence_tier: str = TIER_SELF_REPORTED,
        server_evidence_commit: Optional[str] = None,
    ) -> "ActionReceipt":
        if not isinstance(tool, str) or not tool:
            raise ValueError("tool must be a non-empty string")
        if not isinstance(args_commit, str) or len(args_commit) != 64:
            raise ValueError("args_commit must be a 64-hex SHA-256 commitment")
        if status not in _VALID_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUS)}")
        if evidence_tier not in _VALID_TIERS:
            raise ValueError(f"evidence_tier must be one of {sorted(_VALID_TIERS)}")
        # The minter REFUSES to create a false anchored tier: claiming server_witnessed
        # or attested requires a real external-witness commitment. (The stranger verifier
        # independently COERCES any forged anchored link with no witness down to T0, so a
        # tampered log line can never inflate the floor either.)
        if evidence_tier in _ANCHORED_TIERS and not server_evidence_commit:
            raise ValueError(
                f"tier {evidence_tier!r} requires server_evidence_commit "
                "(an external witness — the server's response or a notary/TEE doc)")
        if evidence_tier == TIER_SELF_REPORTED:
            server_evidence_commit = None  # T0 carries no witness, by definition

        ts = float(ts) if ts is not None else time.time()
        if not math.isfinite(ts) or ts <= 0:
            raise ValueError("ts must be a positive finite number")

        payload = cls._payload(
            tool=tool, args_commit=args_commit, result_commit=result_commit,
            status=status, ts=ts, agent_id=agent_id, evidence_tier=evidence_tier,
            server_evidence_commit=server_evidence_commit)
        receipt_hash = hashlib.sha256(_canon(payload).encode()).hexdigest()
        chain_hash = hashlib.sha256(f"{previous_chain_hash}:{receipt_hash}".encode()).hexdigest()

        return cls(
            receipt_id=receipt_id, tool=tool, args_commit=args_commit,
            result_commit=result_commit, status=status, ts=ts, agent_id=agent_id,
            evidence_tier=evidence_tier, server_evidence_commit=server_evidence_commit,
            receipt_hash=receipt_hash, chain_hash=chain_hash,
            action_hash_version=ACTION_HASH_VERSION)

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "tool": self.tool,
            "args_commit": self.args_commit,
            "result_commit": self.result_commit,
            "status": self.status,
            "ts": self.ts,
            "agent_id": self.agent_id,
            "evidence_tier": self.evidence_tier,
            "server_evidence_commit": self.server_evidence_commit,
            "receipt_hash": self.receipt_hash,
            "chain_hash": self.chain_hash,
            "action_hash_version": self.action_hash_version,
        }


# ── append-only log + chain state ─────────────────────────────────────────────

_COUNTER = 0
_CHAIN_TIP = GENESIS
_INITIALISED = False
_LOCK = threading.Lock()  # single-process. Cross-process mirrors kry._locks (see note in record()).


def _load_tip_from_log() -> tuple[int, str]:
    """Recover (count, tip) from the persisted log so a new process appends to the
    existing chain rather than restarting from genesis."""
    path = _action_log_path()
    if not path.exists():
        return 0, GENESIS
    count, tip = 0, GENESIS
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = _json_loads(line)
            tip = rec.get("chain_hash", tip)
            count += 1
    return count, tip


def _ensure_init() -> None:
    global _INITIALISED, _COUNTER, _CHAIN_TIP
    if _INITIALISED:
        return
    _COUNTER, _CHAIN_TIP = _load_tip_from_log()
    _INITIALISED = True


def reset_state_for_tests() -> None:
    """Drop in-memory chain state. Tests that point KRY_DATA_DIR at a fresh tmpdir
    call this so the module re-reads the (empty) log instead of a stale tip."""
    global _INITIALISED, _COUNTER, _CHAIN_TIP
    _INITIALISED = False
    _COUNTER = 0
    _CHAIN_TIP = GENESIS


def record(
    tool: str,
    args,
    *,
    result=None,
    has_result: bool = True,
    status: str = "ok",
    agent_id: str = "default",
    evidence_tier: str = TIER_SELF_REPORTED,
    server_evidence=None,
    ts: Optional[float] = None,
) -> ActionReceipt:
    """Record one agent action and append a content-free receipt to the chain.

    `args` / `result` / `server_evidence` are passed RAW and committed internally —
    only their SHA-256 commitments are stored. Pass already-computed 64-hex strings
    for args_commit/result_commit/server_evidence_commit if you committed them
    elsewhere (they are detected and passed through).

    Returns the ActionReceipt (whose .chain_hash is the new tip — publish it as an
    anchor with `export_anchor()` to make a later re-mint detectable).

    NOTE on concurrency: this uses a single in-process lock. The savings layer uses
    kry._locks.cross_process_lock for multi-process safety; wiring that here is the
    same one-line change and is intentionally left out to keep this module droppable
    and testable in isolation. Single-writer per process is the supported mode.
    """
    global _COUNTER, _CHAIN_TIP
    args_commit = args if _is_commit(args) else commit(args)
    if not has_result:
        result_commit = None
    else:
        result_commit = result if _is_commit(result) else commit(result)
    server_evidence_commit = None
    if server_evidence is not None:
        server_evidence_commit = (
            server_evidence if _is_commit(server_evidence) else commit(server_evidence))

    with _LOCK:
        _ensure_init()
        receipt = ActionReceipt.create(
            receipt_id=f"act-{_COUNTER + 1}",
            tool=tool,
            args_commit=args_commit,
            previous_chain_hash=_CHAIN_TIP,
            result_commit=result_commit,
            status=status,
            ts=ts,
            agent_id=agent_id,
            evidence_tier=evidence_tier,
            server_evidence_commit=server_evidence_commit,
        )
        path = _action_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(_canon(receipt.to_dict()) + "\n")
        _COUNTER += 1
        _CHAIN_TIP = receipt.chain_hash
    return receipt


def _is_commit(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def chain_tip() -> tuple[int, str]:
    """(count, tip) for the persisted log — what export_anchor() publishes."""
    return _load_tip_from_log()


def export_anchor() -> dict:
    """A content-free {count, tip} commitment to PUBLISH out-of-band. A verifier
    holding it (`kry_action_verify.py --anchor`) catches any retroactive re-mint."""
    count, tip = _load_tip_from_log()
    return {"kind": "kry_action_anchor", "count": count, "chain_tip": tip,
            "action_hash_version": ACTION_HASH_VERSION}


# ── internal verifier (the standalone stranger verifier re-implements this) ────


def verify_action_chain(log_path: Optional[Path] = None) -> tuple[bool, list[str]]:
    """Re-derive the whole chain from genesis. Catches any edit / reorder / insert /
    drop. (This is the IN-PACKAGE check; scripts/kry_action_verify.py is the
    trust-nothing replica a stranger runs.)"""
    path = Path(log_path) if log_path is not None else _action_log_path()
    errors: list[str] = []
    if not path.exists():
        return True, []  # an empty chain is vacuously intact
    prev = GENESIS
    n = 0
    with path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = _json_loads(line)
            except ValueError as exc:
                errors.append(f"line {lineno}: not valid JSON ({exc})")
                return False, errors
            payload = ActionReceipt._payload(
                tool=rec.get("tool", ""),
                args_commit=rec.get("args_commit", ""),
                result_commit=rec.get("result_commit"),
                status=rec.get("status", ""),
                ts=rec.get("ts"),
                agent_id=rec.get("agent_id", ""),
                evidence_tier=rec.get("evidence_tier", ""),
                server_evidence_commit=rec.get("server_evidence_commit"),
            )
            exp_receipt = hashlib.sha256(_canon(payload).encode()).hexdigest()
            if rec.get("receipt_hash") != exp_receipt:
                errors.append(f"line {lineno} ({rec.get('receipt_id','?')}): "
                              "receipt_hash mismatch — a field was tampered")
                return False, errors
            exp_chain = hashlib.sha256(f"{prev}:{exp_receipt}".encode()).hexdigest()
            if rec.get("chain_hash") != exp_chain:
                errors.append(f"line {lineno} ({rec.get('receipt_id','?')}): "
                              "chain_hash mismatch — broken/reordered/inserted/dropped link")
                return False, errors
            prev = exp_chain
            n += 1
    return (len(errors) == 0), errors


# ── public attestation (content-free, what a stranger verifies) ───────────────


def _veracity(links: list[dict]) -> dict:
    total = len(links)
    by_tier: dict[str, int] = {}
    anchored = 0
    for link in links:
        tier = link.get("evidence_tier", TIER_SELF_REPORTED)
        # An anchored tier with no witness is NOT credited (mirror the verifier).
        if tier in _ANCHORED_TIERS and not link.get("server_evidence_commit"):
            tier = TIER_SELF_REPORTED
        by_tier[tier] = by_tier.get(tier, 0) + 1
        if tier in _ANCHORED_TIERS:
            anchored += 1
    return {
        "by_tier": by_tier,
        "anchored_actions": anchored,
        "total_actions": total,
        "veracity_floor": round(anchored / total, 4) if total > 0 else 0.0,
    }


def build_action_attestation(log_path: Optional[Path] = None) -> dict:
    """A public, content-free proof of the action log: every link's commitments +
    metadata + the chain tip + the veracity surface. No raw args/results/witnesses —
    safe to hand to a third party. They run kry_action_verify.py to check it."""
    path = Path(log_path) if log_path is not None else _action_log_path()
    links: list[dict] = []
    tip = GENESIS
    if path.exists():
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = _json_loads(line)
                links.append({
                    "receipt_id": rec.get("receipt_id"),
                    "tool": rec.get("tool"),
                    "args_commit": rec.get("args_commit"),
                    "result_commit": rec.get("result_commit"),
                    "status": rec.get("status"),
                    "ts": rec.get("ts"),
                    "agent_id": rec.get("agent_id"),
                    "evidence_tier": rec.get("evidence_tier"),
                    "server_evidence_commit": rec.get("server_evidence_commit"),
                    "receipt_hash": rec.get("receipt_hash"),
                    "chain_hash": rec.get("chain_hash"),
                    "action_hash_version": rec.get("action_hash_version", ACTION_HASH_VERSION),
                })
                tip = rec.get("chain_hash", tip)
    return {
        "kind": "kry_action_attestation",
        "action_hash_version": ACTION_HASH_VERSION,
        "action_count": len(links),
        "chain_tip": tip,
        "links": links,
        "veracity": _veracity(links),
    }


def assert_no_content_leak(attestation: dict, private_strings: list[str]) -> bool:
    """Fail loudly if any raw private string (an argument value, a result, a prompt)
    appears anywhere in the public attestation. The content-sealing guarantee."""
    blob = _canon(attestation)
    for s in private_strings:
        if s and s in blob:
            raise AssertionError(f"content leak: private string present in attestation: {s[:40]!r}")
    return True
