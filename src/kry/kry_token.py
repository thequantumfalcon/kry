"""KRY — Proof-of-Efficiency Compute Credit.

A compute token earned through measurable inference efficiency and spent on
routing permission across the multi-provider bridge.

## Concept

Traditional LLM credits are bought with money (prepaid, subscription). KRY is
EARNED through recorded efficiency events — cache hits, compression savings, L3 semantic
deduplication, short-circuit avoidance (the hash chain proves the record intact, not that
each event happened; see veracity_floor). You cannot buy KRY directly; you earn it
by running the system well.

    Earning  =  compute you SAVED  (avoidance = value)
    Spending =  routing permission for expensive calls  (compute consumed)

This inverts the normal compute market: instead of "pay to use," it is
"earn by not wasting, spend permission to use when warranted."

## Biological parallel

KRY maps to ATP in cellular metabolism:
  - Glucose (cache hits)           → fast-burn KRY (immediate routing permission)
  - Fatty acids (compression wins) → slow-burn KRY (banked for sustained use)
  - IV bag (emergency reserve)     → phosphocreatine (burst permission)
  - Metabolic state                → enzyme availability (gates spending rate)

The standalone ledger in this module is the KRY wallet for this repository.
Historical host integrations can feed it, but the release artifact here
does not depend on host-repo-specific paths.

## Unit: 1 KRY

1 KRY = 1 frontier-equivalent output token saved or justified.

Frontier baseline = or/anthropic/claude-opus-4.8 non-fast = $25.00 / 1M output tokens
                  = $0.000025 per output token
                  = 1 KRY per token saved/spent

Exchange rates (from `_MODEL_OUTPUT_USD_PER_M`, dated by `PRICE_BASIS_AS_OF`):

  Provider tier    | $/M out | KRY cost per 1k out | Notes
  ─────────────────┼─────────┼─────────────────────┼──────────────────────
  Frontier Opus    | $25.00  | 1,000 KRY           | baseline
  Opus fast (OR)   | $50.00  | 2,000 KRY           | premium routing
  Sonnet-class     | $7.50   | 300 KRY             | estimate
  DeepSeek v4 Pro  | $1.10   | 44 KRY              | MIT, verified cheap
  Haiku-class      | $1.25   | 50 KRY              | fast low-cost
  Groq / NIM / AI  | $0.00   | 0 KRY               | free quota, no spend
  Local Ollama     | $0.00   | 0 KRY               | free, no spend

Earning rates (KRY earned per event):

  Event                    | KRY earned     | Why
  ─────────────────────────┼────────────────┼────────────────────────────
  Cache hit (bridge)       | tokens_saved   | Avoided frontier call
  L3 semantic match        | tokens_saved   | Avoided backend call
  Short-circuit (probe)    | prompt_tokens  | Avoided round-trip
  Compression saving       | tokens_saved   | Reduced output cost
  FeedBag deposit          | deposited * 0.7| IV bag portion earns full
  Cache creation           | 0.0 (a cost)   | Saving is the later cache_hit, not the write
  Continuity capsule       | tokens * 0.1   | Cross-session continuity reuse

## Novel claim vs. existing systems

OpenRouter Credits: prepaid USD → converted to per-call debits. No earning.
Together.ai / Replicate: same model — buy, spend.
GPU hours (AWS, GCP): denominated in real compute, not inference efficiency.
Bittensor TAO: compute on a blockchain, but not LLM-specific, not efficiency-earned.

KRY gap: no existing system denominates tokens in AVOIDED compute.
The token is proof-of-efficiency, not proof-of-work or proof-of-purchase.

## Minimal viable falsifier (self-audit discipline)

The concept is falsified if: running one full earn→bank→spend cycle produces
mathematically inconsistent accounting — i.e., more KRY spent than earned with
no legitimate routing outcome, or KRY earned without a verifiable efficiency event.

## Implementation

KRY accounting in this repository:
  - `KRYLedger` persists earned/spent balances under `KRY_DATA_DIR`
  - `kry_mint.py` records tamper-evident hash-chain mint receipts
  - `kry_attest.py` emits public attestations for stranger verification
  - `scripts/kry_verify.py` independently rechecks chain, magnitude, and veracity

This module: unit definition, exchange rates, earn/spend accounting,
cycle verification.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import logging
import math
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from kry._locks import cross_process_lock as _cross_process_lock


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    # IMPORT-PURITY: path CONSTRUCTION only — no mkdir. This runs at MODULE level to build the path
    # constants, so creating the dir here made a bare `import` write to the caller's cwd (and raise
    # PermissionError under a read-only one). Writers mkdir lazily BEFORE taking the lock —
    # cross_process_lock() opens `<path>.lock` in this dir, so it must exist by LOCK time, not just
    # by write time.
    return Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()

logger = logging.getLogger("kry.token")

_LEDGER_PATH = _kry_data_dir() / "kry_ledger.json"
_LEDGER_LOCK = threading.RLock()   # re-entrant: earn()/spend() hold it then call save()


class LedgerPersistenceError(RuntimeError):
    """A ledger write may have committed; inspect retained state before retrying."""


class _JournalHardLinkError(LedgerPersistenceError):
    """Journal activation requires hard links permitted by the data filesystem."""


def _journal_path() -> Path:
    return _LEDGER_PATH.with_name(_LEDGER_PATH.stem + ".v2" + _LEDGER_PATH.suffix)


def _legacy_backup_path() -> Path:
    return _LEDGER_PATH.with_name(_LEDGER_PATH.name + ".pre-v2")


def _ledger_tombstone() -> bytes:
    return (_json_dumps({"kry_ledger_tombstone": 2, "journal": _journal_path().name},
                        separators=(",", ":")) + "\n").encode()


def _ledger_storage_path() -> Path:
    """Resolve storage under the unchanged legacy coordination lock, never by fallback."""
    from ._journal_activation import _Activation
    activation = _Activation()
    if activation.files(".pending.activation-*") or activation.files(".pending.abandon-*"):
        raise ValueError("active journal activation requires explicit completion or abandonment")
    journal = _journal_path()
    evidence = (journal.exists() or journal.is_symlink() or
                activation.sentinel.exists() or activation.sentinel.is_symlink() or
                _legacy_backup_path().exists() or _legacy_backup_path().is_symlink() or
                any(journal.parent.glob(journal.name + ".pending.*")) or
                any(journal.parent.glob(journal.name + ".corrupt*")))
    marker = _LEDGER_PATH.read_bytes() if _LEDGER_PATH.exists() else None
    if evidence or marker == _ledger_tombstone():
        if marker != _ledger_tombstone() or not journal.is_file():
            raise ValueError("ledger fence incomplete or changed; restore the marker and complete v2 journal")
        activation.check_fence()
        return journal
    return _LEDGER_PATH


def _ledger_incidents() -> dict[str, str]:
    paths = []
    for target in (_LEDGER_PATH, _journal_path()):
        paths += list(target.parent.glob(target.name + ".corrupt*"))
        paths += list(target.parent.glob(target.name + ".pending.*"))
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _sync_ledger_directory() -> None:
    # POSIX needs the directory entry flushed as well as each file's contents.
    # Windows has no equivalent portable stdlib directory-fsync operation.
    if os.name == "posix":
        fd = os.open(_LEDGER_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _finite_number(value, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _event_from_raw(raw: dict) -> KRYEvent:
    return KRYEvent(
        ts=_finite_number(raw.get("ts", 0.0), "event.ts", nonnegative=True),
        kind=str(raw.get("kind", "")),
        source=str(raw.get("source", "")),
        amount=_finite_number(raw.get("amount", 0.0), "event.amount"),
        detail=str(raw.get("detail", "")),
        tx_id=str(raw.get("tx_id", "")),
    )


def _validate_op_id(op_id: str) -> str:
    if (not isinstance(op_id, str) or not op_id.strip() or
            ":" in op_id or "," in op_id):
        raise ValueError("op_id must be a nonempty string without ':' or ','")
    return op_id


def _operation_number(value, name: str) -> float:
    number = _finite_number(value, name, nonnegative=True)
    if isinstance(value, int) and value != number:
        raise ValueError(f"{name} is not exactly representable as a ledger number")
    return number or 0.0


def _ledger_json_loads(text: str) -> dict:
    duplicates = False

    def object_pairs(pairs):
        nonlocal duplicates
        result = {}
        for key, value in pairs:
            if key in result:
                duplicates = True
            result[key] = value
        return result

    raw = json.loads(text, parse_constant=_reject_json_constant,
                     object_pairs_hook=object_pairs)
    if isinstance(raw, dict) and "ledger_format" in raw and duplicates:
        raise ValueError("duplicate fields in versioned ledger")
    return raw


def _ledger_parts(raw: dict) -> tuple[dict, dict]:
    """Validate the opt-in envelope before any reader or recovery can adopt it."""
    if not isinstance(raw, dict):
        raise ValueError("ledger must be an object")
    if "ledger_format" not in raw:
        return raw, {}
    if (type(raw["ledger_format"]) is not int or raw["ledger_format"] != 2 or
            set(raw) != {"ledger_format", "accounting", "ops"}):
        raise ValueError("unsupported or malformed ledger format")
    accounting, operations = raw["accounting"], raw["ops"]
    if not isinstance(accounting, dict) or not isinstance(operations, dict):
        raise ValueError("accounting and ops must be objects")
    spent = accounting["total_spent"]
    _finite_number(spent, "total_spent", nonnegative=True)
    for op_id, record in operations.items():
        _validate_op_id(op_id)
        if not isinstance(record, dict) or set(record) != {"kind", "amount", "params_sha256", "ts"}:
            raise ValueError("invalid operation record")
        if record["kind"] not in ("spend", "debit_kry"):
            raise ValueError("invalid operation kind")
        amount = _operation_number(record["amount"], "operation.amount")
        _finite_number(record["ts"], "operation.ts", nonnegative=True)
        digest = record["params_sha256"]
        if (not isinstance(digest, str) or len(digest) != 64 or
                any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("invalid operation parameter hash")
        if amount > spent:
            raise ValueError("operation amount exceeds total_spent")
    return accounting, operations


# ── Unit definition ───────────────────────────────────────────────────────────

# Frontier baseline: or/anthropic/claude-opus-4.8 = $25.00 / 1M output tokens
FRONTIER_USD_PER_M_OUTPUT = 25.0
KRY_PER_FRONTIER_TOKEN = 1.0          # 1 KRY = 1 frontier-equivalent saved token
USD_PER_KRY = FRONTIER_USD_PER_M_OUTPUT / 1_000_000  # $0.000025

# Spending costs in KRY per 1000 output tokens routed
SPEND_RATES: dict[str, float] = {
    # Free tiers — no KRY spent (they earn from traffic, we route for free)
    "google":    0.0,
    "groq":      0.0,
    "nim":       0.0,
    "local":     0.0,
    "pool":      0.0,
    # Paid tiers — KRY proportional to frontier-equivalent cost
    "or/anthropic/claude-opus-4.8":       1000.0,   # $25/M → 1000 KRY/k
    "or/anthropic/claude-opus-4.8-fast":  2000.0,   # $50/M
    "or/anthropic/claude-opus-4.7-fast":  6000.0,   # $150/M
    "or/deepseek/deepseek-v4-pro":          44.0,   # $1.10/M
    "or/deepseek/deepseek-v4-flash":        11.0,   # $0.28/M
    "or/deepseek/deepseek-r1":              88.0,   # $2.19/M
    "or/qwen/qwen3.7-max":                 150.0,   # $3.75/M
    "fireworks/deepseek-v4-pro":            44.0,
    # Copilot (subscription) — nominal KRY per call (not per-token billing
    # but premium request quota is finite; model this as 500 KRY per call)
    "gh":       500.0,    # per call, not per token
    "ghm":        5.0,    # GitHub Models free tier — minimal
}

# Earning rates: KRY earned per efficiency event
EARN_RATES: dict[str, float] = {
    "cache_hit":         1.0,   # per token saved (full frontier value)
    "l3_semantic_match": 0.8,   # slightly discounted (approximate match)
    "short_circuit":     1.0,   # full value — complete avoidance
    "compression":       0.6,   # partial value — reduced output not zero
    "feed_bag_deposit":  0.7,   # IV bag rate (from fuel_ledger ratios)
    "cache_creation":    0.0,   # COST not saving: a cache write is a 1.25x premium/bet; the
                                # realized saving is the later cache_hit (1.0). Crediting both
                                # = double-count. Own-data audit 2026-06-03.
    "continuity_capsule": 0.1,  # MUST match kry_mint._EARN_RATES: the minter stamps the receipt
                                # (the tamper-evident chain) at this rate, and earn() credits the
                                # live ledger. If this key is missing here, earn() falls back to
                                # 0.5 and the ledger diverges 5x from the chain for this event.
}

# Fuel ledger → KRY conversion (1 fuel token ≈ 1 frontier-equivalent token)
KRY_PER_FUEL_TOKEN = 1.0

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class KRYEvent:
    ts: float
    kind: str           # "earn" or "spend"
    source: str         # event type or model
    amount: float       # KRY amount (positive = earn, negative = spend)
    detail: str = ""
    tx_id: str = ""     # future: hash-chain anchor


_MERGE_FIELDS = ("balance", "total_earned", "total_spent")


@dataclass
class KRYLedger:
    balance: float = 0.0
    total_earned: float = 0.0
    total_spent: float = 0.0
    events: list[KRYEvent] = field(default_factory=list)
    cycle_count: int = 0

    def __post_init__(self) -> None:
        self.balance = _finite_number(self.balance, "balance")
        self.total_earned = _finite_number(self.total_earned, "total_earned",
                                           nonnegative=True)
        self.total_spent = _finite_number(self.total_spent, "total_spent",
                                          nonnegative=True)
        self.cycle_count = int(self.cycle_count)
        # R6 delta-merge baseline: persist only THIS process's change since its
        # last save, added onto the on-disk value — so concurrent processes
        # (bridge + overnight + workers) ACCUMULATE instead of clobbering. This
        # is the same fix that stopped the original FuelLedger drain; the KRY
        # ledger had repeated the naive last-writer-wins bug.
        self._baseline: dict[str, float] = {k: float(getattr(self, k)) for k in _MERGE_FIELDS}
        self._recovery_required = False
        self._last_write_committed = False
        self._recovery: dict = {}
        self._ledger_format = 1
        self._ops: dict[str, dict] = {}

    def flow_balance(self, window_s: float = 3600.0, *, now: float | None = None) -> dict:
        """Toll-bridge solvency: is KRY flowing IN (savings) faster than OUT
        (premium spend) over the recent window? The bridge stays open while
        inflow ≥ outflow — 'the vehicle behind pays our toll'. This gates on
        FLOW (rate), not STOCK (balance) or COUNT (cap): transient zero-balance
        potholes don't matter if the system is genuinely self-funding.

        Returns earn_rate, spend_rate (KRY/hr), net flow, and self_funding bool.
        """
        import time as _t
        now = now if now is not None else _t.time()
        cutoff = now - window_s
        earned = sum(e.amount for e in self.events
                     if e.kind == "earn" and e.ts >= cutoff)
        spent = sum(-e.amount for e in self.events
                    if e.kind == "spend" and e.ts >= cutoff)
        hrs = max(window_s / 3600.0, 1e-9)
        earn_rate = earned / hrs
        spend_rate = spent / hrs
        return {
            "earn_rate_per_hr": round(earn_rate, 2),
            "spend_rate_per_hr": round(spend_rate, 2),
            "net_flow_per_hr": round(earn_rate - spend_rate, 2),
            "self_funding": earn_rate >= spend_rate,   # solvent: savings cover spend
            "window_s": window_s,
        }

    def solvency_early_warning(self, *, window_s: float = 600.0,
                               n_windows: int = 6, now: float | None = None) -> dict:
        """Predictive toll-bridge solvency — CSD on the flow series (predictive transfer).

        The flow gate is REACTIVE (knows when net flow IS negative). The
        critical-slowing-down idea (AR1 + variance) detects
        the APPROACH to a tipping point before it tips. Here the tipping point is
        net-flow crossing zero (insolvency). We sample net-flow over n recent
        sub-windows and warn when: rising lag-1 autocorrelation (the flow is
        trending persistently, not noisy) AND the trend projects a zero-crossing
        soon. Lets the bridge tighten BEFORE insolvency, not after.
        """
        import time as _t
        now = now if now is not None else _t.time()
        samples = []
        for i in range(n_windows):
            hi = now - i * window_s
            lo = hi - window_s
            earned = sum(e.amount for e in self.events
                         if e.kind == "earn" and lo <= e.ts < hi)
            spent = sum(-e.amount for e in self.events
                        if e.kind == "spend" and lo <= e.ts < hi)
            samples.append((earned - spent) / (window_s / 3600.0))  # net KRY/hr
        samples.reverse()  # oldest → newest
        # Inline lag-1 autocorrelation (CSD persistence signal). Inlined rather
        # than importing the operator's optimization loop so the token package
        # stays zero-coupling / standalone-portable.
        def _ar1(xs: list[float]) -> float:
            k = len(xs)
            if k < 3:
                return 0.0
            m = sum(xs) / k
            num = sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, k))
            den = sum((x - m) ** 2 for x in xs)
            return num / den if den > 1e-12 else 0.0
        ar1 = _ar1(samples)
        # Linear trend (slope per window) of net flow
        n = len(samples)
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(samples) / n
        denom = sum((x - mx) ** 2 for x in xs) or 1e-9
        slope = sum((xs[i] - mx) * (samples[i] - my) for i in range(n)) / denom
        current = samples[-1]
        # Windows-to-zero if the downward trend continues
        windows_to_zero = (current / -slope) if slope < -1e-9 else float("inf")
        approaching = (slope < 0 and ar1 > 0.5 and 0 < windows_to_zero <= 3)
        return {
            "current_net_flow_per_hr": round(current, 2),
            "trend_slope": round(slope, 3),
            "ar1": round(ar1, 3),                 # persistence (CSD signal)
            "windows_to_insolvency": (round(windows_to_zero, 1)
                                      if windows_to_zero != float("inf") else None),
            "approaching_insolvency": approaching,   # EARLY warning (pre-tip)
            "samples_net_flow": [round(s, 1) for s in samples],
        }

    @property
    def efficiency_ratio(self) -> float:
        return (1.0 / (1.0 + self.total_spent / self.total_earned)
                if self.total_earned > 0 else 0.0)

    @property
    def usd_equivalent_saved(self) -> float:
        return self.total_earned * USD_PER_KRY

    @classmethod
    def load_or_create(cls) -> "KRYLedger":
        ledger = cls()
        if not _LEDGER_PATH.parent.exists():
            return ledger
        # Initial absence and pending incidents must share the writer's lock:
        # a first writer can remove its intent between an unlocked glob and read.
        # A quarantine decision and rename must also refer to that locked state.
        # Callers acquire this loader before taking their own file lock.
        with _LEDGER_LOCK, _cross_process_lock(_LEDGER_PATH):
            try:
                if not _LEDGER_PATH.exists():
                    from kry.kry_mint import _MINT_LOG_PATH
                    ledger._recovery_required = bool(_ledger_incidents() or _MINT_LOG_PATH.exists())
                raw = ledger._read_locked()
                if raw:
                    ledger._adopt(raw)
            except (OSError, ValueError):
                # Read failure is not evidence that the bytes are corrupt. A
                # later operation retries the read under the same process lock.
                pass
            return ledger

    def _quarantine(self, path: Path | None = None) -> None:
        self._recovery_required = True
        path = path if path is not None else _ledger_storage_path()
        if path.exists():
            corrupt = path.with_suffix(path.suffix + ".corrupt")
            if corrupt.exists():
                corrupt = corrupt.with_name(corrupt.name + "." + uuid.uuid4().hex)
            try:
                os.replace(path, corrupt)
            except OSError:
                logger.error("KRY ledger could not be quarantined; writes remain blocked")
                return
        logger.error("KRY ledger quarantined; debit history must be recovered before further writes")

    def _adopt(self, raw: dict) -> None:
        # Validate into locals before replacing any cached state.
        ledger_format = 2 if "ledger_format" in raw else 1
        raw, operations = _ledger_parts(raw)
        values = {k: _finite_number(raw[k], k, nonnegative=(k != "balance"))
                  for k in _MERGE_FIELDS}
        events = [_event_from_raw(e) for e in raw.get("events", [])][-500:]
        cycle = int(raw.get("cycle_count", 0))
        recovery = raw.get("recovery", {})
        if not isinstance(recovery, dict):
            raise ValueError("recovery must be an object")
        self._recovery_required = True
        for k, value in values.items():
            setattr(self, k, value)
        self.events, self.cycle_count = events, cycle
        self._baseline = values.copy()
        self._recovery = recovery
        self._ledger_format = ledger_format
        self._ops = {key: value.copy() for key, value in operations.items()}
        self._recovery_required = False

    def _read_locked(self) -> dict:
        if self._recovery_required:
            raise ValueError("debit history unavailable; explicit ledger recovery acknowledgement required")
        try:
            path = _ledger_storage_path()
        except ValueError:
            self._recovery_required = True
            raise
        if not path.exists():
            from kry.kry_mint import _MINT_LOG_PATH
            missing_history = any(self._baseline.values()) or _MINT_LOG_PATH.exists()
            if (self._recovery_required or missing_history or
                    _ledger_incidents()):
                raise ValueError("debit history unavailable; restore the ledger before further writes")
            return {}
        # Only successfully read invalid contents warrant quarantine. In
        # particular EMFILE/EIO/permission failures must leave valid bytes alone.
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            self._quarantine(path)
            raise ValueError("debit history unavailable; invalid ledger encoding") from exc
        try:
            raw = _ledger_json_loads(text)
            if path == _journal_path() and (not isinstance(raw, dict) or "ledger_format" not in raw):
                raise ValueError("the journal path requires a format-2 envelope")
            accounting, _ = _ledger_parts(raw)
            for k in _MERGE_FIELDS:
                _finite_number(accounting[k], k, nonnegative=(k != "balance"))
            for event in accounting.get("events", []):
                _event_from_raw(event)
            int(accounting.get("cycle_count", 0))
            recovery = accounting.get("recovery", {})
            if not isinstance(recovery, dict):
                raise ValueError("recovery must be an object")
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
            self._quarantine(path)
            raise ValueError("debit history unavailable; restore the ledger before further writes") from exc
        if recovery.get("incidents", {}) != _ledger_incidents():
            self._recovery_required = True
            raise ValueError("debit history unavailable; explicit ledger recovery acknowledgement required")
        return raw

    def _refresh_locked(self) -> None:
        on_disk = self._read_locked()
        if any(getattr(self, k) != self._baseline[k] for k in _MERGE_FIELDS):
            self._save_locked(on_disk)
        elif on_disk:
            self._adopt(on_disk)

    def save(self) -> None:
        """Merge pending deltas into durable state under one process lock.

        Corruption or missing prior debit history requires explicit recovery.
        A failed pre-replace write preserves pending deltas. A lost replace
        acknowledgement is checked before releasing the lock, never inferred
        from the bounded event history during a later retry.
        """
        _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LEDGER_LOCK, _cross_process_lock(_LEDGER_PATH):
            self._save_locked()

    def _save_locked(self, on_disk: dict | None = None, *, recovery: dict | None = None) -> None:
        self._last_write_committed = False
        if self._recovery_required:
            raise ValueError("debit history unavailable; restore the ledger before further writes")
        try:
            storage = _ledger_storage_path()
        except ValueError:
            self._recovery_required = True
            raise
        if on_disk is None:
            on_disk = self._read_locked()
        disk_format = 2 if "ledger_format" in on_disk else 1
        on_disk, disk_ops = _ledger_parts(on_disk)
        merged = {}
        for k in _MERGE_FIELDS:
            disk_v = _finite_number(on_disk.get(k, 0.0), k)
            delta = _finite_number(getattr(self, k), k) - self._baseline.get(k, 0.0)
            merged[k] = _finite_number(disk_v + delta, k)
        # Preserve the direct-save legacy clamp. Public spend() selects its
        # debit from fresh state under this lock rather than relying on a clamp.
        if merged["balance"] < 0:
            merged["balance"] = 0.0
            merged["total_spent"] = merged["total_earned"]
        # Events accumulate across writers too. The scalars already do (delta-merge), but the
        # events list was written last-writer-wins, silently dropping a concurrent process's audit
        # records. Keep ALL of this process's in-memory events, and ADD only on-disk events that
        # carry a tx_id we don't already hold (another writer's new events). Legacy/empty-tx_id
        # disk events are skipped — they were already loaded into self.events at startup — so
        # distinct legacy records that happen to share a (ts,kind,source,amount,detail) payload are
        # NEVER collapsed by a tuple key, and our own events are never duplicated on a round-trip.
        # Build the merged state into LOCALS first; self/_baseline are adopted only AFTER a durable
        # write succeeds (see below), so a write failure loses nothing.
        _mine = {e.tx_id for e in self.events if e.tx_id}
        _combined = list(self.events)
        for _raw in on_disk.get("events", []):
            try:
                _de = _event_from_raw(_raw)
            except Exception:
                continue
            if _de.tx_id and _de.tx_id not in _mine:
                _combined.append(_de)
        _combined.sort(key=lambda x: x.ts)
        merged_events = _combined[-500:]
        # Divide before summing to avoid overflow in a display-only ratio.
        _ratio = (1.0 / (1.0 + merged["total_spent"] / merged["total_earned"])
                  if merged["total_earned"] > 0 else 0.0)
        data = {
            "balance": merged["balance"],
            "total_earned": merged["total_earned"],
            "total_spent": merged["total_spent"],
            "cycle_count": self.cycle_count,
            "usd_equivalent_saved": round(merged["total_earned"] * USD_PER_KRY, 6),
            "efficiency_ratio": round(_ratio, 4),
            "events": [asdict(e) for e in merged_events],
        }
        if recovery is None:
            recovery = on_disk.get("recovery", {}) if on_disk else self._recovery
        if recovery:
            data["recovery"] = recovery
        if self._ledger_format == 2 or disk_format == 2:
            operations = {key: value.copy() for key, value in disk_ops.items()}
            for key, value in self._ops.items():
                if key in operations and operations[key] != value:
                    raise ValueError("conflicting durable operation records")
                operations[key] = value.copy()
            data = {"ledger_format": 2, "accounting": data, "ops": operations}
            _ledger_parts(data)
        encoded = _json_dumps(data, indent=2)
        target = _journal_path() if "ledger_format" in data else _LEDGER_PATH
        activating = target == _journal_path() and storage == _LEDGER_PATH
        if activating:
            from ._journal_activation import _Activation
            _Activation().activate(self, data)
            return
        fd, tmp = tempfile.mkstemp(dir=_LEDGER_PATH.parent, prefix=".kry_")
        intent = target.with_name(target.name + ".pending." + uuid.uuid4().hex)
        replace_returned = False
        durable = False
        try:
            with os.fdopen(fd, "w") as f:
                f.write(encoded)
                f.flush()
                os.fsync(f.fileno())   # durability: the ledger must survive power loss, not tear
            # A crash or unreadable post-replace state leaves a durable incident
            # marker. Another process must not assume that this operation failed.
            with intent.open("x", encoding="utf-8") as f:
                f.write(_json_dumps({"target_sha256": hashlib.sha256(encoded.encode()).hexdigest()}))
                f.flush()
                os.fsync(f.fileno())
            _sync_ledger_directory()
            self._recovery_required = True
            os.replace(tmp, target)
            replace_returned = True
            _sync_ledger_directory()
            durable = True
            self._adopt(data)
            self._last_write_committed = True
            intent.unlink()
        except BaseException as exc:
            try:
                # While the lock still excludes other writers, distinguish a failed
                # write from a completed replace whose acknowledgement was lost.
                # Retrying the latter delta would credit/debit the same event twice.
                if self._last_write_committed:
                    pass
                elif not os.path.exists(tmp):
                    self._recovery_required = True
                    committed = durable
                    if not durable and not replace_returned:
                        try:
                            committed = target.read_text(encoding="utf-8") == encoded
                        except (OSError, UnicodeError):
                            committed = False
                    if committed and not durable:
                        # A lost replace acknowledgement can be resolved by exact
                        # bytes plus the durability barrier while still locked.
                        try:
                            _sync_ledger_directory()
                        except OSError:
                            committed = False
                    if committed:
                        self._adopt(data)
                        self._last_write_committed = True
                    else:
                        self._recovery_required = True
                else:
                    self._recovery_required = False
                    os.unlink(tmp)
                if ((self._last_write_committed and isinstance(exc, Exception)) or
                        (not self._last_write_committed and not self._recovery_required)):
                    intent.unlink(missing_ok=True)
            except BaseException:
                # A cleanup/readback error must not swallow the cancellation
                # that brought us here or reopen an unresolved operation.
                self._recovery_required = True
                if not isinstance(exc, Exception):
                    raise exc
                raise
            raise

    def _operation_snapshot(self) -> dict:
        data = {**{k: getattr(self, k) for k in _MERGE_FIELDS},
                "events": [asdict(e) for e in self.events], "cycle_count": self.cycle_count,
                "recovery": self._recovery}
        if self._ledger_format == 2:
            return {"ledger_format": 2, "accounting": data,
                    "ops": {key: value.copy() for key, value in self._ops.items()}}
        return data

    @contextmanager
    def _operation_locked(self):
        """Roll back public mutations even when cancellation precedes the save."""
        before = self._operation_snapshot()
        self._last_write_committed = False
        try:
            yield before
        except BaseException as exc:
            if not self._last_write_committed:
                uncertain = self._recovery_required
                try:
                    self._adopt(before)
                except BaseException:
                    self._recovery_required = True
                    if not isinstance(exc, Exception):
                        raise exc
                    raise
                self._recovery_required = uncertain
            raise

    def _commit_operation_locked(self, before: dict) -> bool:
        """Public operations never leave an unconfirmed delta for another writer."""
        try:
            self._save_locked()
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise  # The operation guard restores state; cancellation must propagate.
            if self._last_write_committed:
                return True
            uncertain = self._recovery_required
            self._adopt(before)
            self._recovery_required = uncertain
            if uncertain:
                raise LedgerPersistenceError("ledger commitment uncertain; recovery required") from exc
            if isinstance(exc, _JournalHardLinkError):
                raise
            return False
        return True


# ── Singleton ─────────────────────────────────────────────────────────────────

_ledger_instance: Optional[KRYLedger] = None


def get_ledger() -> KRYLedger:
    global _ledger_instance
    with _LEDGER_LOCK:
        if _ledger_instance is None:
            _ledger_instance = KRYLedger.load_or_create()
        return _ledger_instance


def _durable_snapshot() -> KRYLedger:
    with _LEDGER_LOCK:
        ledger = get_ledger()
        _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _cross_process_lock(_LEDGER_PATH):
            raw = ledger._read_locked()
            snapshot = KRYLedger()
            if raw:
                snapshot._adopt(raw)
            snapshot._journal_fenced = _ledger_storage_path() == _journal_path()
            return snapshot


def acknowledge_ledger_recovery(*, reason: str) -> None:
    """Acknowledge an operator-reviewed restored ledger; never infer lost debits.

    Stop other writers and restore independently established accounting first.
    Retained quarantine and pending-write files are bound by hash, not deleted.
    This acknowledgement is an operator assertion, not verification of history.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a recovery reason is required")
    with _LEDGER_LOCK, _cross_process_lock(_LEDGER_PATH):
        path = _ledger_storage_path()
        raw = _ledger_json_loads(path.read_text(encoding="utf-8"))
        versioned = isinstance(raw, dict) and "ledger_format" in raw
        if path == _journal_path() and not versioned:
            raise ValueError("the journal path requires a format-2 envelope")
        if path == _LEDGER_PATH and versioned:
            raise ValueError("legacy-path operation journal requires reviewed relocation")
        ledger = KRYLedger()
        ledger._adopt(raw)
        if ledger.balance < 0:
            raise ValueError("restored ledger balance is negative; establish consistent accounting first")
        activation = ledger._recovery.get("journal_activation")
        recovery = {"reason": reason, "ts": time.time(), "incidents": _ledger_incidents()}
        if activation is not None:
            recovery["journal_activation"] = activation
        try:
            ledger._save_locked(raw, recovery=recovery)
        except Exception:
            if not ledger._last_write_committed:
                raise
        try:
            ledger._read_locked()
        except Exception as exc:
            raise LedgerPersistenceError("ledger recovery acknowledgement incomplete; inspect retained state") from exc
        global _ledger_instance
        _ledger_instance = ledger


def complete_journal_activation() -> None:
    """Finish an exact retained activation candidate under the existing ledger lock.

    Stop all writers first. This refuses changed accounting or missing/conflicting
    evidence; it cannot reconstruct history or prove an external debit outcome.
    """
    from ._journal_activation import _Activation
    global _ledger_instance
    try:
        _Activation().complete()
    finally:
        _ledger_instance = None


def abandon_incomplete_journal_activation(*, reason: str) -> dict:
    """Retain reviewed legacy accounting and archive a pre-journal activation.

    Stop all writers first. The reason asserts that retained accounting omits no
    committed journal debit. Local absence cannot prove that assertion. Completion
    evidence refuses abandonment; this does not resolve a settlement obligation.
    A repeated call resumes the frozen review or returns its retained receipt.
    """
    from ._journal_activation import _Activation
    global _ledger_instance
    try:
        return _Activation().abandon(reason)
    finally:
        _ledger_instance = None


# ── Edge-weighting: provider-value multiplier (edge-semantics) ────────
#
# Topology alone is a weak prior — the EDGE matters. A cache hit doesn't earn a
# flat credit; it earns proportional to WHAT IT AVOIDED. Avoiding a $25/M Opus
# call is real savings; "avoiding" a $0 free-tier call saved nothing.
#
# value_multiplier(avoided_model) = that model's $/M output ÷ frontier baseline.
#   Opus 4.8 ($25/M)  → 1.00  (full frontier value)
#   Haiku ($1.25/M)   → 0.05
#   DeepSeek ($1.10/M)→ 0.044
#   Free tiers ($0)   → 0.00  (caching a free call saved nothing — honest)

# Per-model $/M output (mirrors the host's model-pricing map; frontier = $25/M)
_MODEL_OUTPUT_USD_PER_M: dict[str, float] = {
    # Current provider model ids at list price. Issuance resolves exact ids or the explicit aliases
    # below; historical class prices remain legal for verifying already issued receipts.
    "claude-fable-5":    50.0,   # Fable 5 and 5.1; the multiplier caps at 1.0 (the $25 frontier)
    "claude-sonnet-5":   10.0,
    "claude-sonnet-4-6": 15.0,
    "claude-sonnet-4-5": 15.0,
    "claude-haiku-4-5":   5.0,
    # Older class entries, unchanged so receipts minted at these prices still verify (SPEC_DEVELOPMENT.md
    # ground rule 1). Only explicit supported aliases use these prices for new issuance.
    "opus":    25.0,   # gh/ or or/ Opus-class
    "sonnet":   7.5,   # Sonnet-class estimate
    "haiku":    1.25,  # Haiku-class
    "gpt-5":   10.0,   # GPT-5.x via Copilot (premium request)
    "gpt-4o-mini": 0.60,  # OpenAI gpt-4o-mini output list price
    "gpt-4o":  10.0,   # OpenAI gpt-4o output list price
    "deepseek-v4-pro": 1.10,
    "deepseek": 0.55,
    "qwen":     1.25,
    "gemini":   0.0,   # AI Studio free quota
}
_FREE_PREFIXES = ("google", "groq", "nim", "local", "pool")
_MODEL_NAMESPACES = frozenset({"gh", "ghm", "or", "openrouter", "anthropic", "openai", "deepseek", "qwen"})
_MODEL_ALIASES = {
    "claude-fable-5-1": "claude-fable-5",
    "claude-opus": "opus", "claude-opus-4": "opus", "claude-opus-4-5": "opus",
    "claude-opus-4-6": "opus", "claude-opus-4-7": "opus", "claude-opus-4-8": "opus",
    "claude-opus-5": "opus",
    # Explicit snapshot identities use the existing 2026-06-03 KRY benchmark,
    # not current provider billing. Identity sources and scope: docs/KRY_LITELLM.md.
    "claude-opus-4-1-20250805": "opus", "claude-opus-4-5-20251101": "opus",
    "gpt-4o-2024-08-06": "gpt-4o", "gpt-4o-2024-11-20": "gpt-4o",
    "gpt-4o-mini-2024-07-18": "gpt-4o-mini",
    "claude-sonnet": "sonnet", "claude-sonnet-4-8": "sonnet",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-5",
    "claude-haiku": "haiku", "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "deepseek-v4-flash": "deepseek", "deepseek-r1": "deepseek", "qwen2-5:14b": "qwen",
    "gemini-2-5-pro": "gemini", "gemini-3-5-flash": "gemini", "gemini-flash": "gemini",
}

# ── Price provenance (F2: dated public-price binding) ─────────────────────────
# Source/quality metadata for each $/M figure above, so magnitude is PUBLICLY-
# CHECKABLE arithmetic: a verifier recomputes kry = tokens × rate × multiplier and
# confirms the multiplier is a legal published value (price ÷ frontier baseline),
# rather than trusting the operator's number. NO price duplication — the $/M
# values live ONLY in _MODEL_OUTPUT_USD_PER_M above (single source of truth; the
# EARN_RATES-drift footgun stays closed). `quality` is honest: "list" = a real
# public list price, "estimate" = our approximation (no single canonical list).
# The newest date any entry was checked; each entry's own date is its `as_of` below.
PRICE_BASIS_AS_OF = "2026-09-15"
_PRICE_SOURCE: dict[str, dict] = {
    "claude-fable-5":    {"quality": "list", "as_of": "2026-09-15",
                          "source": "Anthropic pricing page, Fable 5 and 5.1 output list price ($50/M)"},
    "claude-sonnet-5":   {"quality": "list", "as_of": "2026-09-15",
                          "source": "Anthropic pricing page, Sonnet 5 output list price ($10/M)"},
    "claude-sonnet-4-6": {"quality": "list", "as_of": "2026-09-15",
                          "source": "Anthropic pricing page, Sonnet 4.6 output list price ($15/M)"},
    "claude-sonnet-4-5": {"quality": "list", "as_of": "2026-09-15",
                          "source": "Anthropic pricing page, Sonnet 4.5 output list price ($15/M)"},
    "claude-haiku-4-5":  {"quality": "list", "as_of": "2026-09-15",
                          "source": "Anthropic pricing page, Haiku 4.5 output list price ($5/M)"},
    "opus":            {"quality": "list",     "as_of": "2026-06-03", "source": "Anthropic/OpenRouter Opus-4.x output list price"},
    "sonnet":          {"quality": "estimate", "as_of": "2026-06-03", "source": "Sonnet-class estimate (no single canonical list)"},
    "haiku":           {"quality": "list",     "as_of": "2026-06-03", "source": "Anthropic Haiku output list price"},
    "gpt-5":           {"quality": "estimate", "as_of": "2026-06-03", "source": "GPT-5.x via Copilot premium-request estimate"},
    "gpt-4o-mini":     {"quality": "list",     "as_of": "2026-06-03", "source": "OpenAI gpt-4o-mini output list price ($0.60/M)"},
    "gpt-4o":          {"quality": "list",     "as_of": "2026-06-03", "source": "OpenAI gpt-4o output list price ($10/M)"},
    "deepseek-v4-pro": {"quality": "list",     "as_of": "2026-06-03", "source": "OpenRouter deepseek-v4-pro output list price"},
    "deepseek":        {"quality": "list",     "as_of": "2026-06-03", "source": "OpenRouter deepseek output list price"},
    "qwen":            {"quality": "estimate", "as_of": "2026-06-03", "source": "Qwen-class estimate"},
    "gemini":          {"quality": "list",     "as_of": "2026-06-03", "source": "Google AI Studio free quota ($0)"},
}


def _priced_multiplier(model: str | None) -> float | None:
    """Resolve dated KRY benchmarks; None means unpriced, distinct from a known free route."""
    if model is None:
        return 1.0  # legacy unspecified-model contract
    if not isinstance(model, str):
        return None
    parts = model.lower().replace(".", "-").split("/")
    if any(part not in _MODEL_NAMESPACES and part not in _FREE_PREFIXES for part in parts[:-1]):
        return None
    if any(part in _FREE_PREFIXES for part in parts[:-1]):
        return 0.0  # existing free-route accounting policy, not a provider billing assertion
    key = _MODEL_ALIASES.get(parts[-1], parts[-1])
    price = _MODEL_OUTPUT_USD_PER_M.get(key)
    return min(1.0, price / FRONTIER_USD_PER_M_OUTPUT) if price is not None else None


def value_multiplier(avoided_model: str | None) -> float:
    """Frontier-relative value of avoiding a call to `avoided_model`.

    Returns 0.0 for free tiers (no real spend avoided) and a fraction of 1.0
    for paid models scaled by their output price vs the $25/M frontier baseline.
    Unknown model → 0.0 (unpriced; no credit). None → 1.0 (legacy callers keep
    flat behaviour).
    """
    multiplier = _priced_multiplier(avoided_model)
    return multiplier if multiplier is not None else 0.0


def net_value_multiplier(avoided_model: str | None,
                         served_model: str | None = None) -> float:
    """Value of avoiding `avoided_model`, NET of the paid `served_model` that
    actually ran. A displacement to a FREE tier saves the full avoided price
    (served_model free → multiplier 0 → no netting). A displacement to a cheaper
    PAID provider (e.g. OpenRouter) saves only the price DIFFERENCE — crediting
    the full avoided price would over-count by the server's own cost (Avoid A15).
    Floored at 0: never mint negative value if the 'cheaper' server isn't.
    An unpriced model on either side earns no net credit: unknown served cost is not zero cost."""
    vm = _priced_multiplier(avoided_model)
    if vm is None:
        return 0.0
    if served_model is not None:
        served = _priced_multiplier(served_model)
        if served is None:
            return 0.0
        vm = max(0.0, vm - served)
    return vm


def published_multipliers() -> dict[str, float]:
    """The LEGAL set of value multipliers a verifier checks magnitude against —
    each model's $/M output ÷ the frontier baseline, plus the three structural
    constants. A receipt whose implied multiplier is none of these used a
    non-public price. Derived from the single-source price table (no duplication)."""
    muls = {m: min(1.0, p / FRONTIER_USD_PER_M_OUTPUT)
            for m, p in _MODEL_OUTPUT_USD_PER_M.items()}
    muls["__free__"] = 0.0           # free tier — caching saved nothing
    muls["__unknown_floor__"] = 0.05  # historical unknown-model estimate; no new unpriced issuance
    muls["__legacy_none__"] = 1.0     # avoided_model=None legacy callers
    # Net-saving multipliers: a cheaper-PAID displacement (e.g. OpenRouter serving
    # instead of a frontier) saves only vm(avoided) - vm(served) — the non-negative
    # pairwise DIFFERENCE of two public multipliers, still public arithmetic.
    base = set(muls.values())
    for i, d in enumerate(sorted({round(a - b, 6)
                                  for a in base for b in base if a - b > 0})):
        muls[f"__net_{i}__"] = d
    return muls


def price_provenance() -> dict:
    """Dated, sourced price basis for magnitude — the public reference a verifier
    (or human) uses to confirm each multiplier against the real list price."""
    return {
        "as_of": PRICE_BASIS_AS_OF,
        "frontier_usd_per_m": FRONTIER_USD_PER_M_OUTPUT,
        "usd_per_kry": USD_PER_KRY,
        "models": {m: {"usd_per_m": _MODEL_OUTPUT_USD_PER_M[m], **_PRICE_SOURCE.get(m, {})}
                   for m in _MODEL_OUTPUT_USD_PER_M},
        "multipliers": published_multipliers(),
    }


# ── Earning ───────────────────────────────────────────────────────────────────

def earn(tokens: float, event_type: str, detail: str = "",
         avoided_model: str | None = None, served_model: str | None = None) -> float:
    """Record a KRY earning event. Returns KRY earned.

    Called by: cache hits, L3 matches, short-circuits, compression events.

    avoided_model: the model the efficiency event AVOIDED calling. The credit
    is scaled by that model's frontier-relative cost (edge-weighting) — a cache
    hit that avoided Opus earns ~20x what a Haiku-avoiding hit earns, and a
    free-tier hit earns 0 (it saved nothing). None → flat 1.0 (legacy).
    """
    try:
        tokens = _finite_number(tokens, "tokens", nonnegative=True)
        kry = _finite_number(tokens * EARN_RATES.get(event_type, 0.5) * KRY_PER_FUEL_TOKEN *
                             net_value_multiplier(avoided_model, served_model), "earned KRY")
    except ValueError:
        return 0.0
    if kry <= 0:
        return 0.0
    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_LOCK:
        ledger = get_ledger()
        with _cross_process_lock(_LEDGER_PATH):
            try:
                ledger._refresh_locked()
            except Exception:
                logger.warning("KRY earn: debit history unavailable; no new credit recorded")
                return 0.0
            try:
                balance = _finite_number(ledger.balance + kry, "balance")
                earned = _finite_number(ledger.total_earned + kry, "total_earned", nonnegative=True)
            except ValueError:
                return 0.0
            with ledger._operation_locked() as before:
                ledger.balance, ledger.total_earned = balance, earned
                vmult = net_value_multiplier(avoided_model, served_model)
                ledger.events.append(KRYEvent(
                    ts=time.time(), kind="earn", source=event_type,
                    amount=kry, detail=f"{detail} [avoided={avoided_model},x{vmult:.2f}]"
                           if avoided_model else detail, tx_id=uuid.uuid4().hex))
                if not ledger._commit_operation_locked(before):
                    logger.warning("KRY earn: ledger write failed; no credit recorded")
                    return 0.0
    return kry


# ── Spending ──────────────────────────────────────────────────────────────────

def spend_cost(model: str, output_tokens: int = 1000) -> float:
    """Calculate KRY cost for a routing decision. 0 = free tier.

    Matches the MOST-SPECIFIC (longest) prefix, not the first in dict order. A
    plain `startswith` walk over the insertion-ordered table let a shorter key
    shadow a longer, more-specific one — silently mispricing two real tiers:
      - 'or/anthropic/claude-opus-4.8' shadowed its '-fast' variant (charged
        1000 instead of 2000 — premium tier undercharged 2x),
      - 'gh' shadowed 'ghm/' (charged 500 per call instead of the ghm rate).
    Longest-prefix-wins makes each distinct SPEND_RATES key reachable, matching
    KRY_TOKEN_SPEC.md (opus-fast = 2000/k, ghm = 5/k).
    """
    # Validate the cost input at the boundary, exactly as earn() validates its tokens.
    # Without this a negative output_tokens yields a NEGATIVE cost — and spend() then does
    # `balance -= min(cost, balance)` = balance - (negative) = balance INFLATED; a NaN poisons
    # the balance. spend()/can_afford() both route through here, so this one guard covers them.
    output_tokens = _finite_number(output_tokens, "output_tokens", nonnegative=True)
    for prefix in sorted(SPEND_RATES, key=len, reverse=True):
        if model.startswith(prefix) or model == prefix:
            rate = SPEND_RATES[prefix]
            if prefix in ("google", "groq", "nim", "local", "pool"):
                return 0.0  # free tiers — no KRY spent
            if prefix == "gh":
                return rate  # per-call flat rate (Copilot premium request)
            # Normalize the rate first: a unit-rate debit must preserve its exact
            # amount rather than lose precision in a divide/multiply round trip.
            return _finite_number((rate / 1000) * output_tokens, "spend_cost", nonnegative=True)
    return _finite_number((SPEND_RATES["or/anthropic/claude-opus-4.8"] / 1000) * output_tokens,
                          "spend_cost", nonnegative=True)


def can_afford(model: str, output_tokens: int = 1000) -> bool:
    """Read current durable spending capacity; this is not a reservation."""
    cost = spend_cost(model, output_tokens)
    if cost == 0:
        return True
    try:
        return _durable_snapshot().balance >= cost
    except Exception:
        return False


def _keyed_debit(kind: str, parameters: dict, op_id: str) -> float:
    _validate_op_id(op_id)
    digest = hashlib.sha256(_json_dumps(
        {"kind": kind, **parameters}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_LOCK:
        ledger = get_ledger()
        with _cross_process_lock(_LEDGER_PATH):
            try:
                raw = ledger._read_locked()
            except Exception as exc:
                raise LedgerPersistenceError("operation journal unavailable; recovery required") from exc
            accounting, operations = _ledger_parts(raw)
            if op_id in operations:
                record = operations[op_id]
                if record["kind"] != kind or record["params_sha256"] != digest:
                    raise ValueError("op_id already records different parameters")
                if _ledger_storage_path() != _journal_path():
                    raise LedgerPersistenceError("legacy-path operation journal requires reviewed relocation")
                return record["amount"]
            if accounting.get("balance", 0.0) < 0 or ledger.balance < 0:
                raise ValueError("negative ledger balance requires reviewed recovery")
            # A replay must not reprice or commit unrelated direct-save edits.
            cost = (spend_cost(parameters["model"], parameters["output_tokens"])
                    if kind == "spend" else parameters["amount"])
            try:
                ledger._refresh_locked()
            except Exception as exc:
                raise LedgerPersistenceError("operation journal unavailable; recovery required") from exc
            actual = min(cost, max(0.0, ledger.balance))
            balance = _finite_number(ledger.balance - actual, "balance")
            spent = _finite_number(ledger.total_spent + actual, "total_spent", nonnegative=True)
            if actual > 0 and (balance >= ledger.balance or spent <= ledger.total_spent):
                raise ValueError("debit is not representable in both accounting totals")
            with ledger._operation_locked() as before:
                ledger.balance, ledger.total_spent = balance, spent
                ledger._ledger_format = 2
                ts = _finite_number(time.time(), "operation.ts", nonnegative=True)
                ledger._ops[op_id] = {"kind": kind, "amount": actual,
                                      "params_sha256": digest, "ts": ts}
                if actual > 0:
                    model = parameters["model"] if kind == "spend" else "debit_kry"
                    detail = (parameters["detail"] or f"{parameters['output_tokens']} output tokens"
                              if kind == "spend" else "direct KRY debit")
                    ledger.events.append(KRYEvent(
                        ts=ts, kind="spend", source=model, amount=-actual,
                        detail=detail, tx_id=uuid.uuid4().hex))
                if not ledger._commit_operation_locked(before):
                    raise LedgerPersistenceError("operation was not committed; retry with the same op_id")
    return actual


def ledger_operation(op_id: str) -> dict | None:
    """Read a durable local outcome; None means absent from this v2 snapshot only.

    Legacy or unavailable history raises. Neither presence nor absence proves an
    external debit, or completeness after restoring an older snapshot.
    """
    _validate_op_id(op_id)
    try:
        ledger = _durable_snapshot()
    except Exception as exc:
        raise LedgerPersistenceError("operation journal unavailable; recovery required") from exc
    if ledger._ledger_format != 2:
        raise ValueError("legacy ledger has no operation journal")
    if not ledger._journal_fenced:
        raise ValueError("legacy-path operation journal requires reviewed relocation")
    record = ledger._ops.get(op_id)
    return record.copy() if record is not None else None


def debit_kry(amount: float, *, op_id: str) -> float:
    """Record a bounded KRY debit once per caller ID, returning its durable amount.

    Use a stable, independently generated ID (for example secrets.token_hex(16)) for each logical
    operation. Preserve the entire versioned ledger; operation records never expire.
    This is local accounting, not transactional authorization for an external action.
    """
    amount = _operation_number(amount, "amount")
    return _keyed_debit("debit_kry", {"amount": amount}, op_id)


def spend(model: str, output_tokens: int, detail: str = "", *, op_id: str | None = None) -> float:
    """Return a durable debit; optional caller identity makes recorded retries replayable.

    Keyed calls persist zero outcomes too, and raise on unconfirmed writes. A retry
    must use the same parameters and ID. Unkeyed calls retain their legacy contract.
    """
    if op_id is not None:
        if not isinstance(model, str) or not isinstance(detail, str):
            raise ValueError("model and detail must be strings")
        tokens = _operation_number(output_tokens, "output_tokens")
        return _keyed_debit("spend", {"model": model, "output_tokens": tokens,
                                      "detail": detail}, op_id)
    cost = spend_cost(model, output_tokens)
    if cost == 0:
        return 0.0
    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_LOCK:
        ledger = get_ledger()
        with _cross_process_lock(_LEDGER_PATH):
            try:
                ledger._refresh_locked()
            except Exception:
                logger.warning("KRY spend: debit history unavailable; no new debit recorded")
                return 0.0
            actual = min(cost, max(0.0, ledger.balance))
            if actual == 0:
                return 0.0
            try:
                spent = _finite_number(ledger.total_spent + actual, "total_spent", nonnegative=True)
            except ValueError:
                return 0.0
            with ledger._operation_locked() as before:
                ledger.balance -= actual
                ledger.total_spent = spent
                ledger.events.append(KRYEvent(
                    ts=time.time(), kind="spend", source=model,
                    amount=-actual, detail=detail or f"{output_tokens} output tokens",
                    tx_id=uuid.uuid4().hex))
                if not ledger._commit_operation_locked(before):
                    logger.warning("KRY spend: ledger write failed; no debit recorded")
                    return 0.0
    return actual


# ── Cycle verification (falsifier) ────────────────────────────────

@dataclass
class CycleVerification:
    """Verify one earn→bank→spend cycle is mathematically consistent."""
    earned: float
    spent: float
    balance_before: float
    balance_after: float
    consistent: bool
    delta_error: float


def verify_cycle(
    earned: float, spent: float,
    balance_before: float, balance_after: float,
) -> CycleVerification:
    """The minimal viable falsifier: earn - spend = Δbalance."""
    expected_delta = earned - spent
    actual_delta = balance_after - balance_before
    error = abs(expected_delta - actual_delta)
    consistent = error < 0.001  # floating-point tolerance
    return CycleVerification(
        earned=earned, spent=spent,
        balance_before=balance_before, balance_after=balance_after,
        consistent=consistent, delta_error=error,
    )


# ── Retained dollars (value vs scoreboard, 2026-06-03) ────────
#
# R2 falsifier: "name one EXTERNAL party who'd give something real for KRY."
# Today: none → KRY-as-tradeable-token is still a SCOREBOARD (the original
# IV-bag finding, one layer up). BUT the scoreboard critique misses a real,
# NON-circular value: KRY is denominated against REAL provider pricing, so it
# measures USD COST AVOIDED = money the operator KEPT. That value exists now,
# without any external counterparty. retained_dollars() makes it auditable as
# DOLLARS — the honest "value today" (money kept), not a tradeable instrument.

def supply() -> dict:
    """R3 (unbounded-supply / inflation): distinguish CIRCULATING from LIFETIME.

    The inflation worry conflates two figures:
      lifetime_earned — monotone cumulative savings; correct that it grows with
        usage (it's the retained-dollars measure, not a fixed-supply asset).
      circulating      — balance = earned − spent. Spend is the SINK. This is
        the figure to use for any 'token supply' discussion; it is NOT unbounded
        (bounded by unspent earnings), so there is no inflation of circulating KRY.
    Reporting lifetime_earned as 'supply' was the inflation illusion.
    """
    led = _durable_snapshot()
    return {
        "circulating_kry": round(led.balance, 2),       # earned − spent (the real supply)
        "lifetime_earned_kry": round(led.total_earned, 2),  # cumulative savings (retained-$)
        "total_spent_kry": round(led.total_spent, 2),   # the sink
        "note": "use circulating for supply; lifetime_earned is retained-dollars, not supply",
    }


def retained_dollars() -> dict:
    """The honest value KRY represents TODAY: real dollars the operator kept by
    routing efficiently, denominated against live provider pricing. NOT a
    tradeable-token claim — a measurement of spend avoided.

    total_earned KRY × $0.000025/KRY = USD of frontier-equivalent compute that
    was avoided. Edge-weighting already scaled each earn by the real avoided
    model's price, so this maps to genuine retained dollars, not phantom credit.
    """
    ledger = _durable_snapshot()
    retained = ledger.total_earned * USD_PER_KRY
    return {
        "retained_usd": round(retained, 4),
        "total_earned_kry": round(ledger.total_earned, 2),
        "basis": "edge-weighted KRY × $0.000025 (frontier-equiv USD/token)",
        "value_type": "retained_dollars (money kept) — NOT a tradeable token",
        "external_counterparty_exists": False,  # honest: scoreboard until a B accepts it
        "note": "R2: value today = spend avoided, provable against provider pricing",
    }


# ── Non-additivity invariant (2026-06-03) ─────────────────
#
# A cache hit credits BOTH the FuelLedger (biological reserve view) AND the KRY
# ledger (token economy view). A Minimal Viable Falsifier confirmed: summing
# fuel_ledger.available + kry_balance double-counts the same avoided call at
# 2.00x. These are TWO VIEWS OF ONE EVENT — never additive.
#
#   FuelLedger.available  = internal reserve representation (4 compartments)
#   KRY balance           = external token representation (market unit)
#
# They track the same underlying efficiency events. The canonical "how much did
# we save" figure is reconciled_savings() below — NOT a sum.

def reconciled_savings() -> dict:
    """Canonical savings figure — reconciles the two ledgers without double-count.

    Returns the KRY-view and fuel-view side by side plus the reconciled total
    (they should track closely; large drift flags a wiring bug). Never sums them.
    """
    kry_earned = _durable_snapshot().total_earned
    # The FuelLedger (biological reserve) view is host-integration-only; in the
    # standalone token it is not wired, so the fuel view reports 0.
    fuel_deposited = 0.0
    # The two views measure the same events; the reconciled figure is the larger
    # (most complete) view, NOT the sum. Drift = wiring divergence to investigate.
    reconciled = max(kry_earned, fuel_deposited)
    drift = abs(kry_earned - fuel_deposited)
    return {
        "kry_view_earned": round(kry_earned, 2),
        "fuel_view_deposited": round(fuel_deposited, 2),
        "reconciled_total_saved": round(reconciled, 2),
        "view_drift": round(drift, 2),
        "additive_misuse_would_show": round(kry_earned + fuel_deposited, 2),
        "note": "KRY and FuelLedger are two views of ONE event set — never sum them",
    }


# ── Status ────────────────────────────────────────────────────────────────────

def status() -> dict:
    try:
        ledger = _durable_snapshot()
    except (OSError, ValueError, LedgerPersistenceError):
        return {"available": False, "balance_kry": None, "total_earned_kry": None,
                "total_spent_kry": None, "reason": "ledger unavailable; inspect retained state"}
    return {
        "available": True,
        "balance_kry": round(ledger.balance, 2),
        "total_earned_kry": round(ledger.total_earned, 2),
        "total_spent_kry": round(ledger.total_spent, 2),
        "usd_equivalent_saved": round(ledger.usd_equivalent_saved, 4),
        "efficiency_ratio": round(ledger.efficiency_ratio, 4),
        "cycle_count": ledger.cycle_count,
        "frontier_baseline": f"${FRONTIER_USD_PER_M_OUTPUT}/M output tokens",
        "kry_per_usd": round(1 / USD_PER_KRY),
        "reconciliation": "see reconciled_savings() — KRY & FuelLedger never summed",
    }
