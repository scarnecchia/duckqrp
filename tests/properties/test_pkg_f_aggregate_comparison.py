"""Sensitivity checks for the narrowly scoped aggregate comparison."""
import math
from dataclasses import replace

import pytest
from hypothesis import event, given, strategies as st

from .test_pkg_f_meta_helpers import AggregateTable, aggregate_tables_equal


def table(value: object, column: str = "std_age") -> AggregateTable:
    return AggregateTable((("group", "VARCHAR"), (column, "DOUBLE")),
                          (("a", value),))


@given(steps=st.integers(0, 40), exponent=st.integers(-100, 100))
def test_aggregate_relative_threshold(steps: int, exponent: int) -> None:
    a = math.ldexp(1.0, exponent)
    b = a
    for _ in range(steps):
        b = math.nextafter(b, math.inf)
    # At powers of two, 18 ulps are below 4e-15 relative; 19 above.
    expected = steps <= 18
    event(f"threshold={'inside' if expected else 'outside'}")
    assert aggregate_tables_equal("baseline", table(a), table(b)) == expected


@pytest.mark.parametrize("steps,expected", [(17, True), (18, True), (19, False), (20, False)])
def test_adjacent_values_at_threshold(steps: int, expected: bool) -> None:
    value = 1.0
    for _ in range(steps):
        value = math.nextafter(value, math.inf)
    assert aggregate_tables_equal("baseline", table(1.0), table(value)) == expected


@pytest.mark.parametrize("a,b,equal", [
    (None, None, True), (None, float("nan"), False),
    (float("nan"), float("nan"), True), (float("nan"), 1.0, False),
    (0.0, math.ulp(0.0), False), (1.0, 1.001, False),
    (4.511922447625936, 4.511922447625941, True),
])
def test_aggregate_special_values(a: object, b: object, equal: bool) -> None:
    assert aggregate_tables_equal("baseline", table(a), table(b)) == equal


@pytest.mark.parametrize("column", ["n", "count", "flag", "code", "date", "rxamt"])
def test_nonaggregate_columns_remain_exact(column: str) -> None:
    assert not aggregate_tables_equal("baseline", table(1.0, column),
                                      table(math.nextafter(1.0, 2.0), column))


def test_aggregate_row_multiplicity_and_schema() -> None:
    a = table(1.0)
    duplicate = replace(a, rows=a.rows * 2)
    assert not aggregate_tables_equal("baseline", a, duplicate)
    assert aggregate_tables_equal("baseline", duplicate, duplicate)
    corrupt = replace(duplicate, rows=(a.rows[0], ("a", 2.0)))
    assert not aggregate_tables_equal("baseline", duplicate, corrupt)
    assert not aggregate_tables_equal("baseline", a,
                                      replace(a, schema=(("group", "VARCHAR"), ("std_age", "FLOAT"))))


def test_long_counts_and_stat_identity_remain_exact() -> None:
    schema = (("cohortgrp", "VARCHAR"), ("stat", "VARCHAR"),
              ("kind", "VARCHAR"), ("n", "BIGINT"), ("v", "DOUBLE"))
    a = AggregateTable(schema, (("a", "std_age", "stat", None, 1.0),))
    b = replace(a, rows=(("a", "std_age", "stat", None, math.nextafter(1.0, 2.0)),))
    assert aggregate_tables_equal("baseline_long", a, b)
    assert not aggregate_tables_equal("baseline_long", a,
                                      replace(b, rows=(("a", "mean_age", "stat", None, 1.0),)))
    count = replace(a, rows=(("a", "std_age", "count", 1, 1.0),))
    assert not aggregate_tables_equal("baseline_long", count,
                                      replace(count, rows=(("a", "std_age", "count", 1, math.nextafter(1.0, 2.0)),)))
