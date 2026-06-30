# Verifiable LLM FinOps: why your savings dashboard isn't proof

Your team cut the LLM bill. You cached aggressively, routed the easy calls to a cheaper model, trimmed
the prompts. The dashboard says you saved $40,000 last quarter. Then finance asks you to *prove* it — or
a customer wants the savings reflected in their invoice, or a vendor's claim needs substantiating. You
point at the dashboard.

Here's the problem: that number is a **claim, not proof**. The dashboard reads from a ledger you control.
Nothing stops the figure from being optimistic, double-counted, or priced against a model you'd never
actually have called. Whoever you're showing it to has to *trust you* — and in 2026, "trust me" is
wearing thin. Companies are increasingly expected to substantiate AI-efficiency claims rather than assert
them, and teams are being asked to self-fund AI out of the very savings they're claiming. So the natural
question: what would it take to make an LLM-savings number you could hand to someone who *doesn't* trust
you?

It helps to notice that most cost tooling blurs three different questions into one green number. Pulling
them apart is the whole game.

1. **Integrity — was the ledger edited after the fact?** Solvable. Hash-chain every entry; any edit,
   reorder, insertion, or deletion breaks the chain, and a stranger can recompute it to catch tampering
   without trusting you.

2. **Magnitude — does the dollar figure actually follow from the events and public prices?** Also
   solvable. Recompute every figure from the published price tables. This catches an inflated number
   *even when the ledger is internally consistent* — you can't quietly multiply by a model you didn't use.

3. **Veracity — did the saving actually happen?** This one a hash chain **cannot** prove, and pretending
   otherwise is the central mistake. A cache hit is a call that never reached a provider. By construction,
   nothing outside your own runtime witnessed the counterfactual — the expensive thing you *didn't* do. No
   amount of cryptography conjures an external witness for an event that left no external trace.

That's the line worth keeping: **a hash chain can prove a log wasn't edited; it cannot prove the thing it
records happened.** "Untampered" and "true" are different properties, and a checkmark that conflates them
is quietly claiming more than it can know.

The honest move isn't to fake the third property — it's to separate the three and make the leftover trust
*explicit*. Prove integrity and magnitude to a stranger offline. Then, for veracity, don't bury the gap
under a green tick: put a number on it. What fraction of this balance is backed by something stronger than
your own word — a provider's metered record, a hardware attestation, a notarized response — versus pure
self-report? Print that fraction on the receipt. Now the reader knows *exactly* how much trust they're
still extending, instead of being asked to extend all of it, unlabeled.

A cache hit will score low on that scale — there's no external witness, and saying so plainly is more
credible than faking certainty. A provider-metered displacement scores higher. The goal was never to claim
everything is proven; it's to stop pretending, and to make the unproven part legible.

This is a different thing from cost **observability**. Helicone, Langfuse, LiteLLM and the rest track what
you spent, and they're very good at it — but they read from the same trusted-operator vantage point a
dashboard does. *Verifiable* FinOps is the layer on top: turning the savings number into something a third
party who doesn't trust the operator can re-check offline, with the trust gap labeled rather than hidden.
As scrutiny of AI claims tightens, that layer stops being a nicety.

None of this is finished. The hard, unglamorous part — binding a savings claim to a real external
counterparty, so the veracity number reflects independent reality and not just an honest label — is exactly
the open problem, and it's where the work goes next.

But the framing is the part worth getting right now: **integrity is not veracity, magnitude is neither, and
the honest version of "we saved $X" is the one that tells you which of the three it can actually back.**

The question worth answering: if you run a non-trivial LLM bill, is "savings I can prove to someone who
doesn't trust me" something you'd ever actually need — for finance, a customer, a vendor claim — or is
internal trust enough in practice? That answer decides whether any of this matters.

---

*KRY is one open-source, stdlib-only take on this: a tamper-evident receipt a stranger can recompute
offline, with the trust gap exposed as an explicit `veracity_floor`. Research-grade, Apache-2.0 —
<https://github.com/thequantumfalcon/kry>.*
