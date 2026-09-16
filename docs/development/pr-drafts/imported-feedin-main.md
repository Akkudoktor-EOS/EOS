# fix(optimization): reject invalid imported feed-in tariffs without fallback

Published with user approval as [PR #1324](https://github.com/Akkudoktor-EOS/EOS/pull/1324).
Branch `fix/imported-feedin-main`, head `2a3b961`, base main `4a37244`.
Two commits, two changed files. Original contributions from #1224/#1304 are credited.

GENETIC preparation now cancels if FeedInTariffImport cannot supply a finite
one-dimensional tariff array matching the forecast length. It keeps the configured
provider instead of switching to demo tariffs. Positive, zero and negative valid
amount/Wh revenues remain unchanged. Other providers keep their fallback behavior.

Publication check: 68 passed, 2 regular long-running tests skipped; source Ruff,
format and diff checks passed. Scoped mypy passed for the two changed files with
transitive imports/untyped dependency diagnostics excluded. Full pinned Linux CI: 1921 passed, 16 skipped; pre-commit including full mypy, CodeQL and Docker passed.
Regression coverage includes seven provider identities, simulation arithmetic and
actual timestamped imports. Eleven invalid-input regressions fail on unchanged main.

The existing forward-fill path is unchanged: validation of its resulting array
does not establish raw timestamp coverage or freshness. Feature direct-marketing
overrides and quarter-hour orchestration remain part of the later GENETIC port.
Legacy /optimize and GENETIC0 are unchanged. The source fix is already integrated
with PV, device/configuration and Optimize-result packages in local combined tests.

No merge or deployment. All current-head checks completed successfully.
