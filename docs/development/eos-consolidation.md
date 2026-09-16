# EOS consolidation — integration record

Status: in progress, not a completed optimizer port. Updated 2026-09-16.

Current PR-ready packages, dependency order and development guidance:
[PR workflow](pr-workflow.md). The first independent fix is ready locally on main;
the complete feature consolidation remains open.

## Pinned sources

- Official main: `4a3724424f98b5ce814d0340ddf3b6b5f1364752` (refreshed after initial checkpoint).
- Feature branch: `d2e2d58237339454dd8bb226f92677c5987f8b27`.
- PR #1256: `6ebd343047b87819b57778359784a616452b5f76`, open, conflicts.
- PR #1305: `60b77f6da2da3a0f59518873fe7fbe9a92e733f4`, open, main target. Latest added commit is formatting only.
- PR #1224: `0b12a34c11685822d97c3bc9f56c6292774cd930`, open, feature target.
- PR #1304: `4b4b49f29902579a8deb34999d71eb2142c0620d`, open, feature target.
- PR #1190 merged into main: GENETIC0 retained separately.

Sources: https://github.com/Akkudoktor-EOS/EOS/issues/1192 and linked PRs.
Issue body, all issue comments (none), and PR metadata were retrieved from the GitHub API.
Use `refs/remotes/origin/main`: local `refs/heads/origin/main` is ambiguous.

## Isolation and recovery

Original EOS worktree remains on the feature branch. No reset, stash or checkout there.
Integration branch: `integration/eos-consolidation-20260916`, sibling directory
`EOS-integration-20260916`. Unmodified main comparison worktree: `EOS-reference-20260916`.

Private backup: `%USERPROFILE%/.codex/backups/eos-20260916-120324`.
102 modified/untracked files copied byte-for-byte with SHA256 verification; binary
tracked and index patches, original status/refs, verified `repository.bundle`.
See private `RESTORE.md` and `manifest.json` for recovery into a NEW checkout.
Ignored runtime files stay in the original worktree; this is not an archive of its
virtual environment, caches or ignored production datasets. Backup is private and
may include credentials or real measurements; never stage or publish it.
Only explicitly reviewed source/test/document paths are staged for new commits.

## Functional matrix

| Area | Evidence | Integration state |
| --- | --- | --- |
| Providers, SMARD, fee model | #1192 checked; main prediction modules and #1235 | Keep main implementation; provider equivalence not yet established |
| GENETIC0 / legacy API | #1190; `core/ems.py`, `server/eos.py`, `optimization/genetic0` | Preserved; device regressions pass, full endpoint validation pending |
| Device maps / algorithm conversion | #1256; `devices/settings`, `config/configmigrate.py` | Merged with original history; four merge conflicts resolved |
| Runtime updates | #1305; `config/config.py`, `core/pydantic.py` | Merged with original history; configuration tests pass |
| Stable IDs / LCOS migration | New `test_consolidation_config.py` | Four initial regression failures fixed; 55 configuration tests pass |
| 15-minute battery/inverter physics | Feature `devices/genetic`, `prediction/interpolator.py` | Ported in c0796e1; parameter classes retained in device modules, export/slot/efficiency tests pass |
| New GENETIC, adaptive evolution, export states | Feature `optimization/genetic/genetic.py` | Pending; current optimizer is not the complete feature optimizer |
| Warm start alignment | Feature d2e2d58, `test_genetic_warm_start_alignment.py` | Pending |
| Control horizon / forecast tail / terminal value | Feature a2f4ef6, `tailvalue.py`, `terminalvalue.py` | Primitives ported in f70a786; 22 tests pass. Optimizer/horizon/API integration pending |
| EV deadlines / flexible consumers | Feature f24d9ea; #1256 cycle windows | Pending reconciliation: preserve both per-cycle windows and profile/deadline semantics |
| Imported feed-in revenue | #1224 + #1304, 38 feature regression cases | Neither PR targets main; adapt after GENETIC preparation port, keep author credit |
| Local calibrated PV | Feature f976335/6dc58c3/faed0fd | Pending; keep public provider ID PVForecastAkkudoktor per #1192 |
| Algorithm-specific PDF | Main #1205 vs feature utils/visualize.py | Keep on-demand API; feature slot/tail fields pending |
| Measurement channels/energy/quality/capacity/household | Original uncommitted source + five new tests | Ported in 7338baf to async storage and keyed devices; 127 related tests passed |
| Request configuration learning | Original uncommitted `genetic/configrequest.py` | Secured only; pending |
| HA-private core differences | Separate private repo | Not ported; no HA files modified, no deployment |

## Completed packages and tests

1. `d3f96c4`: merge #1256 onto pinned main. Conflicts in devices.py and three generated
   documents; preserve maps/settings and main evolution. Regenerate OpenAPI from
   merged code. Do not adopt PR build version bumps. 244 configuration/time-window
   tests + 81 device/simulation tests passed before subsequent packages.
2. `b43c8df`: merge #1305 without conflicts. 275 config/configabc/configmigrate/pydantic
   tests passed.
3. `e3987b2`: stable map identities, reject key/ID mismatch, preserve renamed LCOS,
   repair legacy EV charge-rate migration. 4 regression cases failed before fix;
   55 related tests passed afterwards. These are integration/PR defects, not claimed
   as unchanged-main defects.

Tests currently use the existing Python 3.11.9 environment read-only; pytest adds
this worktree's src. No mutation of the original venv. Missing pypdf 6.19.0 installed into the private
backup test-packages directory and exposed only through test-process PYTHONPATH.
This is not yet validation against every pinned dependency / CI Python version.
Synthetic pytest fixtures; no real devices, server deployment or production config.

## HA handoff and acceptance

Do not relocate active feature development to this integration branch yet.
Device collections are keyed maps, with identical map key and device_id. New settings
live in devices/settings; conversion methods derive algorithm parameters. LCOS
configuration is `levelized_cost_of_storage_amt_kwh`; old files migrate.
Runtime config bulk/granular precedence follows #1305. Both algorithms remain
separate; `/optimize` uses GENETIC0 and cannot demonstrate the new GENETIC port.
Pin a final tested commit for HA only after all remaining packages and end-to-end
API/forecast/output tests. No public push, PR, comment or main update is authorized.


4. `c0796e1`: slot-duration-aware battery/inverter simulation, bounded per-slot
   discharge/export, probabilistic direct-use energy model and GENETIC converter
   support for LCOS/export levels. Original author co-authorship retained.
   Fixed monetary goldens from the previous model are intentionally replaced with
   independent grid-flow repricing. Original simulation test passed on unchanged
   main; the changed result is not labelled a baseline failure.
5. `f70a786`: bounded forecast reader, concave terminal value and deterministic
   forecast-tail primitives. 22 direct primitive tests passed. Does NOT activate
   the new optimization algorithm, warmstart, horizon handling or diagnostic API.
6. `d9c434f`: keep main's charge-rate ndarray contract and exported constant after
   #1256. The first broad collection found this missed overlap.
7. `7338baf`: typed measurement channels, sample quality, energy integration,
   household accounting and battery capacity estimation. Adapt all storage calls
   and endpoints to main's async API; map battery IDs through device collections.
   Capacity estimates survive subsequent runtime config updates and never change
   the active capacity_wh. 127 new/existing measurement tests passed, including
   JSON/SQLite/LMDB restart, partial coverage and real FastAPI routes with no lifespan.
   JSON measurement reload regression reproduced on unchanged afb7bcb (0 records
   after reload); applying the previously local singleton fix resolves it.
8. `876756b`: replace stochastic GENETIC output equality with schema, independent
   per-slot cost/revenue accounting and physical-range assertions. GENETIC0 goldens
   remain unchanged. Short optimizer/PDF runs: 4 GENETIC0 and 4 GENETIC pass; one
   400-generation case for each algorithm skipped by the existing --finalize rule.
   New household configuration field has documentation metadata.

### Remaining integration risks and concrete next package

The current GENETIC orchestration is still the main-era optimizer with the newly
ported device physics. It must NOT be described as the complete feature optimizer.
Next port `optimization/genetic/genetic.py`, its parameter preparation and solution
model together, carrying feature d2e2d58 warmstart and #1304 revenue fixes. Translate
old top-level optimization settings to `optimization.genetic`. Check the actual
prediction record units: current `elecprice_marketprice_wh` and `feed_in_tariff_wh`
arrays already contain amount/Wh; only amount/kWh configuration is divided by 1000.
Do not apply a second conversion to the existing *_wh arrays. Use #1256 `to_genetic_*` converters
instead of reintroducing parallel settings. Reconcile flexible profiles/deadlines
with #1256 per-cycle windows/completed cycles before exposing the combined API.
Both porting sides must retain their regression cases. Follow with local PV and
algorithm-specific on-demand PDF; do not copy feature's synchronous/automatic-PDF
server paths over main. Then adapt local configrequest.py to the current API.

No feature-provider robustness fixes are claimed ported merely because a provider
with the same name exists on main. HA private-core differences remain unaudited.
No full 2030-test suite run, --finalize optimization run, dependency-pin CI matrix,
or physical installation validation has been completed.

### Backup verification

An independent clone of repository.bundle at the original feature HEAD was restored
using the backup files. All 102 restored SHA256 hashes match. The original worktree
HEAD and all 102 file hashes were also rechecked unchanged after the source ports.


## Reproducing the focused acceptance run

Use a disposable environment with the repository dependencies plus pytest,
pytest-asyncio, pytest-xprocess, pytest-cov and pypdf. The private backup contains
`test-environment.json`, `integration-final.xml` and the complete collection log.
Run from the integration worktree (not the original feature worktree):

```powershell
python -m pytest tests/test_typingmodels.py tests/test_config.py tests/test_configabc.py tests/test_configmigrate.py tests/test_configfile.py tests/test_pydantic.py tests/test_consolidation_config.py tests/test_genetichomeappliance.py tests/test_genetic0battery.py tests/test_genetic0inverterefficiency.py tests/test_genetic0simulation.py tests/test_battery.py tests/test_inverter.py tests/test_inverter_efficiency.py tests/test_geneticsimulation.py tests/test_geneticsimulation2.py tests/test_terminalvalue.py tests/test_tailvalue_physics.py tests/test_interpolator.py tests/test_measurement_channels.py tests/test_measurement_energy.py tests/test_measurement_household.py tests/test_battery_capacity.py tests/test_measurement_file_restore.py tests/test_measurement.py tests/test_genetic0optimize.py tests/test_geneticoptimize.py tests/test_doc.py -q --tb=short
```

Do not inherit EOS_DIR/EOS_CONFIG_DIR from documentation generation when running
pytest: these conflict with fixture-controlled temporary config directories.
Generate OpenAPI after committing source changes; source-dirty version timestamps
otherwise make exact documentation comparisons nondeterministic.


## Final verified checkpoint for this work session

Focused combined run: **633 passed, 2 skipped in 65.46 s**. The skips are the
existing 400-generation --finalize cases. Zero remaining failures in that run.
All 2030 collected tests were collectable after supplying pypdf and resolving the
charge-rate import overlap; collection alone is not a pass of the full suite.
Ruff passed for the ported measurement/device/interpolator/primitive source files;
git diff --check passed. OpenAPI/config documentation generation and the documentation
comparison tests passed. The first combined run caught fixture state leakage in the
new battery test; reset-before/after fixture isolation resolved it on the repeated run.

This is a tested partial integration checkpoint, NOT completion of the consolidation.
The source packages are locally committed; release, full feature acceptance and
upstream submission remain pending. Do not move active development or HA deployment
here until the remaining optimizer/prognosis/output/request packages are integrated.


## PR-readiness verification, 2026-09-16

Current main `7ebe6d7` is incorporated. Two reviewable local packages now exist:
`fix/measurement-json-reload` (`dba0c9c`, independently based on main) and
`feat/measurement-energy-quality-capacity` (`ea3383e`, depends on the configuration
integration base `d546f08`). The latter excludes the new optimizer/device physics.
See [PR workflow](pr-workflow.md) and its concrete draft descriptions.

The expanded integration run completed with 764 passed, 3 skipped and 2 documentation
failures in 453.99 seconds. Both failures were solely stale generated OpenAPI version
metadata, not schema or functional differences. Regenerated the two files; the focused
rerun of all 5 documentation tests passed. No remaining failure from that selection.
The complete expanded selection was not rerun after this documentation-only correction.
Three skips: the two existing long --finalize optimizer cases and the development-only
Energy-Charts forecast case. All 128 active Energy-Charts regressions passed.

Independent package checks: JSON fix 49 tests; isolated measurement package 453 tests
plus 5 documentation tests; capacity fixture isolation followed by 74 passing tests.
Ruff and source formatting passed for the independent fix; measurement source Ruff
passed. Full pinned Linux/Python 3.13 CI remains outstanding.

All original 102 saved file hashes and original feature HEAD were checked unchanged.
Both PR worktrees are clean, locally committed, and unpublished. This enables small
independent PRs now; it does not complete the still-open optimizer/PV/output port.


## First upstream PR published, 2026-09-16

Explicit user approval received for publishing the standalone JSON restore fix.
Fetched main `4a37244` (dependency update #1321) and rebased the standalone branch;
its new head is `bdc754d12fd08e0da18d3156642c695f4bf67bd2`. All 49 measurement tests,
source Ruff and formatting checks passed again. Pushed only
`fix/measurement-json-reload` and created https://github.com/Akkudoktor-EOS/EOS/pull/1322
against main. Verified the remote head and PR patch: one commit, exactly two files.
The other integration/measurement branches were not pushed. The PR is conflict-free,
not merged; GitHub CI was started and is being checked. Original HEAD and all 102
backup hashes were rechecked unchanged. Earlier notes saying all packages are
unpublished are historical checkpoints; this section supersedes them for this fix.


## Parallel package preparation and compatibility

On the user's explicit request, independent PV and tariff ports and Optimize-mode
compatibility coverage are being prepared in separate worktrees. The main/integration
configuration prerequisite was refreshed to main4a37244 and PR1305head60b77f6;
285 configuration/migration/Pydantic tests pass. The latest 1305 change only formats
three files. See [PR integration matrix](pr-integration-matrix.md) for the seven
remaining functional packages, existing upstream prerequisites, and combined
Optimize acceptance gates. Parallel work is not authorization to publish every lane.
