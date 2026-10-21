"""A green optional-crypto job requires actual execution and the configured native library."""

from __future__ import annotations

import pytest

from scripts import kry_optional_crypto_check as check


@pytest.mark.parametrize("body", ["", '<testcase><skipped/></testcase>',
                                  '<testcase><failure/></testcase>', '<testcase><error/></testcase>'])
def test_empty_skipped_or_failed_report_is_rejected(tmp_path, body):
    report = tmp_path / "result.xml"
    report.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8")
    with pytest.raises(ValueError):
        check.check_junit(report)


def test_successful_report_uses_actual_cases_not_summary_count(tmp_path):
    report = tmp_path / "result.xml"
    modules = sorted(check.REQUIRED_MODULES)
    cases = "".join(f'<testcase file="{modules[i % len(modules)]}" name="case{i}"/>'
                    for i in range(check.MINIMUM_CASES))
    report.write_text(f'<testsuites tests="999"><testsuite>{cases}</testsuite></testsuites>',
                      encoding="utf-8")
    assert check.check_junit(report) == check.MINIMUM_CASES


def test_report_missing_a_required_module_is_rejected(tmp_path):
    report = tmp_path / "result.xml"
    modules = sorted(check.REQUIRED_MODULES - {"kry_pqc/test_pqc_review_regressions.py"})
    cases = "".join(f'<testcase file="{modules[i % len(modules)]}" name="case{i}"/>'
                    for i in range(check.MINIMUM_CASES))
    report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>", encoding="utf-8")
    with pytest.raises(ValueError, match="omitted"):
        check.check_junit(report)


def test_report_with_all_modules_but_too_few_cases_is_rejected(tmp_path):
    report = tmp_path / "result.xml"
    modules = sorted(check.REQUIRED_MODULES)
    cases = "".join(f'<testcase file="{modules[i % len(modules)]}" name="case{i}"/>'
                    for i in range(check.MINIMUM_CASES - 1))
    report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>", encoding="utf-8")
    with pytest.raises(ValueError, match="minimum"):
        check.check_junit(report)


def test_missing_native_library_fails_before_wrapper_import(tmp_path, monkeypatch):
    def unexpected_import(name):
        pytest.fail(f"must not import {name} when native library is missing")

    monkeypatch.setattr(check.importlib, "import_module", unexpected_import)
    with pytest.raises(FileNotFoundError):
        check.check_native(tmp_path / "absent.so")
