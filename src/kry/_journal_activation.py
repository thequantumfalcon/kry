"""Durable journal activation and explicit operator-reviewed recovery.

The caller must stop other writer installations before recovery. Local hashes and
completion records cannot establish completeness after an arbitrary history rollback.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid

from . import kry_token as kt


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def read(path):
    """Only a missing entry is absent; do not follow evidence symlinks."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise ValueError(f"non-regular activation evidence: {path.name}")
    return path.read_bytes()


def parsed(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate activation evidence member")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=kt._reject_json_constant)


def validated_ledger(data, version):
    if data is None:
        raise ValueError("reviewed ledger is missing")
    raw = parsed(data)
    ledger = kt.KRYLedger()
    ledger._adopt(raw)
    if ledger._ledger_format != version or ledger.balance < 0:
        raise ValueError("wrong ledger format or negative balance")
    return raw


class _Activation:
    """Private persistence protocol using the existing legacy-file coordination lock."""

    def __init__(self):
        self.legacy = kt._LEDGER_PATH
        self.root = self.legacy.parent
        self.journal = kt._journal_path()
        self.backup = kt._legacy_backup_path()
        self.sentinel = self.root / (self.legacy.name + ".v2-activated")

    def files(self, suffix):
        return sorted(self.root.glob(self.legacy.name + suffix))

    def sync(self):
        kt._sync_ledger_directory()

    def publish(self, path, data, *, replace=False):
        """Immutable publication uses link, so conflicting evidence is not replaced."""
        existing = read(path)
        if not replace and existing is not None:
            if existing != data:
                raise ValueError(f"conflicting evidence: {path.name}")
            self.sync()
            return
        fd, name = tempfile.mkstemp(prefix=".kry_activation_", dir=self.root)
        temp = Path(name)
        with os.fdopen(fd, "wb", buffering=0) as stream:
            stream.write(data)
            os.fsync(stream.fileno())
        if read(temp) != data:
            raise ValueError("temporary bytes differ from intended publication")
        if replace:
            os.replace(temp, path)
        else:
            os.link(temp, path)
        self.sync()
        if not replace:
            temp.unlink()

    def remove(self, path):
        path.unlink()
        self.sync()

    def incidents(self):
        paths = self.files(".pending.*") + self.files(".corrupt*")
        paths += sorted(self.root.glob(self.journal.name + ".pending.*"))
        paths += sorted(self.root.glob(self.journal.name + ".corrupt*"))
        return {p.name: digest(read(p)) for p in paths}

    def check_fence(self):
        """Old journals remain readable; new activation metadata requires its sentinel."""
        read(self.legacy)
        sentinel = read(self.sentinel)
        journal = read(self.journal)
        try:
            raw = parsed(journal)
        except (ValueError, UnicodeError):
            return  # The ledger reader retains its existing invalid-JSON quarantine path.
        if (not isinstance(raw, dict) or raw.get("ledger_format") != 2 or
                not isinstance(raw.get("accounting"), dict) or
                not isinstance(raw["accounting"].get("recovery", {}), dict)):
            return  # The ordinary envelope validation rejects these structures.
        identity = raw["accounting"].get("recovery", {}).get("journal_activation")
        if identity is None and sentinel is None:
            return  # Journal created before the activation-specific protocol.
        if (not isinstance(identity, dict) or set(identity) != {"schema", "attempt_id"} or
                identity["schema"] != "activation/1" or sentinel is None):
            raise ValueError("journal activation completion evidence missing or malformed")
        completion = parsed(sentinel)
        self.check_sentinel(completion)
        if completion["attempt_id"] != identity["attempt_id"]:
            raise ValueError("journal activation completion identity mismatch")

    def activate(self, ledger, candidate):
        """The caller holds the legacy lock and has already computed the merged debit."""
        original = read(self.legacy)
        if original is not None:
            validated_ledger(original, 1)
        attempt = uuid.uuid4().hex
        accounting = candidate["accounting"]
        accounting["recovery"] = {**accounting.get("recovery", {}), "journal_activation": {
            "schema": "activation/1", "attempt_id": attempt}}
        data = kt._json_dumps(candidate, indent=2).encode()
        validated_ledger(data, 2)
        intent = {"schema": "activation/1", "activation": True, "attempt_id": attempt,
                  "legacy_sha256": digest(original), "journal_sha256": digest(data)}
        path = self.root / (self.legacy.name + ".pending.activation-" + attempt)
        durable = False
        try:
            self.publish(path, encoded(intent))
            ledger._recovery_required = True
            if original is not None:
                self.publish(self.backup, original)
            self.publish(self.journal, data)
            self.publish(self.legacy, kt._ledger_tombstone(), replace=True)
            self.publish(self.sentinel, encoded(self.completion(intent)))
            durable = True
            ledger._adopt(candidate)
            ledger._last_write_committed = True
            self.remove(path)
        except BaseException as exc:
            ledger._recovery_required = True
            try:
                if durable and not ledger._last_write_committed:
                    ledger._adopt(candidate)
                    ledger._last_write_committed = True
                ledger._recovery_required = read(path) is not None or (
                    not durable and read(self.journal) is not None)
            except BaseException:
                ledger._recovery_required = True
                if not isinstance(exc, Exception):
                    raise exc
                raise
            raise

    def intent(self, path):
        value = parsed(read(path))
        self.check_intent(value)
        if path.name != self.legacy.name + ".pending.activation-" + value["attempt_id"]:
            raise ValueError("activation filename differs from identity")
        return value

    @staticmethod
    def check_intent(value):
        if (not isinstance(value, dict)
                or set(value) != {"schema", "activation", "attempt_id", "legacy_sha256", "journal_sha256"}
                or value["schema"] != "activation/1" or value["activation"] is not True
                or not isinstance(value["attempt_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", value["attempt_id"])):
            raise ValueError("malformed activation intent")
        for key in ("legacy_sha256", "journal_sha256"):
            if key == "legacy_sha256" and value[key] is None:
                continue
            if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
                raise ValueError("malformed activation digest")

    @staticmethod
    def completion(intent):
        return {"schema": "activation-complete/1", "attempt_id": intent["attempt_id"],
                "initial_journal_sha256": intent["journal_sha256"]}

    @staticmethod
    def check_sentinel(value):
        if (not isinstance(value, dict) or set(value) != {"schema", "attempt_id", "initial_journal_sha256"}
                or value["schema"] != "activation-complete/1"
                or not isinstance(value["attempt_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", value["attempt_id"])
                or not isinstance(value["initial_journal_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["initial_journal_sha256"])):
            raise ValueError("malformed completion sentinel")

    def one_intent(self):
        paths = self.files(".pending.activation-*")
        if len(paths) != 1:
            raise ValueError("exactly one active activation intent required")
        return paths[0], self.intent(paths[0])

    def complete(self):
        """Finish only the exact published candidate; never erase changed legacy state."""
        with kt._LEDGER_LOCK, kt._cross_process_lock(self.legacy):
            if self.files(".pending.abandon-*"):
                raise ValueError("abandonment is active")
            path, intent = self.one_intent()
            journal = read(self.journal)
            if journal is None or digest(journal) != intent["journal_sha256"]:
                raise ValueError("exact attempted journal required")
            raw = validated_ledger(journal, 2)
            identity = raw["accounting"].get("recovery", {}).get("journal_activation")
            if identity != {"schema": "activation/1", "attempt_id": intent["attempt_id"]}:
                raise ValueError("candidate journal activation identity differs from the intent")
            remaining = self.incidents()
            remaining.pop(path.name)
            if raw["accounting"].get("recovery", {}).get("incidents", {}) != remaining:
                raise ValueError("unexpected incidents require review")
            legacy = read(self.legacy)
            if legacy != kt._ledger_tombstone() and digest(legacy) != intent["legacy_sha256"]:
                raise ValueError("legacy changed after activation attempt")
            if digest(read(self.backup)) != intent["legacy_sha256"]:
                raise ValueError("backup differs from attempted source")
            expected = encoded(self.completion(intent))
            if read(self.sentinel) not in (None, expected):
                raise ValueError("conflicting completion sentinel")
            self.publish(self.legacy, kt._ledger_tombstone(), replace=True)
            self.publish(self.sentinel, expected)
            self.remove(path)

    def refuse_completion(self):
        if (read(self.journal) is not None or read(self.sentinel) is not None
                or read(self.legacy) == kt._ledger_tombstone()
                or list(self.root.glob(self.journal.name + ".pending.*"))
                or list(self.root.glob(self.journal.name + ".corrupt*"))):
            raise ValueError("completion evidence excludes abandonment")

    def archive(self, source, dest, expected):
        """Publish a full copy before removing the original; resume either state."""
        current, saved = read(source), read(dest)
        if current is None:
            if digest(saved) != expected or saved is None:
                raise ValueError("missing or changed recovery evidence")
            self.sync()
            return
        if digest(current) != expected:
            raise ValueError("recovery evidence changed")
        self.publish(dest, current)
        self.remove(source)

    def abandon(self, reason):
        """Operator assertion only. Preserve reviewed accounting, never release obligations."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("operator reason required")
        with kt._LEDGER_LOCK, kt._cross_process_lock(self.legacy):
            self.refuse_completion()
            plans = self.files(".pending.abandon-*")
            if len(plans) > 1:
                raise ValueError("conflicting abandonment plans")
            if plans:
                plan_path = plans[0]
                plan = parsed(read(plan_path))
            else:
                active = self.files(".pending.activation-*")
                if not active:
                    raw = validated_ledger(read(self.legacy), 1)
                    if raw.get("recovery", {}).get("incidents") != self.incidents():
                        raise ValueError("unbound recovery evidence")
                    receipt = raw.get("recovery", {}).get("activation_resolution")
                    if not receipt:
                        raise ValueError("no active attempt or prior resolution")
                    return receipt
                intent_path, intent = self.one_intent()
                current = read(self.legacy)
                if current is not None:
                    validated_ledger(current, 1)
                elif intent["legacy_sha256"] is not None:
                    raise ValueError("missing legacy accounting requires restoration")
                backup = read(self.backup)
                if backup is not None and digest(backup) != intent["legacy_sha256"]:
                    raise ValueError("backup differs from attempted source")
                plan = {"schema": "activation-abandon/1", "basis": "operator_asserted",
                        "reason": reason, "ts": time.time(), "intent": intent,
                        "intent_text": read(intent_path).decode(),
                        "legacy_text": None if current is None else current.decode(),
                        "observed_legacy_sha256": digest(current), "backup_sha256": digest(backup),
                        "incidents": self.incidents()}
                plan_path = self.root / (self.legacy.name + ".pending.abandon-" + intent["attempt_id"])
                self.publish(plan_path, encoded(plan))
            return self.resume_abandon(plan_path, plan)

    def resume_abandon(self, plan_path, plan):
        """Assumes one uninterrupted legacy lock; no nested public acknowledgement."""
        fields = {"schema", "basis", "reason", "ts", "intent", "intent_text", "legacy_text",
                  "observed_legacy_sha256", "backup_sha256", "incidents"}
        if (not isinstance(plan, dict) or set(plan) != fields or plan["schema"] != "activation-abandon/1"
                or plan["basis"] != "operator_asserted" or not isinstance(plan["reason"], str)
                or not plan["reason"].strip() or not isinstance(plan["intent_text"], str)
                or plan["legacy_text"] is not None and not isinstance(plan["legacy_text"], str)
                or not isinstance(plan["incidents"], dict)):
            raise ValueError("malformed abandonment plan")
        kt._finite_number(plan["ts"], "assertion.ts", nonnegative=True)
        intent = plan["intent"]
        self.check_intent(intent)
        if plan["backup_sha256"] not in (None, intent["legacy_sha256"]):
            raise ValueError("backup binding differs from attempted source")
        for name, sha in plan["incidents"].items():
            if (not isinstance(name, str) or Path(name).name != name
                    or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
                raise ValueError("malformed incident binding")
        attempt = intent["attempt_id"]
        intent_path = self.root / (self.legacy.name + ".pending.activation-" + attempt)
        if (plan_path.name != self.legacy.name + ".pending.abandon-" + attempt
                or parsed(plan["intent_text"].encode()) != intent):
            raise ValueError("plan identity mismatch")
        prefix = self.legacy.name + ".corrupt.activation." + attempt + "."
        def archive(kind):
            return self.root / (prefix + kind)
        original = None if plan["legacy_text"] is None else plan["legacy_text"].encode()
        if digest(original) != plan["observed_legacy_sha256"]:
            raise ValueError("observed accounting digest mismatch")
        if original is None and intent["legacy_sha256"] is not None:
            raise ValueError("previously existing accounting cannot become original absence")
        raw = (validated_ledger(original, 1) if original is not None else
               {"balance": 0.0, "total_earned": 0.0, "total_spent": 0.0, "events": [], "cycle_count": 0})
        if any(p != intent_path for p in self.files(".pending.activation-*")):
            raise ValueError("another activation attempt exists")
        # Derive the exact final incident set before changing any source or accounting.
        expected = dict(plan["incidents"])
        if expected.pop(intent_path.name, None) != digest(plan["intent_text"].encode()):
            raise ValueError("plan does not bind activation intent")
        expected[archive("intent").name] = digest(plan["intent_text"].encode())
        expected[archive("resolution").name] = digest(encoded(plan))
        if original is not None:
            expected[archive("legacy").name] = digest(original)
        if plan["backup_sha256"] is not None:
            expected[archive("backup").name] = plan["backup_sha256"]
        receipt = {"attempt_id": attempt, "basis": "operator_asserted", "reason": plan["reason"],
                   "original_legacy_sha256": intent["legacy_sha256"],
                   "observed_legacy_sha256": plan["observed_legacy_sha256"],
                   "legacy_changed": intent["legacy_sha256"] != plan["observed_legacy_sha256"],
                   "resolution_sha256": digest(encoded(plan))}
        final = dict(raw)
        final["recovery"] = {"reason": plan["reason"], "ts": plan["ts"],
                             "incidents": expected, "activation_resolution": receipt}
        final_bytes = encoded(final)
        if read(self.legacy) not in (original, final_bytes):
            raise ValueError("legacy changed after operator assertion; review again")
        actual = self.incidents()
        allowed = {**plan["incidents"], **expected, plan_path.name: digest(encoded(plan))}
        if any(allowed.get(name) != sha for name, sha in actual.items()):
            raise ValueError("incidents changed after operator assertion")
        for name, sha in plan["incidents"].items():
            if name != intent_path.name and actual.get(name) != sha:
                raise ValueError("prior incident missing or changed")
        if original is not None:
            self.publish(archive("legacy"), original)
        self.archive(intent_path, archive("intent"), digest(plan["intent_text"].encode()))
        if plan["backup_sha256"] is not None:
            self.archive(self.backup, archive("backup"), plan["backup_sha256"])
        elif read(self.backup) is not None:
            raise ValueError("unexpected backup after operator assertion")
        self.publish(archive("resolution"), encoded(plan))
        actual = self.incidents()
        actual.pop(plan_path.name)
        if actual != expected:
            raise ValueError("incomplete retained recovery evidence")
        self.publish(self.legacy, final_bytes, replace=True)
        self.remove(plan_path)
        return receipt
