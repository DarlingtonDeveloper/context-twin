"""Unit 2 acceptance tests — onboarding, banded classification, control plane.

Uses the REAL fastembed embedder (tests 1/2/6 need genuine semantic scores; the model
is cached locally after first download). The LLM adjudicator is always a counting fake,
so tests never hit the network and are deterministic.
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from contract import Capability, Reference, TypeDescriptor, Cell, ControlPlaneRow
from db import get_conn
from locators import make_locator, field_of
from twin_core.store import SqliteMasterTableStore
from onboarding.classify import OntologyClassifier, VerdictCache
from onboarding.control_plane import SqliteControlPlane
from onboarding.onboard import onboard_source

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"


# --------------------------------------------------------------------------- fakes
class FakeSourceReader:
    """Reads a seed CSV. sample_field returns first-n non-blank; read_value MUST NOT run."""

    def __init__(self, csv_path: Path, rename: dict | None = None):
        self.rows = list(csv.DictReader(open(csv_path)))
        self.rename = rename or {}
        self.read_calls = 0

    def _col(self, field):
        # reverse a rename (e.g. rm -> job_role) to find the underlying column
        return {v: k for k, v in self.rename.items()}.get(field, field)

    def list_fields(self, source):
        cols = list(self.rows[0].keys())
        return [self.rename.get(c, c) for c in cols]

    def sample_field(self, source, field, n=3):
        col = self._col(field)
        return [r[col] for r in self.rows if r[col].strip()][:n]

    def read_value(self, ref: Reference):
        self.read_calls += 1
        raise AssertionError("read_value must never be called during onboarding")


class FakeGate:
    def __init__(self, allow=True):
        self.allow = allow

    def check(self, cap, predicate, ctx):
        return self.allow and predicate(cap, ctx)


class FakeAudit:
    def __init__(self):
        self.entries = []

    def append(self, entry):
        self.entries.append(entry)


class CountingAdjudicator:
    def __init__(self, node=None):
        self.calls = 0
        self.node = node

    def __call__(self, field_name, sample, candidates):
        self.calls += 1
        return {"node": self.node, "confidence": 0.5, "reason": "fake"}


def _cap():
    return Capability(holder="onboarder", purpose="onboarding",
                      expiry=datetime.now(timezone.utc) + timedelta(hours=1))


# a shared classifier so the embedding model loads once for the module
_CLASSIFIER = None
def classifier(conn=None, adjudicator=None, low=None):
    global _CLASSIFIER
    if adjudicator is None and low is None:
        if _CLASSIFIER is None:
            _CLASSIFIER = OntologyClassifier()
        return _CLASSIFIER
    kwargs = {}
    if adjudicator is not None:
        kwargs["adjudicator"] = adjudicator
    if conn is not None:
        kwargs["cache"] = VerdictCache(conn)
    if low is not None:
        kwargs["low"] = low
    return OntologyClassifier(**kwargs)


# --------------------------------------------------------------------------- tests
def test_1_crm_b_auto_fields_and_region_not_served():
    conn = get_conn(":memory:")
    store = SqliteMasterTableStore(conn)
    clf = classifier()
    cp = SqliteControlPlane(conn, closest_nodes=clf.closest_nodes)
    reader = FakeSourceReader(SEED / "crm_b.csv")

    report = onboard_source(
        "crm_b", reader, FakeGate(), _cap(), FakeAudit(), store, cp, clf,
        rows=reader.rows, principal_of=lambda r: r["contact_id"], key_field="contact_id",
    )

    assert report.bands["name"] == "auto"
    assert report.bands["primary_email"] == "auto"
    assert report.bands["job_role"] == "auto"
    assert report.bands["org_name"] == "auto"

    # the ontology gap: region is NOT auto and is NOT served as a cell
    assert report.bands["region"] != "auto"
    assert report.bands["region"] in ("propose_new", "quarantine")
    assert "region" in report.proposals  # it went to the control plane, proposed not live
    served_fields = {field_of(c.ref.locator) for c in store.all_cells()}
    assert "region" not in served_fields
    assert served_fields == {"name", "primary_email", "job_role", "org_name"}

    # structural quarantine of keys/timestamps
    assert report.bands["contact_id"] == "quarantine"
    assert report.bands["last_touch"] == "quarantine"


def test_2_obscure_field_needs_sample():
    # fake adjudicator: the no-sample case lands in the middle band; keep it off the network
    clf = classifier(conn=get_conn(":memory:"), adjudicator=CountingAdjudicator(node=None))
    role_sample = ["Director, Platform", "Operations Lead", "Staff Software Engineer"]

    with_sample = clf.classify("rm", role_sample, source="crm_b")
    assert with_sample.band == "auto"
    assert with_sample.proposed_node == "role"     # classifies to role BECAUSE of the sample (>= HIGH)

    without_sample = clf.classify("rm", [], source="crm_b")
    assert without_sample.proposed_node != "role"  # sample is load-bearing
    assert without_sample.band != "auto"


def test_3_sample_and_drop_no_values_in_cells():
    conn = get_conn(":memory:")
    store = SqliteMasterTableStore(conn)
    clf = classifier()
    cp = SqliteControlPlane(conn, closest_nodes=clf.closest_nodes)
    reader = FakeSourceReader(SEED / "crm_b.csv")

    onboard_source("crm_b", reader, FakeGate(), _cap(), FakeAudit(), store, cp, clf,
                   rows=reader.rows, principal_of=lambda r: r["contact_id"], key_field="contact_id")

    # every sampled ATTRIBUTE value classification saw. Exclude the key field (contact_id):
    # its values ARE the row keys and legitimately live inside opaque locators, not as data.
    sampled = set()
    for fld in reader.list_fields("crm_b"):
        if fld == "contact_id":
            continue
        sampled.update(reader.sample_field("crm_b", fld, 3))
    sampled = {v for v in sampled if v.strip()}

    dump = "\n".join(
        "|".join("" if x is None else str(x) for x in row)
        for row in conn.execute("SELECT * FROM cells").fetchall()
    )
    for v in sampled:
        assert v not in dump, f"sampled value leaked into cells: {v!r}"
    assert reader.read_calls == 0


def test_4_control_plane_propose_approve_and_sprawl_guard():
    conn = get_conn(":memory:")
    clf = classifier()
    cp = SqliteControlPlane(conn, closest_nodes=clf.closest_nodes)

    row_id = cp.propose(ControlPlaneRow(
        id="node:region", kind="ontology_node",
        payload={"name": "region", "description": "a geographic sales region, e.g. EMEA, NA, APAC"},
    ))
    assert cp.status_of(row_id) == "proposed"      # proposed, NOT live
    assert cp.current_version() == 0

    new_version = cp.approve(row_id, approver="mike")
    assert new_version == 1
    assert cp.current_version() == 1               # version bumped
    assert cp.status_of(row_id) == "approved"

    # sprawl guard: three closest EXISTING nodes surfaced first
    closest = cp.closest_nodes("region", k=3)
    assert len(closest) == 3
    assert all(name in {"person", "email", "role", "organisation", "phone"} for name, _ in closest)

    # now approvable into a cell
    store = SqliteMasterTableStore(conn)
    cell = Cell(
        cell_id="region-cell",
        ref=Reference(source="crm_b", locator=make_locator("crm_b", "b1", "region"), resolver="crm_b"),
        type=TypeDescriptor(kind="string", shape=None, ontology_node="region"),
        policy_id="default", state="placeholder", materialised=None,
    )
    store.put_cell_for("colin", cell)
    assert len(store.cells_for_node("colin", "region")) == 1


def test_5_determinism_zero_llm_and_identical_bands():
    reader = FakeSourceReader(SEED / "crm_b.csv")
    adj = CountingAdjudicator(node=None)
    conn = get_conn(":memory:")
    clf = classifier(conn=conn, adjudicator=adj)  # default thresholds: region is sub-LOW, no LLM

    def run():
        c = get_conn(":memory:")
        store = SqliteMasterTableStore(c)
        cp = SqliteControlPlane(c, closest_nodes=clf.closest_nodes)
        return onboard_source("crm_b", reader, FakeGate(), _cap(), FakeAudit(), store, cp, clf,
                              rows=reader.rows, principal_of=lambda r: r["contact_id"],
                              key_field="contact_id")

    first = run().bands
    calls_after_first = adj.calls
    second = run().bands
    assert first == second                     # identical bands
    assert adj.calls == calls_after_first == 0  # seed has no middle band -> zero LLM calls


def test_6_verdict_cache_prevents_repeat_llm_calls():
    # force region into the middle band (LOW=0.60 < region 0.614 < HIGH 0.66) so the LLM runs
    conn = get_conn(":memory:")
    adj = CountingAdjudicator(node=None)     # "none" -> region still becomes propose_new
    clf = classifier(conn=conn, adjudicator=adj, low=0.60)
    region_sample = ["EMEA", "NA", "APAC"]

    p1 = clf.classify("region", region_sample, source="crm_b")
    p2 = clf.classify("region", region_sample, source="crm_b")

    assert adj.calls == 1                     # second call served from the verdict cache
    assert p1.band == p2.band == "propose_new"
