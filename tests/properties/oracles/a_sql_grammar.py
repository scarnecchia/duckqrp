# pattern: Functional Core
"""A small SQL statement grammar with KNOWN boundaries, for the
``qrp.sqlsplit`` execution-equivalence property.

Oracle independence
-------------------
``build_program`` AUTHORS a program statement by statement, recording as
it goes: each statement's text, the (kind, target) classification it
deserves, and a typed effect. ``replay`` turns the effects into the
final database state with plain Python list operations. Nothing is
inferred from ``qrp.sqlsplit``; the splitter is compared against what
the grammar wrote down.

Hazards are embedded only inside tokens any SQL splitter must treat as
opaque: ``;`` inside single-quoted strings (with '' escapes), inside
``--`` and ``/* */`` comments, and inside double-quoted identifiers.
Programs may end without a trailing semicolon (the tail push), and
comment-only regions may sit between statements (dropped by design).

Grammar guarantees the test relies on:

* a table is created before anything targets it;
* a table that is a VIEW source is never dropped, so views stay
  bindable to the end of the run;
* DROP only fires while at least one other table lives.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

TABLE_NAMES = ("alpha", "beta", "gamma")
VIEW_NAMES = ("va", "vb")
LABELS = ("a;b", "it's; x", 'q"uo;te', "pönt; --", "/* ; */", "plain")


def sql_lit(s: str) -> str:
    """A SQL single-quoted literal, ''-escaped per the SQL standard."""
    return "'" + s.replace("'", "''") + "'"


def quoted_ident(s: str) -> str:
    """A double-quoted identifier, ""-escaped."""
    return '"' + s.replace('"', '""') + '"'


@dataclass
class Plan:
    """One authored statement: its text, the (kind, target) it deserves,
    and the effect replay() applies."""

    sql: str
    kind: str | None
    target: str | None
    effect: dict[str, Any]


@dataclass
class Program:
    text: str
    plans: list[Plan]
    hazards: tuple[str, ...]
    view_sources: dict[str, str]     # view -> source table

    def expected_statements(self) -> list[tuple[str | None, str | None]]:
        return [(p.kind, p.target) for p in self.plans]


def replay(plans: list[Plan]) -> dict[str, tuple[tuple[str, ...],
                                                 list[tuple]]]:
    """The oracle's final database state: table -> (column names, rows).

    Rows are plain tuples held in lists — multiplicity is whatever the
    INSERT/DELETE/COPY sequence produced, never a set.
    """
    state: dict[str, tuple[tuple[str, ...], list[tuple]]] = {}
    for p in plans:
        e = p.effect
        op = e["op"]
        if op == "create":
            state[e["table"]] = (e["cols"], [])
        elif op == "insert":
            _, rows = state[e["table"]]
            rows.extend(e["rows"])
        elif op == "copy":
            src_cols, src_rows = state[e["src"]]
            rows = list(src_rows)
            if e.get("filter") is not None:
                amt_idx = src_cols.index("amt")
                rows = [r for r in rows if r[amt_idx] > e["filter"]]
            if e.get("subset"):
                keep = [src_cols.index(c) for c in e["cols"]]
                rows = [tuple(r[i] for i in keep) for r in rows]
            state[e["dst"]] = (e["cols"], rows)
        elif op == "delete":
            cols, rows = state[e["table"]]
            label_idx = cols.index("label")
            state[e["table"]] = (
                cols,
                [r for r in rows if r[label_idx] != e["label"]],
            )
        elif op == "view":
            pass    # schema + contents are checked live against the source
        elif op == "drop":
            state.pop(e["table"], None)
        elif op == "select":
            pass
        else:  # pragma: no cover - a grammar bug would fail loudly here
            raise AssertionError(f"unknown op {op!r}")
    return state


def projected(program: Program, view: str) -> list[tuple]:
    """A view's final contents, computed from the oracle state."""
    src = program.view_sources[view]
    plans = [p for p in program.plans
             if p.kind == "VIEW" and p.target == view]
    cols = plans[0].effect["cols"]
    src_cols, rows = replay(program.plans)[src]
    keep = [src_cols.index(c) for c in cols]
    return [tuple(r[i] for i in keep) for r in rows]


class _Builder:
    """Assembles statements while recording plans and hazards."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.plans: list[Plan] = []
        self.hazards: list[str] = []

    def _comment(self) -> str:
        which = self.rng.choice(("line", "block"))
        if which == "line":
            self.hazards.append("line_comment")
            # the trailing newline keeps the ';' separator outside the
            # comment — a line comment that ate a separator would make
            # this a different grammar
            return "-- a comment; with ; two; and 'a quote'\n"
        self.hazards.append("block_comment")
        return " /* block; with ' a quote and ; two */ "

    def create_empty(self, table: str) -> None:
        body = (f"CREATE OR REPLACE TABLE {table} AS SELECT "
                "CAST(NULL AS INTEGER) AS id, "
                "CAST(NULL AS VARCHAR) AS label, "
                "CAST(NULL AS DOUBLE) AS amt WHERE FALSE")
        sql = body + self._comment() if self.rng.random() < 0.3 else body
        self.plans.append(Plan(
            sql, "TABLE", table,
            {"op": "create", "table": table,
             "cols": ("id", "label", "amt")},
        ))

    def insert(self, table: str, n_rows: int, start_id: int) -> None:
        values: list[tuple[int, str, float]] = []
        chunks: list[str] = []
        for k in range(n_rows):
            label = self.rng.choice(LABELS)
            amt = round(self.rng.uniform(0.0, 100.0), 2)
            ident = start_id + k
            values.append((ident, label, amt))
            chunks.append(f"({ident}, {sql_lit(label)}, {amt})")
        sql = (f"INSERT INTO {table} (id, label, amt) "
               f"VALUES {', '.join(chunks)}")
        self.plans.append(Plan(sql, None, None,
                               {"op": "insert", "table": table,
                                "rows": values}))
        self.hazards.append("semicolon_in_string")   # labels carry ';'
        if any("'" in v[1] for v in values):
            self.hazards.append("escaped_quote")

    def copy(self, src: str, dst: str, subset: bool) -> None:
        flt = round(self.rng.uniform(0.0, 100.0), 2)
        select = "id, label" if subset else "*"
        sql = (f"CREATE OR REPLACE TABLE {dst} AS "
               f"SELECT {select} FROM {src} WHERE amt > {flt}")
        self.plans.append(Plan(sql, "TABLE", dst,
                               {"op": "copy", "src": src, "dst": dst,
                                "filter": flt, "subset": subset,
                                "cols": (("id", "label") if subset
                                         else ("id", "label", "amt"))}))

    def view(self, src: str, name: str) -> None:
        sql = f"CREATE OR REPLACE VIEW {name} AS SELECT id, amt FROM {src}"
        self.plans.append(Plan(sql, "VIEW", name,
                               {"op": "view", "view": name, "src": src,
                                "cols": ("id", "amt")}))

    def delete_by_label(self, table: str) -> None:
        label = self.rng.choice(LABELS)
        sql = f"DELETE FROM {table} WHERE label = {sql_lit(label)}"
        self.plans.append(Plan(sql, None, None,
                               {"op": "delete", "table": table,
                                "label": label}))

    def drop(self, table: str) -> None:
        self.plans.append(Plan(f"DROP TABLE IF EXISTS {table}",
                               "DROP", None,
                               {"op": "drop", "table": table}))

    def bare_select(self) -> None:
        alias = quoted_ident("ali;as")
        self.hazards.append("quoted_identifier")
        sql = f"SELECT 42 {self._comment()}AS {alias}"
        self.plans.append(Plan(sql, None, None, {"op": "select"}))

    def join(self) -> str:
        """Statements separated by ';' with comment-only junk between;
        the final statement may lack the semicolon (the tail push)."""
        parts: list[str] = []
        for i, p in enumerate(self.plans):
            parts.append(p.sql)
            if i == len(self.plans) - 1 and self.rng.random() < 0.5:
                self.hazards.append("no_trailing_semicolon")
                break                          # tail without ';'
            parts.append(";")
            if self.rng.random() < 0.4:
                parts.append("-- only a comment; ; here\n")
                self.hazards.append("comment_only_region")
        return "".join(parts)


def build_program(seed: int, n_statements: int) -> Program:
    """Author a program of ``n_statements`` with known boundaries.

    Deterministic in (seed, n_statements).
    """
    rng = random.Random(seed)
    b = _Builder(rng)
    live: list[str] = []
    full: list[str] = []             # tables carrying id+label+amt
    view_sources: dict[str, str] = {}
    row_id = 1
    for _ in range(n_statements):
        options = ["select"]
        if not live:
            options = ["create"]
        else:
            # everything but bare SELECT needs the full column set
            # somewhere; subset copies only carry (id, label)
            if full:
                options += ["insert", "insert", "insert", "delete"]
                if len(view_sources) < len(VIEW_NAMES):
                    options += ["view"]
                if any(t not in live for t in TABLE_NAMES):
                    options += ["copy", "subset"]
            droppable = [t for t in live
                         if t not in view_sources.values() and len(live) > 1]
            if droppable:
                options += ["drop"]
        choice = rng.choice(options)
        if choice == "create":
            t = rng.choice(TABLE_NAMES)
            b.create_empty(t)
            live.append(t)
            full.append(t)
        elif choice == "insert":
            b.insert(rng.choice(full), rng.randint(1, 3), row_id)
            row_id += 4
        elif choice in ("copy", "subset"):
            dst = rng.choice([t for t in TABLE_NAMES if t not in live])
            b.copy(rng.choice(full), dst, subset=(choice == "subset"))
            live.append(dst)
            if choice == "copy":
                full.append(dst)
        elif choice == "view":
            src = rng.choice(full)
            name = next(v for v in VIEW_NAMES if v not in view_sources)
            b.view(src, name)
            view_sources[name] = src
        elif choice == "delete":
            b.delete_by_label(rng.choice(full))
        elif choice == "drop":
            victim = rng.choice(
                [t for t in live
                 if t not in view_sources.values() and len(live) > 1])
            b.drop(victim)
            live.remove(victim)
            if victim in full:
                full.remove(victim)
        else:
            b.bare_select()
    return Program(text=b.join(), plans=b.plans,
                   hazards=tuple(dict.fromkeys(b.hazards)),
                   view_sources=view_sources)
