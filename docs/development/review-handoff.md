# EOS review and merge handoff

The consolidation implements the remaining GENETIC optimizer, configuration-owned
Optimize request and result/PDF output. See [GENETIC rollout](genetic-rollout.md)
for configuration and manual acceptance. Historical planning documents in this
directory describe earlier checkpoints; this handoff supersedes their pending-work lists.

## Integration status

The seven prerequisite packages #1322-#1328 are merged into main. PR #1329 was
merged into `integration/genetic-prerequisites`, not into main. The final delivery
branch `feat/genetic-complete-main-port` brings that complete implementation directly
to main. Until that final PR is merged, main lacks the complete GENETIC port.

The foundation preserves the original #1256/#1305 contributions and compatibility
corrections; do not merge those original PRs again as extra prerequisites.

Main `3c86254` has exactly the production source tree of the original prerequisite
integration `5eacd54`. Only generated API version strings and one corrected test
import differ. The final delivery preserves the complete production source and
tests from #1329, including that corrected import, and regenerates API/configuration
documents from the combined code. Its merge conflicts arise from the rewritten
squash ancestry, not from additional production changes on main.

Review and merge the final PR with **main** as its target and all four current-head
checks green: pytest, pre-commit, Docker and CodeQL. Squash and merge is supported.
Do not use a merge into the comparison branch as a release. Keep the original
working copy and backup until manual installation acceptance is complete.

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
