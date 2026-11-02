# Types-final — the campaign's last three mypy errors

Worker: types-final (`wt/types-final`, base 3709ab2).
Scope: `tests/properties/harness.py`,
`tests/properties/test_property_builder_events.py`, and this doc. No
production (`src/`) changes. No `# type: ignore`, no `cast`, no `Any`
plugged into a hole — each fix states the actual type. Semantics
preserved exactly.

## The errors

Reproduce with the campaign interpreter:

    /home/scarndp/.local/share/polytoken/sessions-v1/0f462p-motto/venv-campaign/bin/python -m mypy src tests/properties

Three errors in two files (83 checked):

    tests/properties/harness.py:262: error: Incompatible types in
    assignment (expression has type "tuple[str, str, str]", variable has
    type "tuple[str] | tuple[str, str, str, str] | tuple[str, str]")
    [assignment]
    tests/properties/harness.py:262: error: Incompatible types in
    assignment (expression has type "tuple[str, str, ... 13 total ...]",
    variable has type "tuple[str] | tuple[str, str, str, str] |
    tuple[str, str]")  [assignment]
    tests/properties/test_property_builder_events.py:251: error: Value
    of type "tuple[Any, ...] | None" is not indexable  [index]

Attribution:

* The harness pair came in with 7f5f67d ("complete A4 optional columns
  with integration compatibility") — the `_optional` helper and the two
  `optional_cols` loops that populate the `opt_*` fmt keys.
* The builder-events error came in with c8f897d ("Builder studies carry
  the loader's event care-setting rows"), which added
  `test_property_builder_events.py` wholesale, including the
  `cfg_care_setting` count assertion.

## Root cause, harness.py:262 (both errors)

mypy binds the loop variable `cols` from its first appearance. The first
loop's literal holds three different fixed-length column tuples, so mypy
joins them and pins `cols` to

    tuple[str] | tuple[str, str, str, str] | tuple[str, str]

The second loop (per-table `opt_*` keys) feeds `cols` a 3-tuple
(`dx_codetype, pdx, enctype`) and a 13-tuple (`lab_result`'s optional
columns). Neither length is in the pinned union, so each of those two
literal elements fails the assignment — both reported at the second
loop's header line. The code was never wrong; only the inferred binding
was too narrow for the two loops it has to serve.

## Root cause, test_property_builder_events.py:251

DuckDB's `fetchone()` is typed `tuple[Any, ...] | None`. The assertion
indexed the result directly, so mypy correctly refused to index the
`None` arm. Nothing deeper: the query is a `SELECT count(*)` and always
returns one row.

## The fixes

harness.py (`_prepare_fmt`, one declaration + a comment):

    cols: tuple[str, ...]
    for key, cols in (
        ...
    for table, cols in (
        ...

`tuple[str, ...]` is the real type of every element in both literals —
each fixed-length tuple is assignable to the variadic tuple — so both
loops check against the actual type instead of the accidental
first-loop union. No ignore, no cast, no runtime change: the annotation
is invisible to the interpreter.

test_property_builder_events.py:

    n_settings = harness.engine.con.execute(
        "SELECT count(*) FROM cfg_care_setting").fetchone()
    assert n_settings is not None and int(n_settings[0]) == 4

This mirrors the shape `test_harness_stages.py` already uses for its
`event_claims` count (its `assert n_claims is not None and
int(n_claims[0]) == 4`). `is not None` is genuine narrowing, not a
plaster: if `fetchone()` ever returned None the test still fails — with
a sharper assertion message than the old IndexError.

## Verification

* mypy: `Success: no issues found in 83 source files`. The only
  remaining output is the pre-existing `[annotation-unchecked]` notes in
  `tests/properties/oracles/d_stages.py`, untouched by this work.
* Targeted tests (campaign python, no full heavy suites):
  `test_property_builder_events.py`, `test_harness_stages.py`,
  `test_harness_pipeline.py`, `test_ac1_infrastructure.py`,
  `test_pkg_a_optional_columns.py` → **56 passed**.
* Lint subset: `ruff check` on both owned files reports one finding,
  PYI034 at `harness.py:179` (`__enter__` should return `Self`).
  Verified pre-existing: the same finding fires on the untouched HEAD
  copy (`git show HEAD:...` piped to ruff from /tmp). Not
  campaign-introduced, left alone to keep the diff narrow.
* `ruff format --check` wants to rewrite large, unrelated stretches of
  both files; the repo does not follow ruff-format style, so no
  reformat was applied.

## Boundaries held

Production (`src/`) untouched; no policy, network, publication, stash,
reset, or clean; no other worktree mutation; `uv.lock` untouched. The
commit contains exactly the two owned test-infrastructure files and
this document.
