-- ---------------------------------------------------------------------
-- MAXCUMDOSE censoring (ms_createpov4.sas:178-201).
--
-- SAS does TWO different things with maxcumdose, and this implementation
-- previously did only the first:
--
--   1. ms_pov1dose: exclude the index date when prior cumulative dose is
--      outside [mincumdose, maxcumdose]. That is `dose_excluded`.
--   2. ms_createpov4: accumulate dose WITHIN the episode, from
--      (episodestart - cumdoseper) onward, and CENSOR the episode at the
--      expiry date of the claim that tips the running total over
--      maxcumdose.
--
-- The difference matters: excluding a patient and shortening their
-- follow-up give quite different denominators.
--
-- Only built when a cohort sets both maxcumdose and cumdoseper, which is
-- SAS's own gate (`&maxcumdose. ne . and &cumdoseper. ne .`).
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE dose_censor AS
WITH in_window AS (
    SELECT
        m.cohortgrp,
        m.patid,
        m.indexdt,
        d.adate,
        d.expiredt,
        d.cumdose,
        c.max_cum_dose
    FROM ptsmasterlist m
    JOIN cfg_cohort c
      ON c.cohortgrp = m.cohortgrp
     AND c.max_cum_dose IS NOT NULL
     AND c.cum_dose_per IS NOT NULL
    JOIN claim_dose d
      ON d.cohortgrp = m.cohortgrp
     AND d.patid     = m.patid
     -- SAS: (episodestartdt - cumdoseper) <= adate <= episodeenddt
     AND d.adate BETWEEN m.indexdt - c.cum_dose_per AND m.episodeenddt
),
running AS (
    SELECT
        *,
        sum(cumdose) OVER (
            PARTITION BY cohortgrp, patid, indexdt
            ORDER BY adate, expiredt
            ROWS UNBOUNDED PRECEDING
        ) AS total
    FROM in_window
)
SELECT
    cohortgrp,
    patid,
    indexdt,
    -- SAS takes min() of the expiry dates where the total is exceeded,
    -- i.e. the FIRST claim to tip it over.
    min(expiredt) AS censordate_maxdose
FROM running
WHERE round(total) > max_cum_dose
GROUP BY 1, 2, 3;

-- Apply the dose-censor date to the episode end. NOTE: the
-- length-dependent criteria (min_epis_dur, required days after
-- index/episode) were evaluated in 50_episodes on the PRE-truncation
-- end and are NOT re-checked here, unlike SAS: ms_createptsmasterlist.sas
-- truncates at :124 and only THEN evaluates the flags that read the
-- truncated end (MinEnrolMet3 :156, MinObsEnrolMet2/4 :160/:162,
-- MinEpiDurMet :165) and enforces them (subsetting IF :185). An episode
-- the dose censor pushes below a length criterion therefore survives
-- here but is deleted in SAS.
CREATE OR REPLACE TABLE ptsmasterlist AS
SELECT
    m.* EXCLUDE (episodeenddt, episode_days),
    least(m.episodeenddt,
          COALESCE(x.censordate_maxdose, DATE '9999-12-31')) AS episodeenddt,
    span_days(m.indexdt,
              least(m.episodeenddt,
                    COALESCE(x.censordate_maxdose, DATE '9999-12-31')))
        AS episode_days
FROM ptsmasterlist m
LEFT JOIN dose_censor x
  ON x.cohortgrp = m.cohortgrp
 AND x.patid     = m.patid
 AND x.indexdt   = m.indexdt;
