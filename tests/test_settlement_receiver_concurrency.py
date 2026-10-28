"""Receiver credits must include every completed concurrent settlement."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
import pickle
from queue import Queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from kry import kry_settlement as ks


@dataclass(slots=True)
class SlottedReceiver(ks.ReceiverLedger):
    label: str = "test receiver"


class ReceiverConcurrencyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="kry-receiver-test-")
        self.addCleanup(directory.cleanup)
        for name, value in (
            ("_REGISTRY_PATH", Path(directory.name) / "registry.jsonl"),
            ("_PENDING_RESERVATIONS", {}),
        ):
            replacement = patch.object(ks, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    @staticmethod
    def offer_and_grant(payer, receiver, sequence):
        offer = ks.make_offer(payer, receiver.party, 10.0, 10, now=float(sequence))
        grant = ks.RoutingGrant(
            grant_id=f"grant-{sequence}", offer_id=offer.offer_id,
            granted_by=receiver.party, routing_tokens=10, accepted_kry=10.0,
            ts=float(sequence), attested_balance=100.0,
        )
        return offer, grant

    def test_shared_receiver_preserves_both_payer_credits(self):
        self._overlap(shared_receiver=True)

    def test_other_receiver_can_finish_during_post_debit_read(self):
        self._overlap(shared_receiver=False)

    def _overlap(self, *, shared_receiver):
        first_receiver = ks.ReceiverLedger("receiver-one")
        second_receiver = (first_receiver if shared_receiver
                           else ks.ReceiverLedger("receiver-two"))
        receivers = [first_receiver, second_receiver]
        pairs = [self.offer_and_grant(payer, receiver, i + 1)
                 for i, (payer, receiver) in enumerate(zip(("alice", "charlie"), receivers))]
        balances = {"alice": 100.0, "charlie": 100.0}
        balance_lock = threading.Lock()
        first_read_started = threading.Event()
        second_lock_attempted = threading.Event()
        second_debited = threading.Event()
        release_first = threading.Event()
        receiver_lock = getattr(first_receiver, "_settlement_lock", None)
        if shared_receiver and receiver_lock is not None:
            class ObservedLock:
                def __enter__(self):
                    if first_read_started.is_set():
                        second_lock_attempted.set()
                    receiver_lock.acquire()

                def __exit__(self, *args):
                    receiver_lock.release()

            first_receiver._settlement_lock = ObservedLock()

        def debit(payer, amount):
            with balance_lock:
                balances[payer] -= amount
            if payer == "charlie":
                second_debited.set()
            return amount

        def balance_after(payer):
            if payer == "alice":
                first_read_started.set()
                if not release_first.wait(5):
                    raise TimeoutError("first post-debit read was not released")
            with balance_lock:
                return balances[payer]

        def run(index, payer):
            return ks.settle(
                *pairs[index], debit_a_fn=lambda amount: debit(payer, amount),
                receiver=receivers[index], a_balance_before=100.0,
                a_balance_after_fn=lambda: balance_after(payer),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, 0, "alice")
            try:
                self.assertTrue(first_read_started.wait(5))
                second = pool.submit(run, 1, "charlie")
                if shared_receiver and receiver_lock is not None:
                    self.assertTrue(second_lock_attempted.wait(5))
                    self.assertFalse(second_debited.is_set(),
                                     "a shared receiver allowed a second debit before its first credit")
                else:
                    # Another receiver need not wait. On the pre-fix implementation,
                    # this also forces the lost-credit interleaving on a shared receiver.
                    second.result(timeout=5)
            finally:
                release_first.set()
            receipts = [first.result(timeout=5), second.result(timeout=5)]

        self.assertEqual(balances, {"alice": 90.0, "charlie": 90.0})
        self.assertTrue(all(receipt.conserved for receipt in receipts))
        self.assertTrue(all(receipt.conservation_basis == "measured" for receipt in receipts))
        self.assertTrue(ks.verify_registry()[0])
        if shared_receiver:
            self.assertEqual(first_receiver.received_kry, 20.0)
            self.assertEqual(first_receiver.routing_sold, 20)
            self.assertEqual(first_receiver.settlements, [r.receipt_id for r in receipts])
            self.assertEqual([(r.b_received_before, r.b_received_after) for r in receipts],
                             [(0.0, 10.0), (10.0, 20.0)])
        else:
            self.assertEqual([r.received_kry for r in receivers], [10.0, 10.0])
            self.assertEqual([r.routing_sold for r in receivers], [10, 10])

    def test_callback_failures_release_receiver_for_another_payer(self):
        for stage in ("debit", "balance"):
            for failure_type in (ValueError, KeyboardInterrupt):
                with self.subTest(stage=stage, failure=failure_type.__name__):
                    # Separate payer names and grant IDs keep every retained obligation distinct.
                    sequence = len(ks._registry_entries()) + 1
                    receiver = ks.ReceiverLedger(f"receiver-{sequence}")
                    failed = self.offer_and_grant(f"failed-{sequence}", receiver, sequence)

                    def fail(*args):
                        raise failure_type("callback interrupted")

                    with self.assertRaises(failure_type):
                        ks.settle(
                            *failed, receiver=receiver, a_balance_before=100.0,
                            debit_a_fn=fail if stage == "debit" else lambda amount: amount,
                            a_balance_after_fn=fail if stage == "balance" else lambda: 90.0,
                        )
                    self.assertEqual(receiver.received_kry, 0.0)
                    self.assertEqual(receiver.settlements, [])
                    succeeding = self.offer_and_grant(f"next-{sequence}", receiver, sequence + 1)
                    outcomes = Queue()

                    def next_settlement():
                        try:
                            outcomes.put(ks.settle(
                                *succeeding, receiver=receiver, a_balance_before=100.0,
                                debit_a_fn=lambda amount: amount, a_balance_after_fn=lambda: 90.0,
                            ))
                        except BaseException as exc:
                            outcomes.put(exc)

                    worker = threading.Thread(target=next_settlement, daemon=True)
                    worker.start()
                    outcome = outcomes.get(timeout=5)
                    worker.join(timeout=5)
                    self.assertIsInstance(outcome, ks.SettlementReceipt)
                    self.assertEqual(receiver.received_kry, 10.0)
                    self.assertEqual(receiver.routing_sold, 10)
                    self.assertTrue(ks.verify_registry()[0])

    def test_receiver_accounting_fields_remain_serializable(self):
        receiver = ks.ReceiverLedger("receiver", received_kry=12.0, routing_sold=3,
                                     settlements=["receipt-1"])
        self.assertEqual(asdict(receiver), {
            "party": "receiver", "received_kry": 12.0, "routing_sold": 3,
            "settlements": ["receipt-1"],
        })
        self.assertEqual(receiver, ks.ReceiverLedger(**asdict(receiver)))
        self.assertNotIn("_settlement_lock", repr(receiver))
        for restored in (deepcopy(receiver), pickle.loads(pickle.dumps(receiver))):
            self.assertEqual(restored, receiver)
            restored.settlements.append("receipt-2")
            self.assertEqual(receiver.settlements, ["receipt-1"])


    def test_slotted_receiver_preserves_fields_and_independent_lock(self):
        receiver = SlottedReceiver("receiver", 12.0, 3, ["receipt-1"], "retained")
        for restored in (copy(receiver), deepcopy(receiver),
                         pickle.loads(pickle.dumps(receiver))):
            with self.subTest(method=type(restored).__name__):
                self.assertEqual(asdict(restored), asdict(receiver))
                self.assertIsNot(restored._settlement_lock, receiver._settlement_lock)
                self.assertTrue(restored._settlement_lock.acquire(blocking=False))
                restored._settlement_lock.release()

    def test_slotted_receiver_loads_pre_lock_state(self):
        receiver = SlottedReceiver("receiver", 12.0, 3, ["receipt-1"], "retained")
        # Default object pickle state used before ReceiverLedger had a lock.
        state = (None, asdict(receiver))
        restored = SlottedReceiver.__new__(SlottedReceiver)
        restored.__setstate__(state)
        self.assertEqual(asdict(restored), asdict(receiver))
        self.assertTrue(restored._settlement_lock.acquire(blocking=False))
        restored._settlement_lock.release()


if __name__ == "__main__":
    unittest.main()
