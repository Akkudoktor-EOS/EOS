# fix(optimization): return only the current completed Optimize result

Published with user approval as [PR #1323](https://github.com/Akkudoktor-EOS/EOS/pull/1323).
Branch: `fix/optimize-run-result`; base main `4a37244`; head `ef8d913`.
Three commits, three changed files. No merge or deployment.

A failed explicit optimization previously returned an old cached solution as HTTP
200. Conversion failures could publish a new native result alongside the previous
generic result and plan. Return this run's completed solution directly to the route;
build all three representations before publishing them together. Preserve previous
consistent results after optimizer/conversion errors and skip the failed run's
control dispatch. Legacy `/optimize` remains GENETIC0; automatic mode keeps its
configured algorithm selection. No optimizer mathematics or slot behavior changes.

Validation: 38 tests passed, 2 regular long-running tests skipped. All 22 new
regression cases passed again after typing dynamic test keyword arguments. Source
Ruff, formatting and diff checks passed. Scoped mypy passed for all three changed
files (transitive imports/untyped dependency diagnostics excluded locally).
Pinned Linux CI: 1908 passed, 16 skipped; pre-commit including full mypy, CodeQL and Docker passed. Thirty regressions also passed separately in UTC and Europe/Berlin; timezone expectations are now explicit.

The fix is already combined with PV, tariff and device packages in the integration
branch. Integration also carries the test annotation correction. Independent of
#1322, #1256 and #1305. Original working copy remains untouched.
