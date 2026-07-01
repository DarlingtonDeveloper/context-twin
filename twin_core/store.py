"""Unit 1 — SQLite backing for the master table of reference-cells.

The store holds refs + types only. A `MaterialisedValue` appears on a cell ONLY when
Unit 4 later writes a governed cache entry back through `put_cell`. The store never
fetches and never holds policy callables — a cell carries `policy_id`; the callable
`CellPolicy` is rehydrated from Unit 3's `PolicyRegistry` at use time.
"""
from __future__ import annotations
import sqlite3
from typing import Optional

from contract import (
    Cell,
    MaterialisedValue,
    Reference,
    TypeDescriptor,
)

_CREATE_CELLS = """
CREATE TABLE IF NOT EXISTS cells (
    cell_id       TEXT PRIMARY KEY,
    principal_id  TEXT,              -- denormalised for fast cells_for()
    source        TEXT,             -- Reference.source
    locator       TEXT,             -- Reference.locator
    resolver      TEXT,             -- Reference.resolver
    kind          TEXT,             -- TypeDescriptor.kind
    shape         TEXT,             -- TypeDescriptor.shape (nullable)
    ontology_node TEXT,             -- TypeDescriptor.ontology_node (mandatory, fail-closed)
    policy_id     TEXT,
    state         TEXT,             -- 'placeholder' | 'materialised'
    mat_json      TEXT              -- serialised MaterialisedValue, or NULL
);
"""


def init_cells(conn: sqlite3.Connection) -> None:
    """Create the `cells` table if it does not already exist."""
    conn.execute(_CREATE_CELLS)
    conn.commit()


def _principal_of(cell: Cell) -> str:
    """Recover the principal_id denormalised onto the row.

    The frozen `MasterTableStore.put_cell(cell)` signature carries no principal_id and
    `Cell` has no such field, so we derive it from the Reference locator. `build_cells`
    mints locators as `f"{source}:{principal_id}:{field}"`; we split off source (first
    segment) and field (last segment), leaving the principal — which is robust even if a
    principal_id itself contains ':'.
    """
    parts = cell.ref.locator.split(":")
    if len(parts) >= 3:
        return ":".join(parts[1:-1])
    # Fallback for locators not minted by build_cells: use the whole locator.
    return cell.ref.locator


class SqliteMasterTableStore:
    """Concrete `MasterTableStore` (see contract.py) over a shared sqlite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        init_cells(conn)

    # ---- writes ----
    def put_cell(self, cell: Cell) -> None:
        """Upsert a cell.

        The pydantic `Cell` validator already enforces the fail-closed invariants
        (state==materialised iff materialised is not None; non-empty ontology_node) at
        construction, so a valid `Cell` object cannot violate them here. We serialise
        `materialised` to `mat_json` and never write raw rows that could bypass the model.
        """
        mat_json = cell.materialised.model_dump_json() if cell.materialised is not None else None
        self.conn.execute(
            """
            INSERT INTO cells (cell_id, principal_id, source, locator, resolver,
                               kind, shape, ontology_node, policy_id, state, mat_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cell_id) DO UPDATE SET
                principal_id=excluded.principal_id,
                source=excluded.source,
                locator=excluded.locator,
                resolver=excluded.resolver,
                kind=excluded.kind,
                shape=excluded.shape,
                ontology_node=excluded.ontology_node,
                policy_id=excluded.policy_id,
                state=excluded.state,
                mat_json=excluded.mat_json
            """,
            (
                cell.cell_id,
                _principal_of(cell),
                cell.ref.source,
                cell.ref.locator,
                cell.ref.resolver,
                cell.type.kind,
                cell.type.shape,
                cell.type.ontology_node,
                cell.policy_id,
                cell.state,
                mat_json,
            ),
        )
        self.conn.commit()

    # ---- reads ----
    def cells_for(self, principal_id: str) -> list[Cell]:
        rows = self.conn.execute(
            "SELECT * FROM cells WHERE principal_id = ? ORDER BY cell_id", (principal_id,)
        ).fetchall()
        return [self._row_to_cell(r) for r in rows]

    def cells_for_node(self, principal_id: str, node: str) -> list[Cell]:
        """Cells for one principal classified to one ontology node.

        Returns the multiplicity that becomes a conflict downstream (e.g. two `role`
        cells for Colin — one from crm_a, one from crm_b).
        """
        rows = self.conn.execute(
            "SELECT * FROM cells WHERE principal_id = ? AND ontology_node = ? ORDER BY cell_id",
            (principal_id, node),
        ).fetchall()
        return [self._row_to_cell(r) for r in rows]

    def all_cells(self) -> list[Cell]:
        rows = self.conn.execute("SELECT * FROM cells ORDER BY cell_id").fetchall()
        return [self._row_to_cell(r) for r in rows]

    # ---- rehydration ----
    def _row_to_cell(self, row: sqlite3.Row) -> Cell:
        """Rebuild a `Cell` from a row so the model validator re-checks invariants on load."""
        materialised: Optional[MaterialisedValue] = None
        if row["mat_json"] is not None:
            materialised = MaterialisedValue.model_validate_json(row["mat_json"])
        return Cell(
            cell_id=row["cell_id"],
            ref=Reference(
                source=row["source"],
                locator=row["locator"],
                resolver=row["resolver"],
            ),
            type=TypeDescriptor(
                kind=row["kind"],
                shape=row["shape"],
                ontology_node=row["ontology_node"],
            ),
            policy_id=row["policy_id"],
            state=row["state"],
            materialised=materialised,
        )
