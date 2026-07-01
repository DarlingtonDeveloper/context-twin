"""Unit 2 — the onboarding orchestrator.

Walk a source's fields; for each: gated bounded sample read -> classify -> route by band.
`auto` fields are minted into the twin as placeholder cells (via Unit 1's `build_cells`);
everything else becomes a PROPOSED control-plane row (fail-closed — no live cell without
human approval).

Note on rows: the frozen `SourceReader` has no row-enumeration method and we must not
invent a contract method, so `onboard_source` is handed the source `rows` (dicts). Rows
are used ONLY for their key (row_key) and for `principal_of` grouping — attribute VALUES
are never written into cells (cells are placeholders; see reference inversion).
"""
from __future__ import annotations
from dataclasses import dataclass, field as dc_field
from typing import Callable, Optional

from contract import (
    AuditSink,
    Capability,
    ClassificationProposal,
    ControlPlaneRow,
    Gate,
    Reference,
    SourceReader,
    TypeDescriptor,
)
from locators import make_locator
from twin_core.inversion import build_cells
from twin_core.store import SqliteMasterTableStore
from onboarding.classify import OntologyClassifier
from onboarding.control_plane import SqliteControlPlane
from onboarding.reader_gate import onboarding_read


@dataclass
class OnboardReport:
    source: str
    bands: dict[str, str] = dc_field(default_factory=dict)              # field -> band
    proposed_nodes: dict[str, Optional[str]] = dc_field(default_factory=dict)
    proposals: dict[str, str] = dc_field(default_factory=dict)          # field -> control_plane row id
    auto_fields: list[str] = dc_field(default_factory=list)
    minted_cells: int = 0

    def is_auto(self, field: str) -> bool:
        return self.bands.get(field) == "auto"


def _schema_ref(source: str, field: str) -> Reference:
    # a schema-level ref: sample the COLUMN, not a specific row's value
    return Reference(source=source, locator=make_locator(source, "__schema__", field), resolver=source)


def onboard_source(source: str,
                   reader: SourceReader,
                   gate: Gate,
                   cap: Capability,
                   audit: AuditSink,
                   store: SqliteMasterTableStore,
                   control_plane: SqliteControlPlane,
                   classifier: OntologyClassifier,
                   rows: list[dict],
                   principal_of: Callable[[dict], str],
                   key_field: str,
                   default_policy_id: str = "default") -> OnboardReport:
    report = OnboardReport(source=source)
    auto_fields: list[tuple[str, TypeDescriptor]] = []

    for fld in reader.list_fields(source):
        sample = onboarding_read(_schema_ref(source, fld), reader, gate, cap, audit)
        proposal: ClassificationProposal = classifier.classify(fld, sample, source=source)
        report.bands[fld] = proposal.band
        report.proposed_nodes[fld] = proposal.proposed_node

        if proposal.band == "auto":
            auto_fields.append((fld, TypeDescriptor(kind="string", shape=None,
                                                    ontology_node=proposal.proposed_node)))
            report.auto_fields.append(fld)
        else:
            # fail-closed: proposed, not live
            row_id = control_plane.propose(ControlPlaneRow(
                id=f"{source}:{fld}", kind="classification",
                payload={
                    "source": source, "field": fld, "band": proposal.band,
                    "proposed_node": proposal.proposed_node, "evidence": proposal.evidence,
                },
            ))
            report.proposals[fld] = row_id

    # mint placeholder cells for the auto (servable) fields, one row at a time, grouped
    if auto_fields:
        for row in rows:
            pid = principal_of(row)
            row_key = str(row[key_field])
            for cell in build_cells(source, row_key, auto_fields, reader, default_policy_id):
                store.put_cell_for(pid, cell)
                report.minted_cells += 1

    return report


class Onboarder:
    """Concrete `Onboarder` (contract) — binds reader/gate/cap/audit + classifier to a source."""

    def __init__(self, reader: SourceReader, gate: Gate, cap: Capability,
                 audit: AuditSink, classifier: OntologyClassifier, source: str = "") -> None:
        self.reader = reader
        self.gate = gate
        self.cap = cap
        self.audit = audit
        self.classifier = classifier
        self.source = source

    def onboarding_read(self, ref: Reference, sample_n: int = 3) -> list[str]:
        return onboarding_read(ref, self.reader, self.gate, self.cap, self.audit, sample_n)

    def classify(self, field_name: str, sample: list[str]) -> ClassificationProposal:
        return self.classifier.classify(field_name, sample, source=self.source)
