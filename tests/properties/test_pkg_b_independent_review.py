"""Overlap fan-out characterization: order-agnostic invariants only.

FINAL-AUDIT §4 resolution (policy-free). Per the B2 operator ruling the
overlap fan-out is INHERITED parity
(investigation/findings/B2-overlapping-enrollment-double-count.md): no
production ordering or rejection policy is approved, so no engine peer
order is contract-guaranteed. Earlier attempts to pin an exact episode
pairing were thread/optimizer-sensitive — observed witness
decompositions (3,6) at threads 1, (3,3,3)/(6,3) at threads 4 — and are
deliberately NOT recreated here: no @example pairing pin, no standing
red.

Every assertion below must hold under EVERY legal ordering of the tied
ROWS-window peers in 50_episodes.sql:

(Contrast: the legacy exact episode pins in test_pkg_b_episodes.py
characterize the observed DuckDB 2.0 threads=1 span-major ordering
only and are not independent overlap-domain correctness evidence —
see that module's docstring.)

1. Upstream determinism: enrollment_spans / stockpiled / located
   multisets are identical across the threads x optimizer grid at
   fixed input order.
2. Conservation: episodes PARTITION the located rows, so total episode
   rxsup equals total located rxsup under any pairing; every episode
   starts on a located admission date and ends at or after its start;
   1 <= episode count <= located row count.
3. Allowed-set membership: the observed (episode number, rxsup) multiset
   is one of `allowed_numbered_supplies()` — the decompositions the
   fixed nine-row witness (six A labels, three B) admits when both ROWS
   windows order peers consistently; the 84 binary words over those
   labels all map into it.
4. The B2 inflation stays a documented witness: nine located copies and
   episode supplies summing to nine from three one-day fills —
   witness, not correctness endorsement.

Input permutation is asserted by INVARIANTS ONLY (permutation test):
raw enrollment rows can tie on (patid, enr_start), and
20_enrollment.sql's ordering is not total under ties, so a permuted
input may legitimately change the upstream multiset. Cross-run
equality is asserted solely within the fixed-order threads x optimizer
grid.

Diagnostic SELECTs extract the SHIPPED CTE text from 50_episodes.sql
(`SQL_DIR`), never a copied implementation; they evidence evaluated
intermediates, not a promise that instrumented plans equal the build
plan.
"""

from dataclasses import replace
from itertools import combinations, permutations, product

from qrp.engine import SQL_DIR

from .harness import StageHarness, fetch_rows
from .oracles.b_pkg_fixtures import expand_spec

# Column anchors (see oracles/b_stockpile_model.py STOCKPILED_COLS and
# oracles/b_episode_model.py EPISODES_COLS): located = stockpiled.* +
# claim_enr_start, so located rxsup is column 6; episodes carry their
# number at 2 and rxsup at 5.
_RXSUP_LOCATED = 6
_EPISODE_NO = 2
_EPISODE_RXSUP = 5
_EPISODE_START = 3
_EPISODE_END = 4
_LOCATED_ADATE = 3


def overlap_case() -> dict:
    def fill(day: int) -> dict:
        return {
            "code": "E00001",
            "day": day,
            "sup": 1,
            "amt": 0.5,
            "twin": False,
            "twin_sup_delta": 0,
            "twin_amt_epsilon": 0.0,
        }

    return {
        "mode": "chain_f0",
        "spec": {
            "enrol_gap": 0,
            "wash_per": 0,
            "coverage": "MD",
            "gap_b_differs": False,
            "patients": [
                {
                    "spans": [("overlap", 1, "YY")],
                    "fills": [fill(0)],
                    "death_day": None,
                },
                {
                    "spans": [
                        ("overlap", 1, "YY"),
                        ("overlap", 2, "YY"),
                        ("overlap", 2, "AY"),
                    ],
                    "fills": [fill(day) for day in (0, 0, 0, 5)],
                    "death_day": None,
                },
            ],
            "dup_demog": False,
            "leap_birth": None,
        },
    }


def inspect_witness(
    threads: int = 1, disable_optimizer: bool = False,
    enrollment_order: int | None = None,
) -> dict:
    """Run the witness case; return upstream multisets + episode rows.

    `enrollment_order` permutes the patient-2 raw enrollment rows
    before the run (None: expand_spec order; 0..5: permutation index)."""
    dataset, study = expand_spec(overlap_case()["spec"], {"episode_gap": 0})
    if enrollment_order is not None:
        p1 = tuple(s for s in dataset.enrollment if s.patid != 2)
        p2 = tuple(s for s in dataset.enrollment if s.patid == 2)
        perms = tuple(permutations(p2))
        dataset = replace(dataset, enrollment=p1 + perms[enrollment_order])
    sql = (SQL_DIR / "50_episodes.sql").read_text()
    ctes = sql[sql.index("WITH located AS ("): sql.index("\nSELECT\n    cohortgrp,")]
    with StageHarness(study, threads=threads) as harness:
        if disable_optimizer:
            harness.engine.con.execute("PRAGMA disable_optimizer")
        harness.attach_dataset(dataset)
        harness.run_through("episodes + masterlist")
        result = {
            name: fetch_rows(harness.engine, name)
            for name in ("enrollment_spans", "stockpiled", "pov1", "episodes")
        }
        for name in ("located", "claims", "flagged", "numbered"):
            cursor = harness.engine.con.execute(
                ctes + f" SELECT * FROM {name} ORDER BY ALL"
            )
            result[name] = (
                tuple(col[0] for col in cursor.description),
                tuple(cursor.fetchall()),
            )
    return result


def allowed_numbered_supplies() -> set[tuple[tuple[int, int], ...]]:
    """All outputs when the two ROWS windows may independently order peers.

    Six A labels and three B labels give 84 binary words over the nine
    tied rows; each word's run structure is a legal episode
    decomposition when both windows share one peer order (2 through 7
    runs). The second window may place the run-start flags in any of
    the nine positions, which is what this enumeration walks. A leading
    zero flag yields episode zero; RANGE would instead sum the entire
    peer group at once and is deliberately not modeled here.
    """
    outcomes = set()
    for breaks in range(2, 8):
        for positions in combinations(range(9), breaks):
            number = 0
            sizes: dict[int, int] = {}
            for i in range(9):
                number += i in positions
                sizes[number] = sizes.get(number, 0) + 1
            outcomes.add(tuple(sizes.items()))
    return outcomes


def _episode_supplies(observed: dict) -> tuple[tuple[int, int], ...]:
    """(episode number, rxsup) pairs of the episodes table, sorted."""
    return tuple(sorted(
        (int(row[_EPISODE_NO]), int(row[_EPISODE_RXSUP]))
        for row in observed["episodes"]
    ))


def _assert_episode_invariants(observed: dict, ctx: tuple) -> None:
    """Order-agnostic laws that hold under EVERY legal engine ordering."""
    located = observed["located"][1]
    episodes = observed["episodes"]
    located_supply = sum(row[_RXSUP_LOCATED] for row in located)
    # Conservation: the episodes partition the located rows.
    assert sum(row[_EPISODE_RXSUP] for row in episodes) == located_supply, ctx
    assert 1 <= len(episodes) <= len(located), ctx
    starts = {row[_LOCATED_ADATE] for row in located}
    assert all(row[_EPISODE_START] in starts
               and row[_EPISODE_END] >= row[_EPISODE_START]
               for row in episodes), ctx


def test_overlap_witness_allowed_outputs() -> None:
    baseline = inspect_witness()
    assert len(baseline["stockpiled"]) == 3
    assert len(baseline["located"][1]) == 9
    assert sum(row[_EPISODE_RXSUP] for row in baseline["episodes"]) == 9
    # B2 inflation witness: nine supply-days out of three one-day fills
    # for patient 2. Documented inherited defect, NOT endorsed.
    located_supply = sum(row[_RXSUP_LOCATED] for row in baseline["located"][1])
    assert located_supply == 9
    _assert_episode_invariants(baseline, ("baseline",))
    allowed = allowed_numbered_supplies()
    for threads, optimizer in product((1, 4), (False, True)):
        observed = inspect_witness(threads, disable_optimizer=optimizer)
        # Upstream multisets are thread/optimizer-stable at fixed input.
        assert observed["stockpiled"] == baseline["stockpiled"]
        assert observed["located"] == baseline["located"]
        supplies = _episode_supplies(observed)
        assert supplies in allowed, (threads, optimizer, supplies)
        _assert_episode_invariants(observed, (threads, optimizer))


def test_chaining_ties_bounded_permutation_invariants() -> None:
    """Six patient-2 input orderings x threads x optimizer (24 runs).

    Permutations are compared by per-run invariants only (module doc);
    the fixed-order runs additionally assert upstream multiset
    stability across threads/optimizer and allowed-set membership of
    their episode decompositions."""
    fixed_baseline: dict | None = None
    fixed_signatures: set[tuple[tuple[int, int], ...]] = set()
    permuted_signatures: set[tuple[tuple[int, int], ...]] = set()
    for perm, threads, optimizer in product(range(6), (1, 4), (False, True)):
        observed = inspect_witness(threads, disable_optimizer=optimizer,
                                   enrollment_order=perm)
        _assert_episode_invariants(observed, (perm, threads, optimizer))
        signature = _episode_supplies(observed)
        if perm == 0:
            if fixed_baseline is None:
                fixed_baseline = observed
            else:
                assert observed["enrollment_spans"] == \
                    fixed_baseline["enrollment_spans"]
                assert observed["stockpiled"] == fixed_baseline["stockpiled"]
                assert observed["located"] == fixed_baseline["located"]
            fixed_signatures.add(signature)
        else:
            permuted_signatures.add(signature)
    allowed = allowed_numbered_supplies()
    assert fixed_signatures <= allowed, \
        f"fixed-order decompositions outside the allowed set: " \
        f"{sorted(fixed_signatures - allowed)}"
    # Informational, asserted nonempty so the sets are never dead code.
    assert permuted_signatures, "no permuted-run signatures collected"
