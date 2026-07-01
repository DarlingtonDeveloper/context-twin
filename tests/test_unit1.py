"""Unit 1 acceptance tests — the map data layer.

Covers: cell round-trip (incl. materialised provenance), fail-closed invariants,
reference-inversion never fetching, the conflict multiplicity, and cell_id stability.
"""
from __future__ import annotations
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from contract import (
    Cell,
    MaterialisedValue,
    Reference,
    TypeDescriptor,
    Value,
)
from db import get_conn
from twin_core.inversion import build_cells
from twin_core.store import SqliteMasterTableStore


# --------------------------------------------------------------------------- helpers
class SpyReader:
    """A `SourceReader` whose `read_value` MUST NOT be called during map-build."""

    def __init__(self) -> None:
        self.read_calls = 0

    def list_fields(self, source: str) -> list[str]:
        return ["full_name", "email", "title", "company"]

    def sample_field(self, source: str, field: str, n: int = 3) -> list[str]:
        return ["sample"] * n

    def read_value(self, ref: Reference) -> Value:
        self.read_calls += 1
        raise AssertionError("read_value must never be called at map-build time")


def _store() -> SqliteMasterTableStore:
    # An isolated in-memory db per test; the store shares this one connection.
    return SqliteMasterTableStore(get_conn(":memory:"))


def _role(node: str = "role") -> TypeDescriptor:
    return TypeDescriptor(kind="string", shape=None, ontology_node=node)


def _placeholder_cell(pid: str, source: str, field: str, node: str) -> Cell:
    return Cell(
        cell_id=f"{pid}-{source}-{field}",
        ref=Reference(source=source, locator=f"{source}:{pid}:{field}", resolver=source),
        type=TypeDescriptor(kind="string", shape=None, ontology_node=node),
        policy_id="default",
        state="placeholder",
        materialised=None,
    )


# --------------------------------------------------------------------------- tests
def test_1_roundtrip_placeholder_and_materialised():
    store = _store()
    placeholder = _placeholder_cell("colin", "crm_a", "email", "email")

    materialised = Cell(
        cell_id="colin-crm_a-title",
        ref=Reference(source="crm_a", locator="crm_a:colin:title", resolver="crm_a"),
        type=_role(),
        policy_id="default",
        state="materialised",
        materialised=MaterialisedValue(
            value="VP Engineering",
            fetched_under="cap-abc123",
            fetched_at=datetime(2025, 11, 2, 12, 0, 0),
            ttl=timedelta(hours=1),
            origin_policy_id="default",
        ),
    )

    store.put_cell(placeholder)
    store.put_cell(materialised)

    cells = {c.cell_id: c for c in store.cells_for("colin")}
    assert set(cells) == {"colin-crm_a-email", "colin-crm_a-title"}

    got = cells["colin-crm_a-title"]
    assert got.state == "materialised"
    assert got.materialised is not None
    # provenance survives the round-trip
    assert got.materialised.fetched_under == "cap-abc123"
    assert got.materialised.origin_policy_id == "default"
    assert got.materialised.value == "VP Engineering"
    assert got.materialised.ttl == timedelta(hours=1)

    # the placeholder stays a placeholder with no value
    assert cells["colin-crm_a-email"].state == "placeholder"
    assert cells["colin-crm_a-email"].materialised is None


def test_2_fail_closed_invariants():
    # state=materialised with materialised=None -> rejected at construction
    with pytest.raises(ValidationError):
        Cell(
            cell_id="bad-1",
            ref=Reference(source="crm_a", locator="crm_a:x:email", resolver="crm_a"),
            type=_role(),
            policy_id="default",
            state="materialised",
            materialised=None,
        )

    # empty ontology_node -> rejected (fail-closed)
    with pytest.raises(ValidationError):
        Cell(
            cell_id="bad-2",
            ref=Reference(source="crm_a", locator="crm_a:x:email", resolver="crm_a"),
            type=TypeDescriptor(kind="string", shape=None, ontology_node=""),
            policy_id="default",
            state="placeholder",
            materialised=None,
        )


def test_3_build_cells_never_fetches():
    spy = SpyReader()
    fields = [
        ("full_name", TypeDescriptor(kind="string", shape=None, ontology_node="person")),
        ("email", TypeDescriptor(kind="string", shape=None, ontology_node="email")),
        ("title", _role()),
    ]
    cells = build_cells("colin", "crm_a", fields, reader=spy, default_policy_id="default")

    assert len(cells) == 3
    assert spy.read_calls == 0  # values never touched at map-build time
    assert all(c.state == "placeholder" for c in cells)
    assert all(c.materialised is None for c in cells)
    # refs point back at the source; no values are carried
    email_cell = next(c for c in cells if c.type.ontology_node == "email")
    assert email_cell.ref.locator == "crm_a:colin:email"
    assert email_cell.ref.resolver == "crm_a"


def test_4_conflict_multiplicity_for_role():
    store = _store()

    role_a = TypeDescriptor(kind="string", shape=None, ontology_node="role")  # title
    role_b = TypeDescriptor(kind="string", shape=None, ontology_node="role")  # job_role

    # Colin across both CRMs -> two role cells.
    for source, field, td in [("crm_a", "title", role_a), ("crm_b", "job_role", role_b)]:
        for c in build_cells("colin", source, [(field, td)], reader=SpyReader(), default_policy_id="default"):
            store.put_cell(c)

    # Dana too, so we prove filtering by principal works.
    for source, field, td in [("crm_a", "title", role_a), ("crm_b", "job_role", role_b)]:
        for c in build_cells("dana", source, [(field, td)], reader=SpyReader(), default_policy_id="default"):
            store.put_cell(c)

    colin_roles = store.cells_for_node("colin", "role")
    assert len(colin_roles) == 2
    assert {c.ref.source for c in colin_roles} == {"crm_a", "crm_b"}

    # a different node returns nothing for Colin
    assert store.cells_for_node("colin", "email") == []
    # Dana is isolated
    assert len(store.cells_for_node("dana", "role")) == 2


def test_5_cell_id_stability_idempotent():
    fields = [("title", _role())]
    first = build_cells("colin", "crm_a", fields, reader=SpyReader(), default_policy_id="default")
    second = build_cells("colin", "crm_a", fields, reader=SpyReader(), default_policy_id="default")

    assert first[0].cell_id == second[0].cell_id

    # re-onboarding upserts rather than duplicating
    store = _store()
    store.put_cell(first[0])
    store.put_cell(second[0])
    assert len(store.all_cells()) == 1
