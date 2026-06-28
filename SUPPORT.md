# Support

Thanks for using KRY. Here is where to go for each kind of help.

| I want to… | Go to |
|---|---|
| Ask a question / discuss an idea | **GitHub Discussions** (if enabled) |
| Report a bug | An **Issue** using the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml) |
| Request a feature | An **Issue** using the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml) |
| Report a security vulnerability | **Do not** open a public issue — follow [SECURITY.md](SECURITY.md) |
| Ask about commercial use / licensing | KRY is **Apache-2.0** — commercial use is free; for support or partnership, email **thequantumfalcon@gmail.com** |

## Before opening an issue

- Read [`README.md`](README.md).
- Try the 30-second demo: `python examples/try_kry.py`.
- Check whether your question is answered by the computed status: `readiness_label()` and
 [`docs/KRY_READINESS.md`](docs/KRY_READINESS.md).

## Reproducing the core claim

```bash
python -m pip install -e ".[dev]"
PYTHONPATH=src python -m pytest tests/ -q # 475 tests
python scripts/kry_savings_report.py examples/sample_usage_log.jsonl
```

This is a maintainer-supported project; response times are best-effort.
