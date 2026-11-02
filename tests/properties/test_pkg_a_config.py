"""Package A — config translation semantics and rejection boundaries.

Contract under test (AC.2): ``qrp.inputfile`` + ``qrp.config`` translate
a QRP study input file into a validated StudyConfig, per INPUTS.md.
The oracle is ``tests/properties/oracles/a_config_semantics.py``:
hand-authored expected semantics, written from INPUTS.md's documented
tables, never computed by production code.
"""

from __future__ import annotations

import dataclasses
import json
import random
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import example, event, given
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument

from qrp.config import StudyConfig, load_study, load_study_dict

from .oracles import a_config_semantics as fx


def _ev(label: str) -> None:
    """event() where Hypothesis allows it; a no-op in plain tests."""
    try:
        event(label)
    except InvalidArgument:
        pass

# ---------------------------------------------------------------------
# Reading a StudyConfig through dotted paths, against literal
# expectations. The reader is plumbing; every expected VALUE lives in
# the oracle module as a hand-written literal.
# ---------------------------------------------------------------------


def read_sem(study: StudyConfig, path: str) -> object:
    if path.startswith("cohort:"):
        rest = path[len("cohort:"):]
        grp, attr = rest.split(".", 1)
        matches = [c for c in study.cohorts if c.cohortgrp == grp]
        assert len(matches) == 1, f"cohort {grp!r} not uniquely present"
        if attr == "age_strata":
            return tuple(
                (s.ordinal, s.label, s.lo, s.hi, s.unit)
                for s in matches[0].age_strata.strata
            )
        return getattr(matches[0], attr)
    if path == "n_cohorts":
        return len(study.cohorts)
    if path == "stockpiling":
        return tuple(dataclasses.astuple(r) for r in study.stockpiling)
    if path == "covariates":
        # compact hand-readable encoding of the covariate list
        return "; ".join(
            f"{c.covarname}:from={c.covfrom},to={c.covto},"
            f"codes={len(c.codes)}"
            for c in study.covariates
        )
    return getattr(study, path)


def assert_semantics(study: StudyConfig, expect: dict[str, object]) -> None:
    for path, want in expect.items():
        got = read_sem(study, path)
        assert got == want, f"{path}: got {got!r}, contract says {want!r}"


# ---------------------------------------------------------------------
# Representation variants: the SAME meaning, different spellings. The
# variant builder is deterministic in (seed, axes) and applies only
# spellings INPUTS.md says are accepted.
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    dates_as: str            # "sasint" | "iso" | "digitstring"
    weird_case_keys: bool    # row keys and table names in odd casing
    pad_scalars: bool        # whitespace around parameter values
    indirect_tables: bool    # dataset-name keys + QRP_PARAMETERS mapping
    sex_as_string: bool      # '"f" "m"' instead of a list


def apply_variant(payload: dict, v: Variant, rng: random.Random
                  ) -> tuple[dict, dict | None]:
    """Re-spell the base payload per the variant. Semantics unchanged."""
    out = json.loads(json.dumps(payload))     # deep copy without sharing

    def wobble(key: str) -> str:
        if not v.weird_case_keys:
            return key
        return "".join(
            ch.upper() if rng.random() < 0.5 else ch.lower() for ch in key)

    if v.dates_as != "sasint":
        scalars = {r["parameter"]: r for r in out["QRP_PARAMETERS"]}
        for name, d in (("startdate", fx.BASE_PERIOD[0]),
                        ("enddate", fx.BASE_PERIOD[1]),
                        ("censordate", fx.BASE_PERIOD[2])):
            row = scalars[name]
            row["run1"] = (d.isoformat() if v.dates_as == "iso"
                           else str(fx.sas_day(d)))

    table_names = {
        "QRP_PARAMETERS": "QRP_PARAMETERS",   # loader keys off this name
    }
    for key in list(out):
        if key == "QRP_PARAMETERS":
            continue
        new_name = wobble(key)
        table_names[key] = new_name

    if v.indirect_tables:
        params = out["QRP_PARAMETERS"]
        for logical in ("cohortfile", "type2file", "cohortcodes"):
            actual = table_names.get(logical.upper(), table_names.get(logical))
            if actual is None:
                continue
            params.append({
                "parameter": ("  " + logical + " ") if v.pad_scalars
                else logical,
                "run1": actual,
            })
    if v.pad_scalars:
        for r in out["QRP_PARAMETERS"]:
            r["parameter"] = " " + str(r["parameter"]) + " "

    # SAS-quoted-string spelling for the code list
    if v.sex_as_string:
        for tbl in out.values():
            if not isinstance(tbl, list):
                continue
            for row in tbl:
                if isinstance(row, dict) and "Sex" in row:
                    row["Sex"] = '"f" "m"'

    renamed = {table_names.get(k, k): val for k, val in out.items()}
    return renamed, None


# ---------------------------------------------------------------------
# Property 1: translation semantics.
# ---------------------------------------------------------------------


def _load_from_file(payload: dict, lookup: dict | None, tmp_path: Path
                    ) -> StudyConfig:
    safe_name = "stüdy [1] 'q.json"     # quoting/special chars in the path
    path = tmp_path / safe_name
    path.write_text(json.dumps(payload, ensure_ascii=False))
    lookup_path = None
    if lookup is not None:
        lookup_path = tmp_path / "loo kup.json"
        lookup_path.write_text(json.dumps(lookup, ensure_ascii=False))
    return load_study(path, lookup_path)


class TestConfigTranslationSemantics:
    """Every authored case lands on its hand-written semantics."""

    @pytest.mark.parametrize(
        "name,payload,lookup,expect",
        [(n, p, lk, e) for n, p, lk, e in fx.SEMANTIC_CASES],
        ids=[n for n, _, _, _ in fx.SEMANTIC_CASES],
    )
    def test_authored_case(
        self,
        name: str,
        payload: dict,
        lookup: dict | None,
        expect: dict[str, object],
        tmp_path: Path,
    ) -> None:
        study = _load_from_file(payload, lookup, tmp_path)
        assert_semantics(study, expect)

    @given(
        dates_as=st.sampled_from(("sasint", "iso", "digitstring")),
        weird=st.booleans(),
        pad=st.booleans(),
        indirect=st.booleans(),
        sex_str=st.booleans(),
        seed=st.integers(0, 2 ** 31 - 1),
    )
    @example(dates_as="sasint", weird=False, pad=False, indirect=False,
             sex_str=False, seed=0)
    @example(dates_as="iso", weird=True, pad=True, indirect=True,
             sex_str=True, seed=1)
    @example(dates_as="digitstring", weird=True, pad=False, indirect=True,
             sex_str=False, seed=2)
    def test_representation_metamorphic(
        self,
        dates_as: str,
        weird: bool,
        pad: bool,
        indirect: bool,
        sex_str: bool,
        seed: int,
    ) -> None:
        """Different accepted spellings of one study must translate to
        the same hand-written semantics. Dates, indirection, casing,
        padding and code-list spelling are representation; the meaning
        is fixed by the oracle. Own tempdir: Hypothesis forbids pytest's
        function-scoped fixtures inside @given."""
        event(f"dates_as={dates_as}")
        for axis, on in (("weird_case", weird),
                         ("pad_scalars", pad),
                         ("indirect", indirect),
                         ("sex_string", sex_str)):
            if on:
                event(f"axis_{axis}")
        v = Variant(
            dates_as=dates_as,
            weird_case_keys=weird,
            pad_scalars=pad,
            indirect_tables=indirect,
            sex_as_string=sex_str,
        )
        payload, lookup = apply_variant(fx.base_payload(), v,
                                        random.Random(seed))
        with tempfile.TemporaryDirectory(prefix="qrp-a-config-") as tmp:
            study = _load_from_file(payload, lookup, Path(tmp))
        assert_semantics(study,
                         {k: fx.BASE_EXPECT[k] for k in fx.CORE_SEMANTICS_KEYS})

    @given(
        knobs=st.sets(st.sampled_from(fx.COINCIDENT_KNOBS),
                      min_size=1, max_size=len(fx.COINCIDENT_KNOBS)
                      ).map(lambda s: tuple(sorted(s))),
    )
    @example(knobs=("agestrat", "covar_point", "dup_code", "lab_eq",
                    "sup_eq", "wash_eq_enr"))
    @example(knobs=("dup_code",))
    @example(knobs=("lab_eq",))
    @example(knobs=("sup_eq",))
    @example(knobs=("wash_eq_enr",))
    def test_coincident_bound_semantics(self, knobs: tuple[str, ...]
                                        ) -> None:
        """min==max shapes pass 1 never generated: coincident agestrat
        bounds (65-65), wash == enr_days (the accepted edge of the
        documented rejection), one-day covariate windows (covfrom ==
        covto), lab ranges with lo == hi, stockpiling ranges with lo ==
        hi, and code lists carrying a duplicate code. Each knob's
        translation is asserted against its hand expectation; the
        events report the coincident-bound rates."""
        for k in knobs:
            event(f"coincident:{k}")
        payload = fx.apply_coincident(fx.base_payload(), knobs)
        with tempfile.TemporaryDirectory(prefix="qrp-a-coinc-") as tmp:
            study = _load_from_file(payload, None, Path(tmp))
        assert_semantics(study, fx.coincident_expect(knobs))


# ---------------------------------------------------------------------
# Property 2: rejection boundaries.
# ---------------------------------------------------------------------


class TestInvalidConfigBoundaries:
    """Near-valid variants: documented rejections vs incidental crashes,
    classified per case in the oracle module."""

    @pytest.mark.parametrize(
        "case",
        fx.BOUNDARY_CASES,
        ids=[c["name"] for c in fx.BOUNDARY_CASES],
    )
    def test_boundary(self, case: dict) -> None:
        kind = case["kind"]
        _ev(f"boundary:{case['name']}:{kind}")
        if kind == "documented":
            with pytest.raises(
                (ValueError, NotImplementedError), match=re.escape(
                    case["fragment"])
            ):
                load_study_dict(case["payload"])
        elif kind == "accepts":
            if case.get("warn_fragment"):
                with pytest.warns(UserWarning,
                                  match=re.escape(case["warn_fragment"])):
                    study = load_study_dict(case["payload"])
            else:
                study = load_study_dict(case["payload"])
            assert_semantics(study, case.get("expect", {}))
        else:  # incidental: characterized, typed; no message contract
            with pytest.raises(case["exc_type"]) as excinfo:
                load_study_dict(case["payload"])
            _ev(f"incidental:{case['name']}:{type(excinfo.value).__name__}")
