# C-1: the dose-censor statement claims a post-truncation criteria
# re-check that does not happen — and SAS does enforce one (parity-gap
# lead, NOT fixed; comment corrected)

- **id:** C-1
- **proposed issue title:** 55_dose_censor.sql comment claims a
  length-criteria re-check after truncation; the SQL does not re-check
  and SAS does — parity gap recorded, comment corrected
- **contract:** SAS's maxcumdose handling is apply-and-check BY
  ORDERING within one data step: ms_createptsmasterlist.sas:124
  truncates EpisodeEndDt at censordate_maxdose, and only then are the
  length-dependent flags evaluated on the truncated end — MinEnrolMet3
  (:156), MinObsEnrolMet2/4 (:160/:162), MinEpiDurMet (:165) — and
  enforced with the subsetting IF (:185). This implementation evaluated
  those criteria in 50_episodes (min_epi_dur_met :241, min_enr_aft_met
  :237, enforced at :258-262) on the PRE-truncation end;
  55_dose_censor.sql then truncates episodeenddt/episode_days and
  re-checks NOTHING. The in-file comment claimed "Apply, then re-check
  the criteria that depend on episode length ... exactly as it can in
  SAS" — false on both halves: no re-check happens here, and SAS's
  behavior differs. (The assignment pointed at ms_episoderec2.sas for
  the dose-truncation path; that file contains no maxcumdose logic —
  the apply-and-check lives in ms_createptsmasterlist.sas, applied from
  ms_createpov4.sas:178-201's censordate_maxdose.)
- **trigger:** A cohort with BOTH maxcumdose and cumdoseper set (the
  stage's gate, SAS's own gate `&maxcumdose. ne . and &cumdoseper. ne .`)
  where the running cumulative dose tips over maxcumdose on a claim
  whose expiry date pushes the episode below minepisdur — or below the
  required observable days (reqdaysaftepi / obs windows).
- **witness/repro command:**
  `investigation/witnesses/c1-dose-censor-recheck-sas-evidence.txt`
  (SAS line evidence; no Python witness study built — quantifying needs
  a maxcumdose-tipped short-episode fixture, see DB-5).
- **expected vs actual:** Expected (parity with SAS): an episode the
  dose censor pushes below a length criterion is deleted. Actual: it
  survives with a shortened episode_days — cohort membership and every
  downstream level differ from SAS for that study shape.
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base).
- **mechanism:** stage ordering: 50 filters on flags computed from the
  untruncated end; 55 truncates afterwards as a pure projection. SAS
  computes flags from the truncated end because the truncation precedes
  the flag assignments in the same step. Severity: silent divergence
  from SAS for maxcumdose+cumdoseper studies whose censor date crosses
  a length boundary — moderate if such studies exist, otherwise latent.
  NOT implemented by the arbiter (assignment: record as a lead, decide
  via DB-5).
- **severity:** silent divergence from SAS (parity-gap lead) + doc
  defect (the false comment, corrected).
  **+ unproven impact:** episode-level attrition counts and all
  downstream outputs for affected maxcumdose studies; magnitude unknown
  without a witness study.
- **affected paths:** src/qrp/sql/55_dose_censor.sql (comment corrected
  in inv/0f462p/fix3-docs commit dff222c, comment-only); the behavioral
  gap spans 50_episodes.sql + 55_dose_censor.sql jointly (any fix
  belongs in DB-5's option space, not here).
- **minimal fix/commit:** none for the behavior (DB-5 open). Comment
  corrected in fix3-docs dff222c: states what the SQL does, what SAS
  does, and the verified line numbers.
- **red/green commands+results:** N/A for the behavior (lead, no fix).
  Comment-only commit validated by `git diff c3e60db fix3-docs -- src/`
  showing zero non-comment line changes, plus determinism+rewrites on
  the merged tree.
- **broader validation:** see DB-5 for what a fix would require
  (witness study with maxcumdose tipped on a short-episode patient;
  50/55 restructure options and their parity consequences).
- **related findings:** C-3 (same stage family, different mechanism,
  fixed), DB-5 (the decision brief this lead feeds), package C
  observation 1 (the original report, verified and sharpened here).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **SCDM 8.2.2 alignment:** reviewed 2026-11-03 — dose-censoring
  window mechanics; no SCDM data-model-bearing assumption.
