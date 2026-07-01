"""Unit 1 — the reference inversion.

The single most important idea in the system: facts carry references and types,
NEVER values. `build_cells` turns classified source fields into placeholder `Cell`s
that locate their value at the source but hold no value. It reads ZERO values — it
must never call `reader.read_value`. A value only ever appears on a cell when Unit 4
writes a governed cache entry back through the store.
"""
from __future__ import annotations
import hashlib

from contract import Cell, Reference, SourceReader, TypeDescriptor


def _cell_id(principal_id: str, source: str, field: str) -> str:
    """Stable id so re-onboarding the same field is idempotent."""
    return hashlib.sha256(f"{principal_id}{source}{field}".encode()).hexdigest()[:16]


def build_cells(
    principal_id: str,
    source: str,
    classified_fields: list[tuple[str, TypeDescriptor]],
    reader: SourceReader,
    default_policy_id: str,
) -> list[Cell]:
    """Mint placeholder reference-cells for a principal's classified fields.

    For each `(field_name, TypeDescriptor)` a `Cell` is built whose `ref` locates the
    value at the source and whose `type` carries the ontology node. State is always
    ``placeholder``. The `reader` is part of the seam signature but is intentionally
    NOT read here — a spy reader whose `read_value` raises proves values are never
    touched at map-build time.
    """
    cells: list[Cell] = []
    for field_name, type_desc in classified_fields:
        ref = Reference(
            source=source,
            locator=f"{source}:{principal_id}:{field_name}",
            resolver=source,
        )
        cells.append(
            Cell(
                cell_id=_cell_id(principal_id, source, field_name),
                ref=ref,
                type=type_desc,
                policy_id=default_policy_id,
                state="placeholder",
                materialised=None,
            )
        )
    return cells
