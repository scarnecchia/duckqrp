"""Package A — SQL splitting execution equivalence (AC.2).

Contract: ``Engine.script_stage`` executes shipped .sql by splitting it
with ``qrp.sqlsplit.split_statements`` and running the statements one
at a time. The property pins the meaning of that split: for generated
programs with KNOWN statement boundaries, splitting + sequential
execution and DuckDB's own single-call multi-statement execution must
reach the SAME final database state — schemas, values, and row
multiplicities — in separate fresh connections. Multi-statement
execution returns only its final result, so per-statement returns are
never asserted.

The statement grammar (``oracles/a_sql_grammar.py``) embeds semicolons
inside strings, comments, and quoted identifiers, ends programs with
and without a trailing semicolon, and plants comment-only regions the
splitter is designed to drop.
"""

from __future__ import annotations

from collections import Counter

import duckdb
import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from qrp.sqlsplit import Statement, split_statements

from .conftest import expensive_settings
from .oracles import a_sql_grammar as g


def _ev(label: str) -> None:
    """event() where Hypothesis allows it; a no-op in plain tests."""
    try:
        event(label)
    except Exception:
        pass

# Inputs with NO contract behind them (dollar-quoted strings, BEGIN
# blocks). The splitter tracks only --, /* */, ', and " — these are
# characterized, never asserted correct.
UNSUPPORTED_INPUTS = (
    "SELECT $$dollar; quoted; text$$ AS v;",
    "BEGIN TRANSACTION; CREATE TABLE t (x INTEGER); COMMIT;",
    "CREATE MACRO m(a) AS CASE WHEN a THEN 1; ELSE 0 END;",
)


def _new_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("SET threads = 1")
    return con


def _assert_state(
    con: duckdb.DuckDBPyConnection,
    expected: dict[str, tuple[tuple[str, ...], list[tuple]]],
    tag: str,
) -> None:
    for table, (cols, rows) in expected.items():
        cur = con.execute(f"SELECT * FROM {table} LIMIT 0")
        got_cols = tuple(str(d[0]) for d in cur.description)
        assert got_cols == cols, f"[{tag}] {table} columns {got_cols} != {cols}"
        fetched = con.execute(
            f"SELECT * FROM {table} ORDER BY ALL").fetchall()
        got = Counter(tuple(r) for r in fetched)
        want = Counter(rows)
        assert got == want, (
            f"[{tag}] {table}: multiset mismatch "
            f"(got {sum(got.values())} rows, want {len(rows)}; "
            f"difference: {got - want} extra / {want - got} missing)"
        )
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert n is not None and int(n[0]) == len(rows), (
            f"[{tag}] {table}: count(*) {n} != {len(rows)}")


class TestSqlSplitExecutionEquivalence:
    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    @pytest.mark.parametrize("n", [2, 3, 5])
    def test_mandatory_shapes(self, seed: int, n: int) -> None:
        """Deterministic coverage: the interaction of the tail push,
        comment-only regions, escaped quotes before semicolons, and
        every statement kind must not be left to chance."""
        self._check_program(seed, n)

    @expensive_settings()
    @given(seed=st.integers(0, 2 ** 31 - 1), n=st.integers(1, 6))
    def test_sql_split_execution_equivalence(self, seed: int, n: int
                                             ) -> None:
        self._check_program(seed, n)

    # ---- shared body ------------------------------------------------

    def _check_program(self, seed: int, n: int) -> None:
        program = g.build_program(seed, n)
        for h in program.hazards:
            _ev(f"grammar:{h}")
        expected = g.replay(program.plans)

        stmts = split_statements(program.text)
        got_shapes = [(s.kind, s.target) for s in stmts]
        assert got_shapes == program.expected_statements(), (
            f"split produced {got_shapes}, grammar authored "
            f"{program.expected_statements()}"
        )

        con_seq = _new_con()
        try:
            for s in stmts:
                con_seq.execute(s.sql)
            con_multi = _new_con()
            try:
                # one call, DuckDB's own boundaries; only the FINAL
                # result comes back and it is consumed so the
                # connection is free again
                con_multi.execute(program.text).fetchall()
                _assert_state(con_seq, expected, "split")
                _assert_state(con_multi, expected, "multi")
                for view in program.view_sources:
                    for con, tag in ((con_seq, "split"),
                                     (con_multi, "multi")):
                        cur = con.execute(
                            f"SELECT * FROM {view} LIMIT 0")
                        got_cols = tuple(str(d[0]) for d in cur.description)
                        assert got_cols == ("id", "amt"), (
                            f"[{tag}] {view} columns {got_cols}")
                        rows = con.execute(
                            f"SELECT * FROM {view} ORDER BY ALL"
                        ).fetchall()
                        want = Counter(g.projected(program, view))
                        assert Counter(tuple(r) for r in rows) == want, (
                            f"[{tag}] {view} contents differ"
                        )
            finally:
                con_multi.close()
        finally:
            con_seq.close()

    def test_empty_and_degenerate_inputs(self) -> None:
        assert split_statements("") == []
        assert split_statements("   \n\t ") == []
        assert split_statements(";;;") == []
        assert split_statements("-- only; a comment\n") == []
        assert split_statements("/* ; */ /* ' */ ") == []
        tail = split_statements("SELECT 1")
        assert [s.kind for s in tail] == [None]     # the tail push


class TestSqlSplitTextIsVerbatim:
    """Structural oracle for ANY input: every returned statement is a
    verbatim, in-order slice of the source text — the splitter may drop
    ';' separators and comment-only regions, but may never reorder,
    fabricate, or rewrite characters."""

    @given(seed=st.integers(0, 2 ** 31 - 1), n=st.integers(1, 6))
    def test_generated_programs_are_slices(self, seed: int, n: int) -> None:
        self._check_slices(g.build_program(seed, n).text)

    @pytest.mark.parametrize("text", UNSUPPORTED_INPUTS)
    def test_unsupported_syntax_characterized(self, text: str) -> None:
        """No contract, so no correctness assertion — but the verbatim
        invariant must hold for any text, and the observed statement
        counts are recorded as characterization evidence."""
        stmts = split_statements(text)
        _ev(f"unsupported:{text[:20]!r}:{len(stmts)} statements")
        self._check_slices(text)

    # ---- shared body ------------------------------------------------

    @staticmethod
    def _check_slices(text: str) -> None:
        pos = 0
        parts = split_statements(text)
        for s in parts:
            assert isinstance(s, Statement)
            found = text.find(s.sql, pos)
            assert found >= 0, (
                f"statement {s.sql!r} is not a verbatim slice at/after "
                f"offset {pos}"
            )
            pos = found + len(s.sql)
