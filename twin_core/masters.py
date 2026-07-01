"""Unit 1 — thin `MasterTable` convenience over the store.

Pure reads for the demo UI (Units 3/4 render the grid). No values are dereferenced;
each grid cell is a placeholder/materialised `Cell` describing a ref + type.
"""
from __future__ import annotations

from contract import Cell, MasterTableStore


class MasterTable:
    """Read-only view of the map: principals as rows, ontology nodes as columns."""

    def __init__(self, store: MasterTableStore) -> None:
        self.store = store

    def _all(self) -> list[Cell]:
        return self.store.all_cells()

    def rows(self) -> list[str]:
        """Distinct principal_ids present in the map (sorted, stable)."""
        seen: list[str] = []
        for cell in self._all():
            pid = self._principal_of(cell)
            if pid not in seen:
                seen.append(pid)
        return sorted(seen)

    def columns(self) -> list[str]:
        """Distinct ontology nodes present in the map (sorted, stable)."""
        nodes = {cell.type.ontology_node for cell in self._all()}
        return sorted(nodes)

    def grid(self) -> dict[str, dict[str, list[Cell]]]:
        """`{principal_id: {ontology_node: [Cell, ...]}}`.

        A node may map to more than one cell for a principal (the multiplicity that
        becomes a conflict downstream), so values are lists.
        """
        out: dict[str, dict[str, list[Cell]]] = {}
        for cell in self._all():
            pid = self._principal_of(cell)
            node = cell.type.ontology_node
            out.setdefault(pid, {}).setdefault(node, []).append(cell)
        return out

    @staticmethod
    def _principal_of(cell: Cell) -> str:
        parts = cell.ref.locator.split(":")
        if len(parts) >= 3:
            return ":".join(parts[1:-1])
        return cell.ref.locator
