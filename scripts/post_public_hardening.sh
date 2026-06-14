#!/usr/bin/env sh
# Run this ONCE, right after you switch the repository to public.
#
# It enables the security features that GitHub cannot turn on while a repo is
# private (Private Vulnerability Reporting, secret scanning, push protection) and
# re-affirms the Dependabot ones. The CodeQL and Scorecard workflows are already
# gated to auto-activate on the next run once the repo is public — no action needed
# for those.
#
# Requires: the `gh` CLI authenticated as an admin of the repo.
# Usage:    sh scripts/post_public_hardening.sh [owner/repo]

R="${1:-thequantumfalcon/kry}"
echo "Enabling public-only security on ${R} ..."

ok() { echo "  ok  $1"; }
skip() { echo "  --  $1 (skipped — repo still private, or insufficient permissions)"; }

gh api -X PUT "repos/${R}/vulnerability-alerts" --silent 2>/dev/null \
  && ok "Dependabot alerts" || skip "Dependabot alerts"

gh api -X PUT "repos/${R}/automated-security-fixes" --silent 2>/dev/null \
  && ok "Dependabot security updates" || skip "Dependabot security updates"

gh api -X PUT "repos/${R}/private-vulnerability-reporting" --silent 2>/dev/null \
  && ok "Private Vulnerability Reporting" || skip "Private Vulnerability Reporting"

gh api -X PATCH "repos/${R}" --silent --input - >/dev/null 2>&1 <<'JSON' \
  && ok "Secret scanning + push protection" || skip "Secret scanning + push protection"
{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}
JSON

echo ""
echo "Done. CodeQL + Scorecard activate automatically now that the repo is public."
echo "Remaining manual step (a deliberate choice — changes your push workflow):"
echo "  set up a branch ruleset on main (require PR + signed commits + status checks)."
echo "  See docs/GITHUB_HARDENING.md section 2."
