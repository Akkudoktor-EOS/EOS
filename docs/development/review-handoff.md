# EOS review handoff — 2026-09-16

The user authorized publication of all prepared packages and requested completed
checks before manual review. No upstream merge, HA change or deployment is authorized
by this handoff. The original feature working copy remains the preserved reference.

## Published packages

| PR | Scope | Review base | Expected merge prerequisite |
| --- | --- | --- | --- |
| [#1322](https://github.com/Akkudoktor-EOS/EOS/pull/1322) | Restore JSON measurement records into the singleton | main | Independent |
| [#1323](https://github.com/Akkudoktor-EOS/EOS/pull/1323) | Return only this successful Optimize run; publish consistent results | main | Independent |
| [#1324](https://github.com/Akkudoktor-EOS/EOS/pull/1324) | Reject invalid imported tariffs without replacing the provider | main | Independent |
| [#1325](https://github.com/Akkudoktor-EOS/EOS/pull/1325) | Local calibrated Akkudoktor PV backend | main | Independent |
| [#1326](https://github.com/Akkudoktor-EOS/EOS/pull/1326) | Typed measurements, quality, energy, household and capacity APIs | feat/config-integration-base | #1256, #1305, configuration corrections, #1322 |
| [#1327](https://github.com/Akkudoktor-EOS/EOS/pull/1327) | Slot-aware devices, export limits, interpolation and cache identity | feat/config-integration-base | #1256, #1305, configuration corrections |

`feat/config-integration-base` at `9038b65` is a published comparison/dependency
branch. It preserves existing contributor history rather than replacing #1256/#1305
with another competing PR. Its complete pinned mypy check passes (231 files), as
do 257 configuration/device tests and five generated-documentation tests.

Do not merge #1326/#1327 into this comparison branch as if that released them to
main. After the prerequisites land, rebase/retarget them onto main, remove overlaps
such as #1322, and repeat combined CI. CodeQL is configured to run only for PRs
targeting main; it is not an expected check on these two stacked PRs yet.

## Review focus

- #1323: a failed optimizer/conversion must never return stale HTTP success; native
  and generic results and the execution plan must remain consistent for both algorithms.
- #1324: sale revenue stays amount/Wh, including zero and negative prices; invalid
  imports cancel preparation. Forward-fill does not establish raw forecast freshness.
- #1325: public provider ID stays PVForecastAkkudoktor, remote remains the default;
  local configuration is explicit, migration preserves current values, power stays W.
- #1326: missing measurements remain distinct from zero; time support and quality
  determine coverage. A capacity estimate never silently replaces active capacity.
- #1327: energy/efficiency and export constraints hold at both slot lengths; the
  unchanged GENETIC0 implementation retains its separate interpolator and legacy API.

## Remaining feature work

These six PRs do not complete the old feature branch's full new GENETIC. Quarter-hour
orchestration, warmstart alignment, EV deadlines/flexible profiles, forecast-tail
integration, the config-owned `/v1/optimize` request, and corresponding result/PDF
output still require implementation and acceptance. Main's current preparation
still enforces hourly GENETIC slots. Local slot-physics tests alone are not an
end-to-end quarter-hour Optimize acceptance.

## Final check results

All six PRs were freshly verified open and unmerged on their listed commits.
Every applicable GitHub workflow completed successfully on 2026-09-16.

| PR | Verified head | Full pytest | Other applicable checks |
| --- | --- | --- | --- |
| #1322 | ce132ea | 1,884 passed, 16 skipped | Pre-commit/mypy, CodeQL, Docker passed |
| #1323 | ef8d913 | 1,908 passed, 16 skipped | Pre-commit/mypy, CodeQL, Docker passed |
| #1324 | 2a3b961 | 1,921 passed, 16 skipped | Pre-commit/mypy, CodeQL, Docker passed |
| #1325 | f0a560b | 1,916 passed, 16 skipped | Pre-commit/mypy, CodeQL, Docker passed |
| #1326 | 635ff2d | 2,046 passed, 16 skipped | Pre-commit/mypy and Docker passed |
| #1327 | 8292ea1 | 1,994 passed, 16 skipped | Pre-commit/mypy and Docker passed |

The local full combined suite completed with 2,191 passed, 18 skipped, six failures
and 18 setup errors. Three failures reproduce on unchanged main: the Windows CEC
file-timestamp test and two Docker tests without a reachable local engine. The
server PID failure and setup errors also reproduce on unchanged main: Windows
virtualenv launchers have a different PID from their healthy child servers. The
remaining failures concern missing Sphinx executable discovery (corrected test
environment for the rerun) and the already corrected docstring markup. Complete
locked mypy now passes across all 253 combined source/test files. The follow-up
tail-value, terminal-value, file-restore, Optimize compatibility and generated-doc
checks passed all 37 cases after the corrections. The full Sphinx HTML build also
passed after activating the isolated environment and correcting UTF-8 encoding in
the local handoff documents (11m29s). No green result
should be inferred for a superseded head. The initial #1323 Linux run exposed a
test's local timezone
assumption; the replacement explicitly checks UTC and Europe/Berlin. #1327's
docstring reference was corrected to syntax accepted by the repository checker.

The original HEAD and all 102 files recorded in the preservation manifest were
verified unchanged. All six published PR worktrees and their configuration base
are clean. Full logs, baseline reproductions and the final workflow snapshot are
retained in the private backup `eos-20260916-120324`.
