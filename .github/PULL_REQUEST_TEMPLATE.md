## Description
<!-- What this PR does and WHY. -->

## Related issue
Closes #
<!-- If there's no issue, explain why this change is needed. -->

## Type of change
- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change
- [ ] Documentation
- [ ] Refactor (no behavior change)
- [ ] Build / CI

## Checklist — the bar (see [CONTRIBUTING.md](../CONTRIBUTING.md))
- [ ] `PYTHONPATH=src python -m pytest tests/ -q` passes
- [ ] `ruff check src/ scripts/ tests/ examples/ lab/` is clean
- [ ] **Stdlib only** — no new third-party dependency in `src/` or `scripts/`
- [ ] **No AI attribution** anywhere (commits, docs, comments) — enforced by CI
- [ ] If capabilities changed: `verify_capabilities()` is clean; nothing marked `implemented` lacks code **and** tests
- [ ] Claims are labeled **measured vs. speculative**; no hidden `veracity_floor`
- [ ] No runtime data committed (`KRY_DATA_DIR` / `kry_data` stays gitignored)

## Breaking changes / migration
<!-- If breaking: what breaks and how should users update? -->

## Notes for reviewers
<!-- Anything to pay special attention to. -->
