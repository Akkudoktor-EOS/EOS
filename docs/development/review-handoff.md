# EOS review and merge handoff

The consolidation implements the remaining GENETIC optimizer, configuration-owned
Optimize request and result/PDF output. See [GENETIC rollout](genetic-rollout.md)
for configuration and manual acceptance. Historical planning documents in this
directory describe earlier checkpoints; this handoff supersedes their pending-work lists.

## PR dependencies

<!-- pyml disable line-length -->
| PR | Scope | Review base | Merge prerequisite |
| --- | --- | --- | --- |
| [#1322](https://github.com/Akkudoktor-EOS/EOS/pull/1322) | Restore JSON measurements | main | Independent |
| [#1323](https://github.com/Akkudoktor-EOS/EOS/pull/1323) | Atomic Optimize result publication | main | Independent |
| [#1324](https://github.com/Akkudoktor-EOS/EOS/pull/1324) | Preserve imported sale tariffs | main | Independent |
| [#1325](https://github.com/Akkudoktor-EOS/EOS/pull/1325) | Local calibrated Akkudoktor PV | main | Independent |
| [#1328](https://github.com/Akkudoktor-EOS/EOS/pull/1328) | Device/configuration foundation | main | Includes #1256/#1305 |
| [#1326](https://github.com/Akkudoktor-EOS/EOS/pull/1326) | Measurement and quality APIs | feat/config-foundation-main | #1328, #1322 |
| [#1327](https://github.com/Akkudoktor-EOS/EOS/pull/1327) | Slot-aware devices and export | feat/config-foundation-main | #1328 |
| [#1329](https://github.com/Akkudoktor-EOS/EOS/pull/1329) | Complete GENETIC, requests and reports | integration/genetic-prerequisites | All above |
<!-- pyml enable line-length -->

Merge the independent packages and #1328 first. The foundation preserves the original
`#1256/#1305` contribution histories and adds compatibility corrections; do not merge
those original PRs again as extra prerequisites. After their dependencies reach main,
retarget/rebase #1326/#1327 onto main and rerun CI. Then retarget/rebase the complete
GENETIC PR onto main and rerun combined CI. Squash merges can require removing already
landed commits when rebasing. Do not release by merging into a comparison branch.

The sequential merge rehearsal reproduced the complete tested tree. Small conflict
resolutions are needed: for #1328/#1327/#1329, select the newer version line in
`docs/_generated/openapi.md` and `openapi.json`, keeping the automatically combined
schemas. For #1326, retain both the `bisect_left`/`bisect_right` and
`datetime`/`timedelta` imports in `measurement.py`. Do not replace entire schema
files with one side of a merge conflict.

The final comparison branch is the union of the seven published prerequisite heads.
Its ancestry is attached without changing the combined, tested feature tree.
CodeQL currently runs for PRs targeting main, so it becomes applicable to stacked
PRs after retargeting. Other checks run on the stacked branches already.

## Review and validation

- Optimize: preparation or conversion failure never returns a previous successful
  result; native result, generic solution and execution plan publish atomically.
- Forecasts: provider power is converted from W to slot Wh exactly once. Raw missing
  records stay missing, control coverage is mandatory, and a shorter tail is clipped.
- Economics: explicit/imported sale prices remain authoritative, including zero and
  negative values. Battery export is opt-in; terminal value is separate from LCOS.
- Time: 15/60-minute slots, repeated DST hours, local-midnight forecast origins and
  non-integer timezone offsets are covered. Old GENETIC reports use saved timestamps.
- Devices: EV deadlines, flexible power profiles, crossed per-cycle windows, completed
  cycles and minimum gaps are tested through the optimizer and result conversion.
- Compatibility: GENETIC0 keeps its legacy request and device implementation. Both
  algorithms retain finite solution validity and existing persistent plan instructions.

Exact workflow results belong to each PR's current head; superseded green heads do
not prove a later revision. The combined checks cover pinned mypy, generated OpenAPI
and configuration, physics, native HTTP, automatic preparation, PDF generation and
the 400-generation optimizer regression.

Local Windows server-PID tests and Docker builds have known environment failures
that also reproduce on unchanged main. Linux CI remains the full-suite gate. Local
PDF pixel comparison needs an optional converter; report semantics and PDF bytes
are tested independently. Production HA/device behavior still needs manual acceptance.

## Preservation and release boundary

The original feature working copy is read-only. Its original HEAD and 102 saved files
are checked against the private preservation manifest. Runtime configuration, secrets
and real measurements are excluded from the PRs. No remote PR is merged and no HA
configuration, production server or physical device is changed by this preparation.
