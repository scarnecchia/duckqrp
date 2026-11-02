"""Hypothesis strategies over the synthetic builder.

Small on purpose: every example here may run real SQL stages, so sizes
are bounded (a handful of patients, one study window) and the wall-clock
budget is the profile, not the input space.
"""

from __future__ import annotations

from hypothesis import strategies as st

from .synth import GenerateOptions, SyntheticDataset, generate

# Draw seeds from a compact domain: shrinking collapses to seed 0, the
# smallest dataset, which is exactly the witness we want on failure.
SEEDS = st.integers(min_value=0, max_value=2**31 - 1)


def generate_options(
    *,
    defaults: GenerateOptions | None = None,
) -> st.SearchStrategy[GenerateOptions]:
    """Boolean knobs for generate(). Defaults keep ties and encounters
    ON: a strategy that mostly turns them off would make every
    tie-breaking property vacuously pass."""
    base = defaults or GenerateOptions()
    return st.builds(
        GenerateOptions,
        duplicate_demographics=st.booleans()
        if base.duplicate_demographics else st.just(False),
        same_supply_ties=st.booleans()
        if base.same_supply_ties else st.just(False),
        include_deaths=st.booleans()
        if base.include_deaths else st.just(False),
        include_encounters=st.booleans()
        if base.include_encounters else st.just(False),
        include_labs=st.booleans()
        if base.include_labs else st.just(False),
    )


def datasets(
    *,
    min_patients: int = 1,
    max_patients: int = 5,
    with_ties: bool = True,
) -> st.SearchStrategy[SyntheticDataset]:
    """Synthetic SCDM extracts, small enough to run the pipeline on.

    Ties (duplicate demographics, same-supply dispensings) are ON by
    default at the GENERATOR level so tie-break stages are exercised;
    `with_ties=False` drops the tie knobs entirely.
    """
    return st.builds(
        generate,
        patients=st.integers(min_patients, max_patients),
        seed=SEEDS,
        options=(
            generate_options(defaults=GenerateOptions(
                duplicate_demographics=False,
                same_supply_ties=False,
            )) if not with_ties else generate_options()
        ),
    )
