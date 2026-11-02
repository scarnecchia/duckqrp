# Finding template

Copy this file to `investigation/findings/<NNN>-<short-slug>.md` and fill
every field. A finding without a witness file or a red/green pair is a
hypothesis, not a finding.

```markdown
# <NNN>: <short slug>

- **id:** <NNN>
- **proposed issue title:** <one line, reviewable without the campaign>
- **contract:** <which documented contract is violated; cite INPUTS.md /
  docs/ or the SAS reference cited in the code comment>
- **trigger:** <the input condition that reaches the faulty code —
  keep RIPR in mind: reachability, infection, propagation, revealability>
- **witness/repro command:** <path to investigation/witnesses/<file> AND
  the exact command, e.g. `python -m pytest
  'tests/properties/test_x.py::test_y' --hypothesis-profile=quick`>
- **expected vs actual:** <both, concretely. A disagreement with an
  opaque generated model alone is not the deliverable>
- **first failing revision:** <SHA; or "since c3e60db5d08343b1cebbb90d234602f09cb0d70d
  (campaign base)" if not bisected further>
- **mechanism:** <why the code does this; the invariant that is actually violated>
- **severity:** <crash / wrong answer / silent divergence from SAS / reproducibility>
  **+ unproven impact:** <what it would mean downstream, clearly labeled
  as not yet demonstrated end to end>
- **affected paths:** <src/qrp/... files + shipped SQL files>
- **minimal fix/commit:** <branch + SHA once a fix exists; empty while open>
- **red/green commands+results:**
  - red: <command> → <failure, before fix>
  - green: <command> → <pass, after fix>
- **broader validation:** <the generalized failure-family generators and
  related entry points the fix was checked against>
- **related findings:** <ids sharing a mechanism, or "none">
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
```

Rules of thumb:

* One mechanism per finding. Different traces, same cause → one file;
  similar symptoms, different causes → separate files.
* The deterministic reduction belongs in the eventual fix PR, not here;
  the witness + property stays on the discovery-side branch.
* Update `manifest.json` (`findings` array) when filing and when
  resolving.
