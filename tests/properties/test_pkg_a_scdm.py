"""Package A — SCDM schema handling: aliases, partitions, optional
columns, row permutation (AC.2).

Contract (INPUTS.md "SCDM data"): the physical form of the extract is
the site's choice — one folder/file/shard-set per table, any casing,
hive-style nesting, CSV or parquet, aliases or arbitrary names found by
required columns, optional columns present or absent (absent resolves
to NULL), and the dispensed-code column may be ``ndc`` instead of
``rx``. None of that may change a single output row: code identity and
row multiplicity are preserved, not normalized away.

Oracle: the same SyntheticDataset, serialized through the shared
``rows_for_table`` boundary, re-materialised under each layout by
``oracles/a_scdm_layouts.py``, then real end-to-end ``qrp.run`` runs
compared table by table.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from .conftest import expensive_settings
from .oracles import a_scdm_layouts as L
from .strategies import datasets
from .synth import (
    Dispensing,
    GenerateOptions,
    generate,
    rows_for_table,
    study_for,
    write_parquet,
)

VARIANTS = (
    # (kind, optionals, code_column, permute_seed); optionals="absent"
    # is compared against the matched NULL control (see below), the
    # rest against the untouched dataset
    ("standard", "present", "rx", None),
    ("sharded", "present", "rx", None),
    ("hive", "present", "rx", None),
    ("alias", "present", "rx", None),
    ("fingerprint", "present", "rx", None),
    ("mixed", "present", "rx", 7),
    ("standard", "absent", "rx", None),
    ("standard", "absent", "ndc", None),
)


def _prep(dataset):
    """No encounters: the variant set is about resolution, not the
    enveloping path; a table resolved from a *renamed* encounter file
    would not be found at all (fingerprint=False) and would change
    semantics rather than test them."""
    return dataclasses.replace(dataset, encounters=())


def _run_variant(root: Path, dataset, study, variant,
                 multiplicity_of=None) -> dict:
    kind, optionals, code_column, permute = variant
    L.write_variant(root / "indata", dataset, kind=kind,
                    optionals=optionals, code_column=code_column,
                    permute_seed=permute)
    with L.run_at(root / "indata", study) as result:
        if multiplicity_of is not None:
            _assert_multiplicity(multiplicity_of, result)
        return L.capture_outputs(result)


def _run_control(root: Path, dataset, study) -> dict:
    write_parquet(dataset, root / "control")
    with L.run_at(root / "control", study) as result:
        return L.capture_outputs(result)


def _assert_multiplicity(dataset, result) -> None:
    engine = result.engine
    patids = {r["patid"] for r in rows_for_table(dataset, "demographic")}
    dupes = set(dataset.duplicate_demographic_patids())
    n = engine.con.execute("SELECT count(*) FROM demographics").fetchone()
    assert int(n[0]) == len(patids), (
        "demographics must hold exactly one row per patid"
    )
    got = {r[0] for r in engine.con.execute(
        "SELECT patid FROM demographics_multi").fetchall()}
    assert got == dupes, (
        f"demographics_multi {sorted(got)} != duplicate patids {sorted(dupes)}"
    )
    leaked = {r[0] for r in engine.con.execute(
        "SELECT patid FROM pov1").fetchall()} & dupes
    assert not leaked, (
        "duplicate-demographics patids must be excluded from pov1 entirely"
    )
    codes = {r["rx"] for r in rows_for_table(dataset, "dispensing")}
    known = codes | set(dataset.exposure_codes) | set(dataset.noise_drug_codes)
    seen = {r[0] for r in engine.con.execute(
        "SELECT DISTINCT code FROM exposure_claims").fetchall()}
    assert seen <= known, f"exposure_claims invented codes: {seen - known}"


class TestScdmAliasAndPartitionEquivalence:
    @pytest.mark.parametrize("variant", VARIANTS, ids=[
        f"{v[0]}-{v[1]}-{v[2]}" for v in VARIANTS])
    def test_mandatory_variants(self, variant, tmp_path: Path) -> None:
        ds = _prep(generate(patients=2, seed=0))
        study = study_for(ds)
        kind, optionals, code_column, permute = variant
        if optionals in ("null", "absent"):
            # absent/NULL variants are compared against the NULL
            # control, not against the untouched dataset: dropping
            # `race` legitimately changes values (the 'M' default)
            control = _run_variant(tmp_path, ds, study,
                                   (kind, "null", code_column, permute))
        else:
            control = _run_control(tmp_path, ds, study)
        got = _run_variant(tmp_path, ds, study, variant)
        assert got == control, (
            f"layout {variant} changed pipeline outputs: "
            + "; ".join(f"{t}: {len(got[t])} vs {len(control[t])}"
                        for t in got if got[t] != control[t])
        )

    @expensive_settings()
    @given(ds=datasets(min_patients=2, max_patients=3, with_ties=True),
           variant=st.sampled_from(VARIANTS),
           permute=st.integers(0, 2 ** 31 - 1))
    @example(ds=generate(patients=2, seed=0), variant=VARIANTS[1],
             permute=0)
    @example(ds=generate(patients=3, seed=1), variant=VARIANTS[4],
             permute=1)
    def test_scdm_alias_and_partition_equivalence(
        self, ds, variant, permute
    ) -> None:
        # per-example tempdir: pytest fixtures are function-scoped, and
        # Hypothesis examples would share one tree — stale files from
        # an earlier example would pollute later resolution
        kind, optionals, code_column, _ = variant
        dataset = _prep(ds)
        study = study_for(dataset)
        with tempfile.TemporaryDirectory(prefix="qrp-a-scdm-") as tmp:
            root = Path(tmp)
            if optionals == "absent":
                # matched control: the same layout with the optional
                # columns present but NULL — documented to be equivalent
                control = _run_variant(
                    root, dataset, study,
                    (kind, "null", code_column, permute))
            else:
                control = _run_control(root, dataset, study)
            got = _run_variant(
                root, dataset, study,
                (kind, optionals, code_column, permute),
                multiplicity_of=dataset)
            for table, rows in control.items():
                assert got[table] == rows, (
                    f"layout kind={kind} optionals={optionals} "
                    f"code_column={code_column} changed {table}"
                )


class TestCodeIdentityPreserved:
    def test_case_is_not_normalized(self, tmp_path: Path) -> None:
        """Code identity: 'e00001' and 'E00001' are different codes.
        Nothing in the contract upcases code values, so a study naming
        E00001 must not capture a lowercase fill — and must still
        capture an uppercase one."""
        ds = _prep(generate(patients=2, seed=0, options=GenerateOptions(
            duplicate_demographics=True, same_supply_ties=False,
            include_deaths=False)))
        row = ds.dispensings[0]
        inject = (
            Dispensing(row.patid, row.rxdate, "E00001", row.rx_codetype,
                       row.rxsup, 3.0, row.providerid),
            Dispensing(row.patid, row.rxdate, "e00001", row.rx_codetype,
                       row.rxsup, 3.0, row.providerid),
        )
        dataset = dataclasses.replace(ds, dispensings=ds.dispensings + inject)
        study = study_for(dataset)
        write_parquet(dataset, tmp_path / "indata")
        with L.run_at(tmp_path / "indata", study) as result:
            upper = result.engine.con.execute(
                "SELECT count(*) FROM exposure_claims "
                "WHERE code = 'E00001'").fetchone()
            lower = result.engine.con.execute(
                "SELECT count(*) FROM exposure_claims "
                "WHERE code = 'e00001'").fetchone()
            raw = result.engine.con.execute(
                "SELECT count(*) FROM cdm_dispensing "
                "WHERE code = 'e00001'").fetchone()
        assert int(raw[0]) >= 1, "cdm_dispensing lost the lowercase row"
        assert int(upper[0]) >= 1, "uppercase exposure fill not captured"
        assert int(lower[0]) == 0, (
            "lowercase 'e00001' was case-normalized into exposure — "
            "code identity is NOT preserved"
        )


class TestDocumentedOptionalRxamt:
    @pytest.mark.xfail(
        strict=True,
        reason="A-2: INPUTS.md declares dispensing.rxamt optional "
               "(dose only), but 10_normalize.sql references it "
               "unconditionally, so a probe-compatible extract "
               "without the column dies with a BinderException",
    )
    def test_optional_rxamt_runs(self, tmp_path: Path) -> None:
        ds = _prep(generate(patients=2, seed=0, options=GenerateOptions(
            duplicate_demographics=True, same_supply_ties=True,
            include_deaths=False)))
        study = study_for(ds)
        L.write_variant(tmp_path / "indata", ds, kind="standard",
                        optionals="absent", drop=("rxamt",))
        with L.run_at(tmp_path / "indata", study) as result:
            assert L.capture_outputs(result)
