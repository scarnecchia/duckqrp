# SAS ground-truth rulings (package 14.5.0, read-only source `/home/scarndp/dev/Sentinel/qrp/`)

Date of investigation: this session. All quotes are verbatim from the SAS tree; line numbers are 1-based from the files as read.
Scope notes: `ms_createbaseline.sas` does not exist in this package — the baseline distribution table builder is `inputfiles/macros/ms_createdistbaselinetable.sas`. The dose-denominator macro is `inputfiles/macros/ms_dose_denom.sas`.

---

## Q1 (DB-6) — level-1 "non-missing birth date/sex" semantics

**Answer.** SAS tests non-missing with the `missing()` function — for a character variable that is true for the blank/empty string (SAS has no NULL; `.` is numeric-missing) — so a blank-string sex member is NOT counted at SAS level 1 and does NOT survive to POV1 or the master list.

**Evidence.**

`ms_create_enrollment_spans.sas:187-192` — the missing-demographics list is built with `missing()`:
```
    /*Determine non-missing birth date/sex and mutliple demog entries (should be zero - defensive coding)*/
    data worktemp.demogsmissing;
      set indata.&Demtable.(keep=patid birth_date sex);
      where missing(BIRTH_DATE) or missing(Sex);
      keep PatId;
    run;
```

`ms_attrition_enrollment.sas:89-95` — `_keep` (the level-1 population) drops those members before any counting:
```
    data _enrForAttrition;
      merge _enrForAttrition(in=a)
            worktemp.demogs(in=b)
            worktemp.demogsmissing(in=c)
            worktemp.multiDemogs(in=d);
      by PatId;
      if a and b and not c and not d;
```

`ms_attrition.sas:182-185` — level 1 is then just the distinct-PatId count of `_keep` (no `ToExclVar`):
```
	%let level=1;

    %ms_attrition_cidacompute(attritiontype=excl_Member, datafile=_keep, ToExclVar=, ToExclValue=, claim_level=Member,  
			   descr=%str(Initial Member Count - Members with a non-missing birth date/sex at any enrollment episode overlapping the query period), debug=&qrp_debug.); 
```
(count computed at `ms_attrition_cidacompute.sas:78-87`: `select count(*) ... select distinct PatId ... where not (&ToExclVar.=&ToExclValue.)` — vacuous true for level 1.)

Survival to POV1: blank-sex members were already removed upstream because enrollment spans require the demographic `in` lists, `ms_create_enrollment_spans.sas:141-142`:
```
			where Enr.patid=Dem.patid and upcase(Dem.sex) in (&DemogSex.) and 
			upcase(Dem.Race) in (&DemogRace.) and upcase(Dem.Hispanic) in (&DemogHispanic.) 
```
(blank ∉ `&DemogSex.` = `"F" "M" "U" "A"` default, `ms_create_enrollment_spans.sas:80-87`), so they have no enrollment spans, no shaved claims, and therefore no POV1 index dates. Had one reached POV1 anyway, sex would be recoded `case when sex in('F','M') then sex else 'O'` (`ms_createpov1.sas:87-89`).

**Port must.** Treat blank/NULL string sex and NULL birth date as missing via a `missing()`-equivalent (blank counts), drop such members from the level-1 population before counting, and never let them produce enrollment spans/POV1 rows (a blank sex that slipped through would recode to 'O', not survive as blank).

---

## Q2 (DB-7) — missing race in POV1 demographics

**Answer.** A missing/blank race is NOT remapped to 'M' or to '0'/'unknown' — the member is simply excluded: the enrollment-span join requires `upcase(Dem.Race) in (&DemogRace.)`, and the default/allowable race list never contains a blank, so a blank-race member keeps no enrollment spans and produces no POV1 rows.

**Evidence.**

`ms_create_enrollment_spans.sas:89-95` — the study race condition becomes the allowed list; when the study specifies none, the default is `0 1 2 3 4 5 M`:
```
					*Race values applicable to demographics;
					if missing(Race) then do;
					call symputx("DemogRace",'"0" "1" "2" "3" "4" "5" "M"');
					end;
					else do;
					call symputx("DemogRace",cat('"',tranwrd(upcase(strip(race))," ",'" "'),'"'));
					end;
```

`ms_create_enrollment_spans.sas:142` — the filter that drops blank race (inner join): `upcase(Dem.Race) in (&DemogRace.)`.

Attrition side, same list and same failure mode — `ms_attrition_enrollment.sas:154-159` (default `'"0" "1" "2" "3" "4" "5" "M"'`) and `ms_attrition_enrollment.sas:186-189`:
```
      MeetDemogs='Y';
      if upcase(sex) not in (&DemogSex.) or upcase(Race) not in (&DemogRace.) or
         upcase(Hispanic) not in (&DemogHispanic.) then MeetDemogs='N';
```

POV1 itself passes RACE through untouched (`ms_createpov1.sas:85-90`: `select distinct pts.*, BIRTH_DATE, case when sex ... end as sex, RACE, HISPANIC, ...`) — no defaulting at that step.

**Port must.** Exclude members whose race is missing/absent (blank fails the membership test against the study race list or the `0-5,M` default); do not map blank to 'M', '0', or an unknown bucket; pass a surviving member's race through to POV1 unchanged.

---

## Q3 (DB-8) — blank Pdx through extraction to the two consumers

**Answer.** SAS never blanket-normalizes a blank claim Pdx to 'X'; blank stays blank and is matched as blank — except that `%ms_envelope` recodes `pdx='X'` (and EnType to 'IP') for any non-IP claim whose ADate falls inside an inpatient stay, when `RUN_ENVELOPE ne 2`.

**Evidence — extraction.** `ms_cidanum.sas:664-679` stacks PX+DX extracts; PX claims get a blank Pdx by construction:
```
	      if procex then do;
	      code = compress(PX,' .');
	      PDX = " ";
	      Codetype = PX_codetype;
	      CodeCat = "PX";
	      end;
```

**Evidence — the only Pdx→'X' recoder.** `ms_envelope.sas:113-116` (gated by `%if &RUN_ENVELOPE. ne 2` at line 47; invoked at `ms_cidanum.sas:689-694` on the combined DX/PX dataset):
```
					if EncType ne 'IP' and IP_ADate &sign. ADate <=  max(IP_ADate,IP_DDate) then do;
						pdx='X';
    	    			EncType='IP'; 						
					end;
```

**Evidence — event/care-setting match.** `ms_caresettingprincipal.sas` token grammar on the PARAMETER string (lines 44-46, 49-53, 57-59, 65-66): `'*'→'A'`, `'.'→'_'`; `AAA`/empty value → `EncType="**"; PDX="*";` per token `EncType=substr(scan,1,2)` (`AA→"**"`), `PDX=substr(scan,3,1)`; then `if PDX="_" then PDX=" ";` and `if PDX="A" then PDX="*";` — i.e. a missing principal position becomes a BLANK Pdx token, never 'X'. The claim join is `ms_loopmeds.sas:56-59`:
```
              ((codes.lkp_enctype = claim.EncType and codes.lkp_pdx = claim.Pdx) or  /*No Wilcards*/
               (codes.lkp_enctype = "**"          and codes.lkp_pdx = claim.Pdx) or  /*Any EncType*/
               (codes.lkp_enctype = claim.EncType and codes.lkp_pdx = "*") or        /*Any Pdx*/
               (codes.lkp_enctype = "**"          and codes.lkp_pdx = "*"));
```
A blank claim Pdx equals a blank `lkp_pdx` (SAS character equality) and satisfies the `lkp_pdx="*"` branches (claim side unconstrained); it never equals 'X'.

**Evidence — codedistribution path.** `ms_expand_enctype_pdx.sas:59-67` expands DX `PDX='*'` into `'P','S','X'` **plus a blank row** (`PDX=''; output;`), and `:68-75` emits PX as `PDX='X'` (when `run_envelope ne 2`) **and** `PDX=''`. No blank→'X' coercion exists; the match is an exact merge, `ms_codedistribution.sas:163-165`:
```
				%indexcode_param (codecat = RX, type = rx, mergevars = stockgroup codecat);
				%indexcode_param (codecat = PX", "DX, type = meds, mergevars = codecat codetype enctype pdx code);
```
with `if indexcds and params then output;` (`:154-160`).

**Port must.** Keep blank Pdx as blank through extraction; emit the 'X' recode only for claims inside an inpatient stay envelope (when enabled); match blank Pdx against blank parameter tokens and wildcard-any rows, and against the blank expansion rows ('' entries) in the codedistribution exact-key merge.

---

## Q4 (DB-9) — NumIS count column

**Answer.** Definitive YES — SAS emits a NumIS column: `AV OA IP IS ED` is the care-setting list and per-setting counts `Num&enc` (hence NumIS) are produced by `%ms_util` and consumed as `NumIS`/`mean_NumIS`/`std_NumIS` in the baseline distribution table.

**Evidence.**

`ms_computeutilization.sas:62`: `    %let listofcaresettings = AV OA IP IS ED;`
`ms_computeutilization.sas:176-188`: `%ms_util(... CARESETTINGS=&listofcaresettings., BYENC=Y, ...)`.

`ms_util.sas:190-201` — one output column per care setting:
```
    create table outputBYENC as 
    select PatId,
           &INDEXDT.,
           %do i = 1 %to &EncCnt. ;
             %let enc = %scan(&CARESETTINGS.,&i.) ;
             sum(NumVisits_&enc.) as Num&enc. ...
```
(visit-level flags at `ms_util.sas:112-116`: `NumVisits_%scan(...) = (EncType eq "&enc.")` and `if indexw("&CARESETTINGS.",EncType) then output;`).

Baseline table consumption — `ms_createdistbaselinetable.sas:315`:
```
								NumAV NumOA NumIP NumIS NumED NumGeneric NumClass NumRx
```
`:429` (`var Age ... NumAV NumOA NumIP NumIS NumED ...`), `:442` (`mean_NumAV mean_NumOA mean_NumIP mean_NumIS mean_NumED`), `:454` (`std_NumAV std_NumOA std_NumIP std_NumIS std_NumED`).

**Port must.** Emit a NumIS count column (non-acute institutional stays) alongside NumAV/NumOA/NumIP/NumED, counted from encounters with EnType='IS' inside the utilization window, and carry it into the baseline distribution table statistics.

---

## Q5 (DB-10) — missing/blank EncType

**Answer.** In the event/outcome match a blank claim EncType DOES match the `'**'` wildcard (the wildcard is compared on the parameter side, the claim side is unconstrained), while in the code distribution the `'**'` parameter is first expanded to the explicit literals `IP IS ED AV OA`, and the exact-key merge then fails to match a blank-EnType claim.

**Evidence — event path.** `ms_loopmeds.sas:57`:
```
               (codes.lkp_enctype = "**"          and codes.lkp_pdx = claim.Pdx) or  /*Any EncType*/
```
`claim.EncType` never appears in this branch, so blank matches. (Also `ms_caresettingprincipal.sas:49-53`: an empty parameter value expands to `EncType="**"; PDX="*";`.)

**Evidence — codedistribution path.** `ms_expand_enctype_pdx.sas:43-52`:
```
    if EncType = "**" then do;
      do i = 1 to 5;
        enctype = scan('IP IS ED AV OA', i);
        output;
      end;
```
(a non-wildcard, including blank, passes through unchanged, lines 50-52). Match is exact via the merge keys `codecat codetype enctype pdx code` (`ms_codedistribution.sas:154-160`), so a blank claim EnType matches only parameter rows whose EnType is itself blank.

**Port must.** In the claims extraction keep the four-branch OR so blank EnType matches a `**` parameter; in the code distribution expand `**` to IP/IS/ED/AV/OA and join on exact EnType so blank-EnType claims only match blank-EnType parameters.

---

## Q6 (DB-2) — inclusion window construction (`excl_incl=Y`)

**Answer.** Inclusion subconditions build per-claim windows `EligStart = ADate - coalesce(CondTo,99999)` and `EligEnd = Expiredt - coalesce(CondFrom,-99999)` (or `ADate - coalesce(CondFrom,-99999)` when DATEONLY='Y'), require `codedays` distinct-ADate windows to overlap via a self-join, clip the denominator enrollment into those windows with `%ms_shaveoutside(KeepPartBf=Y)`, evaluate MINRXDAYS upstream as total overlapped days-in-window ≥ minrxdays, apply the INCLUSION-restricted stack first and the EXCLUSION-shaved stack second, INTERSECT across subconditions within a condition and UNION across conditions.

**Evidence — window formulas** (`ms_cidadenom.sas:212-219`, SUBINCLUSION=1):
```
                            EligStart=ADate-coalesce(CondTo,99999);
                            if dateonly = 'N' then do;
                                EligEnd=Expiredt-coalesce(CondFrom,-99999);
                            end;
                            else do;
                                EligEnd=ADate-coalesce(CondFrom,-99999);
                            end;
                            if EligStart<=EligEnd;
```
Exclusions are symmetric (`ms_cidadenom.sas:303-310`: `UneligStart=ADate-coalesce(CondTo,99999);` with the same dateonly split on `UneligEnd`).

**Evidence — codedays inside the window** (`ms_cidadenom.sas:220-247`): an `id` is assigned per (PatId, distinct ADate) (`lag_adate = lag(adate); ... else if lag_adate ne adate then id+1;`), then a `codedays`-way self-join takes
```
                                        max(a1.eligstart ... a&ref..eligstart) as overlap_start
                                        min(a1.eligEnd   ... a&ref..eligEnd)   as overlap_end
                                        ... where a1.Patid = a&ref..PatId and (a1.id < a&ref..id ...)
```
i.e. the common overlap of `codedays` windows from distinct dispensing dates; collapsed with `%MergeTimePeriods` (`:249-251`). (Numerator side, `ms_createpov3.sas:408-412`: `output out=_POV3Incl_tmp(... where=(freq>=&codedays)) n=freq;` counts records per (patid, Adate).)

**Evidence — DATEONLY** (`ms_cidadenom.sas:213-218` above; also `ms_createpov3.sas:151-152`: dateonly='Y' compares the CondFrom–CondTo window to `Inc.ADate` only, ignoring Expiredt).

**Evidence — MINRXDAYS** (upstream, `ms_createpov3.sas`): claim days are clipped into the index-anchored window, `:129-133`:
```
	                   , max(inc.adate, index.Adate+CondFrom) as minrxadate length=4
	                   , min(inc.expiredt, index.Adate+CondTo) as minrxexpiredt length=4
```
then `:374-393`:
```
                                       sum(minrxexpiredt - minrxadate + 1) as sumrxdays  /*Total days in window*/
                                ...
                                having sumrxdays>=&minrxdays.
```
with overlapping-dispensing episode dedup when >1 stockgroup (`:344-371`). A subcondition passes only when ALL its subconds are met — `ms_createpov3.sas:462-468`: `hasincl_&cond.=sum(of SubCond_&COND._1-SubCond_&COND._&MAXSUBCOND.)=&MAXSUBCOND.;`.

**Evidence — shave mechanism** (`ms_cidadenom.sas:253-261`):
```
                         %ms_shaveoutside(reffile=_overlap_spans_&subcond.,
                                          refstart=EligStart,
                                          refend=EligEnd,  
                                          KeepPartBf=Y,
                                          ToShaveFile=_Denom&Group._&cond.,
                                          ToShaveStart=DenomEnrStartDt,
                                          ToShaveEnd=DenomEnrEndDt,
                                          shaverx=N,
                                          outfile=_Denom&Group._&cond.);
```
`ms_shaveoutside.sas:79-91` semantics: each enrollment period overlapping a window is CLIPPED into the window; `KeepPartBf=N` would DELETE a period starting before the window start, `KeepPartBf=Y` keeps it (clipped to `EligStart`).

**Evidence — merge order and union/intersect** (`ms_cidadenom.sas`): per condition, inclusion subconds progressively clip `_Denom&Group._&cond.` (so WITHIN a condition, multiple inclusion subconditions INTERSECT; exclusion subconds are removed via `%ms_shaveinside`, `:347-354`). Per condition the result is appended to `_Denom&Group._STACK_INC` (`:382-386`) or `_STACK_EXCL` (`:388-392`). Then order is: INCLUSION first — `:400-424` merges STACK_INC periods (`%MergeTimePeriods`, union/merge of overlapping periods) and `%ms_shaveoutside(... ToShaveFile=worktemp._denom&Group. ...)` keeps only enrollment time inside the merged inclusion windows ("`_Denom&Group._STACK_INC includes all time periods where the potential index dates will meet at least one of the cond inclusions`", `:406` — UNION across conditions); EXCLUSION second — `:426-449` merges STACK_EXCL and `%ms_shaveinside` removes those windows from the already inclusion-restricted time; finally `:451-453` dedupes by `patid DenomEnrStartDt DenomEnrEndDt eligepisode`.

**Port must.** Implement inclusion windows as claim-span ∩ [index+CondFrom, index+CondTo] with the exact dateonly variants, codedays as distinct-dispensing-date overlap (denominator) / per-index-date record count (numerator), minrxdays as summed clipped days ≥ threshold, KeepPartBf=Y clipping for the inclusion shave, intersect across subconditions within a condition, union inclusion conditions, then subtract merged exclusion windows, in that order.

---

## Q7 (DB-2) — dose-denominator restriction (`%ms_dose_denom`)

**Answer.** `%ms_dose_denom` restricts TIME WINDOWS, not members: it computes, per member-day of (potential) eligibility inside the CondFrom–CondTo lookback, the prorated cumulative dose / average filled daily dose, and outputs only the contiguous member-day spans (`eligcumdosestart`/`eligcumdoseend`) that meet the thresholds — the member is never dropped, only day-spans are.

**Evidence.** Inputs (`ms_dose_denom.sas:53-60`): `_inclexcl` claims filtered to the subcondition with `cumdose`/`cfdd numdispensing`; eligibility bounds collapsed to min start / max end per patid (`:65-73`). One row per eligible day (`:77-92`):
```
	       eligstartdt = adate - condto;
           if dateonly = 'N' then eligenddt = expiredt - condfrom;
           else eligenddt = adate - condfrom;
            do date = eligstartdt to eligenddt;
                if denomenrstartdt <= date <= denomenrenddt then output;
            end;
```
Claims counted per potential index date if their span overlaps `[date+CondFrom, date+CondTo]` (`:145-151`), prorated at the window edges (`:158-170`: `ToDeductBf=(date+condfrom)-claimadate; ... cumdose=cumdose*(rxsup-sum(ToDeductBf,ToDeductAf))/rxsup;`), summed per (patid, date) and filtered (`:173-182`):
```
				 where=(1=1 and round(cumdose,1)>=&subcumdose.
				        and round((cfdd/numdispensing),1) >= &subminafdd.
				        and round((cfdd/numdispensing),1) <= &submaxafdd.)
```
Qualifying days collapsed into contiguous spans `eligcumdosestart/eligcumdoseend` (`:185-204`) → `_inclexcl_cumdose`, which `ms_cidadenom.sas` then shaves into the denominator: `%ms_shaveoutside(reffile=_inclexcl_cumdose, refstart=eligcumdosestart, refend=eligcumdoseend, KeepPartBf=Y, ...)` for inclusions (`:272-280`) and `%ms_shaveinside` for exclusions (`:366-376`). Call sites `:265-270` and `:359-364`, gated by `%if %sysevalf(&subcumdose. > 0) | %sysevalf(&subminafdd. > 0) | %sysevalf(&submaxafdd. > 0)`.

**Port must.** Implement dose restriction as a per-member-day eligibility mask (dose prorated into each potential index date's lookback window), collapse qualifying days into spans, and intersect/remove those spans from the denominator periods — no member-level exclusion.

---

## Q8 (DB-5) — masterlist truncation, flags, subsetting IF

**Answer.** The maxcumdose truncation at `ms_createptsmasterlist.sas:124` happens BEFORE the flag computations, so MinEnrolMet3, MinObsEnrolMet2, MinObsEnrolMet4 and MinEpiDurMet are all computed on the POST-truncation EpisodeEndDt; the subsetting IF at `:185` drops every episode failing any of the eight enrollment/episode/observation flags.

**Evidence — truncation** (`ms_createptsmasterlist.sas:122-125`; earlier truncations of the same variable: censordate `:114`, IndexLookEndDt `:119`, DeathDt `:133`, FUT `:139`, enr_end `:143`):
```
		**Adjust EpisodeEndDt when MAXCUMDOSE;
		%if &maxcumdose. ne . %then %do;
			if missing(censordate_maxdose)=0 and EpisodeEndDt > censordate_maxdose then EpisodeEndDt = censordate_maxdose;
		%end;
```

**Evidence — flags on post-truncation EpisodeEndDt:**
```
156:        if dataavail_dt >= EpisodeEndDt + max(0, &reqdaysaftepi.) then MinEnrolMet3=1;
160:        if Enr_Start <= EpisodeEndDt-max(0,-1*&obs_from_epiend.) then MinObsEnrolMet2=1;
162:        if dataavail_dt >= EpisodeEndDt + max(0, &obs_to_epiend.) then MinObsEnrolMet4=1;
165:        if EpisodeEndDt-IndexDt + 1 >= &minepisdur. then MinEpiDurMet=1;
```
(MinEnrolMet `:146` and MinEnrolMet2 `:153` use IndexDt / dataavail_dt; MinObsEnrolMet1 `:159` and MinObsEnrolMet3 `:161` use IndexDt / dataavail_dt. A further truncation at `:168` — maximum episode duration — runs AFTER the flags and is not fed back into them.)

**Evidence — subsetting IF** (`ms_createptsmasterlist.sas:185`):
```
        if MinEnrolMet and MinEnrolMet2 and MinEnrolMet3 and FUEnrolMet and MinEpiDurMet and MinObsEnrolMet1 and MinObsEnrolMet2 and MinObsEnrolMet3 and MinObsEnrolMet4;
```
In SAS a subsetting IF keeps only rows where the expression is true AND non-missing, so this drops any episode for which any of: MinEnrolMet (lookback enrollment), MinEnrolMet2 (post-index data availability), MinEnrolMet3 (post-episode-END data availability), FUEnrolMet (enrolled through minimum episode duration; forced to 1 when minepisdur≤0, `:171-174`), MinEpiDurMet, MinObsEnrolMet1–4 (secondary observation window) is 0/missing — followed by the defensive `if IndexDt > EpisodeEndDt then delete;` (`:188`).

**Order confirmed.** Yes: all EpisodeEndDt truncations listed above (including maxcumdose at `:124`) precede the flag computations at `:146-165`; only the max-episode-duration re-truncation (`:168`) comes after, without recomputing flags.

**Port must.** Apply censoring/truncations (censordate, IndexLookEndDt, censordate_maxdose, death, FUT, enr_end) to EpisodeEndDt first, then evaluate MinEnrolMet3 / MinObsEnrolMet2 / MinObsEnrolMet4 / MinEpiDurMet on the truncated value, require all eight flags truthy AND-set (missing = fail), and apply max-episode-duration truncation last; drop episodes failing any flag or with IndexDt > EpisodeEndDt.
