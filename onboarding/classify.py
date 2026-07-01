"""Unit 2 — the banded field-to-node classifier.

The resolver's band architecture pointed at field-to-node classification. A field is
placed by embedding `field_name + " | " + sample values` and MAX-POOLING its cosine
against every node's exemplar set (loaded from ontology.yaml). Bands:

  auto        score >= HIGH                     -> servable after cell-minting
  flag        LOW <= score < HIGH               -> LLM middle-band adjudicates; still
                                                    human-confirmed (never auto)
  propose_new score < LOW, real concept absent  -> propose a NEW node (proposed_node=None)
  quarantine  score < LOW, no coherent concept  -> parked, not served

FAIL-CLOSED (hole 2): anything not `auto` produces NO live cell until a human approves it
through the control plane. A misclassification can never silently become a servable cell.

A structural pre-filter quarantines obvious keys (`id`, `*_id`) and timestamp columns
before embedding — they are not attributes, and their names ('contact_id', 'last_touch')
otherwise embed spuriously near real concepts (contact_id->email 0.76, updated_at->phone
0.73, both ABOVE the auto bar). This filter is load-bearing, not cosmetic: it kills the
worst spurious matches before they can auto-mint. Quarantine is the safe direction.

THRESHOLDS ARE CALIBRATED PER ONTOLOGY, NOT UNIVERSAL. The *method* generalises
(multi-exemplar max-pool + structural pre-filter + human-in-the-band); the constants are
tuned per deployment and should be recalibrated when a new-vocabulary source is onboarded.
For THIS ontology, tuned on the seed:
  - HIGH=0.70: the eight true crm_a/crm_b attribute fields all clear 0.70 (min is
    `name`->person = 0.7003, the binding constraint — a thin margin, honestly). `region`
    (the ontology gap) tops out at 0.614, so it lands `propose_new`, never `auto`.
  - LOW=0.62: the flag band is a WIDE [0.62, 0.70). This is a deliberate fail-closed
    choice: a spurious field that clears the old 0.66 bar (e.g. `department`->role 0.68,
    `linkedin`->organisation 0.68) now routes to the human-confirmed LLM band instead of
    silently auto-minting. Nothing in the seed hits [0.62, 0.70), so seed determinism and
    the zero-LLM-on-seed property are untouched.

KNOWN RESIDUAL LIMITATION (bge-small, not a bug): a few spurious fields still clear 0.70
on real data (e.g. a free-text `notes`->phone 0.74). Embeddings alone cannot separate
these; the mitigations are the structural pre-filter (common id/date cases), the wide flag
band (0.62-0.70), and per-ontology recalibration on onboarding. Do not present 0.70 as a
law of nature — it is a per-deployment calibration.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import yaml

from contract import AuditEntry, Capability, ClassificationProposal
from onboarding.embed import Embedder, cosine, default_embedder

HIGH = 0.70   # auto bar. Binding constraint: name->person = 0.7003 (see module docstring).
LOW = 0.62    # flag band is a deliberately WIDE [LOW, HIGH) so danger-zone fields flag, not auto.

_ONTOLOGY_PATH = Path(__file__).resolve().parent.parent / "ontology.yaml"

# structural pre-filter
_DATE_RE = re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}")


def _looks_key(field_name: str) -> bool:
    f = field_name.strip().lower()
    return f == "id" or f.endswith("_id")


def _looks_date(sample: list[str]) -> bool:
    return bool(sample) and all(_DATE_RE.match(v.strip()) for v in sample)


# ---- LLM middle-band adjudicator -------------------------------------------------
# Signature: (field_name, sample, candidates) -> {"node": str|None, "confidence": float, "reason": str}
# candidates is a list of (node_name, description). Injectable so tests never hit the network.
Adjudicator = Callable[[str, list[str], list[tuple[str, str]]], dict]


def anthropic_adjudicator(field_name: str, sample: list[str],
                          candidates: list[tuple[str, str]]) -> dict:
    """Default middle-band adjudicator: one strict-JSON Anthropic call (RESOLVER_MODEL)."""
    import os
    import anthropic

    model = os.environ.get("RESOLVER_MODEL", "claude-sonnet-4-6")
    cand_lines = "\n".join(f"- {n}: {d}" for n, d in candidates)
    prompt = (
        "You map a data-source field to at most one ontology node.\n"
        f"Field name: {field_name}\n"
        f"Sample values: {sample}\n"
        f"Candidate nodes:\n{cand_lines}\n\n"
        'Answer with STRICT JSON only: {"node": <one candidate name or null>, '
        '"confidence": <0..1>, "reason": <short string>}. '
        'Use null if the field fits none of the candidates.'
    )
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=model, max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    text = text[text.find("{"): text.rfind("}") + 1]
    verdict = json.loads(text)
    return {"node": verdict.get("node"),
            "confidence": float(verdict.get("confidence", 0.0)),
            "reason": str(verdict.get("reason", ""))}


class VerdictCache:
    """SQLite memo of middle-band verdicts so re-runs make ZERO new LLM calls."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS classify_cache "
            "(key TEXT PRIMARY KEY, verdict_json TEXT)"
        )
        conn.commit()

    @staticmethod
    def key(field_name: str, sample: list[str]) -> str:
        raw = field_name + "::" + "|".join(sample)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT verdict_json FROM classify_cache WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, verdict: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO classify_cache (key, verdict_json) VALUES (?, ?)",
            (key, json.dumps(verdict, sort_keys=True)),
        )
        self.conn.commit()


def load_ontology(path: Path = _ONTOLOGY_PATH) -> list[dict]:
    """Nodes as raw dicts: {name, description, exemplars}. `region` is intentionally absent."""
    data = yaml.safe_load(path.read_text())
    nodes = []
    for n in data["nodes"]:
        nodes.append({
            "name": n["name"],
            "description": n["description"],
            "exemplars": list(n.get("exemplars", [])),
        })
    return nodes


class OntologyClassifier:
    """Embeds node exemplars once; classifies fields with max-pool banding."""

    def __init__(self, embedder: Optional[Embedder] = None,
                 adjudicator: Optional[Adjudicator] = None,
                 cache: Optional[VerdictCache] = None,
                 audit=None,
                 ontology_path: Path = _ONTOLOGY_PATH,
                 high: float = HIGH, low: float = LOW) -> None:
        self.embedder = embedder or default_embedder()
        self.adjudicator = adjudicator or anthropic_adjudicator
        self.cache = cache
        self.audit = audit
        self.high = high
        self.low = low
        self.nodes = load_ontology(ontology_path)

        # flat exemplar list (+ description) with owner index, embedded once
        self._flat: list[str] = []
        self._owner: list[str] = []
        for node in self.nodes:
            for text in [node["description"], *node["exemplars"]]:
                self._flat.append(text)
                self._owner.append(node["name"])
        self._ex_vecs = self.embedder.embed(self._flat)

    # ---- scoring ----
    def _node_scores(self, field_name: str, sample: list[str]) -> list[tuple[str, float]]:
        text = field_name + " | " + " | ".join(sample)
        fv = self.embedder.embed([text])[0]
        best: dict[str, float] = {n["name"]: -1.0 for n in self.nodes}
        for i, _ in enumerate(self._flat):
            s = cosine(fv, self._ex_vecs[i])
            owner = self._owner[i]
            if s > best[owner]:
                best[owner] = s
        return sorted(best.items(), key=lambda kv: -kv[1])

    def closest_nodes(self, concept: str, k: int = 3) -> list[tuple[str, float]]:
        """Sprawl guard: the k existing nodes most similar to a proposed concept."""
        return self._node_scores(concept, [])[:k]

    # ---- classification ----
    def classify(self, field_name: str, sample: list[str],
                 source: str = "") -> ClassificationProposal:
        proposal = self._classify(field_name, sample, source)
        if self.audit is not None:
            self.audit.append(AuditEntry(
                event="classify", ts=datetime.now(timezone.utc),
                principal=source or "onboarding", capability_id="onboarding",
                cell_id=None, policy_version=0, decision="allow",
            ))
        return proposal

    def _classify(self, field_name: str, sample: list[str], source: str) -> ClassificationProposal:
        # structural pre-filter: keys and timestamps are not attributes -> quarantine
        if _looks_key(field_name):
            return self._quarantine(source, field_name,
                                    f"'{field_name}' is an identifier/key column, not an attribute")
        if _looks_date(sample):
            return self._quarantine(source, field_name,
                                    f"'{field_name}' holds timestamps {sample}, not an attribute")

        ranked = self._node_scores(field_name, sample)
        top, score = ranked[0]

        if score >= self.high:
            return ClassificationProposal(
                source=source, field_name=field_name, proposed_node=top,
                confidence=round(score, 4), band="auto",
                evidence=f"max-pool cosine to '{top}' = {score:.3f} (>= HIGH {self.high})",
            )

        if score >= self.low:
            # middle band: LLM adjudication among the top candidates (still human-confirmed)
            verdict = self._adjudicate(field_name, sample)
            if verdict.get("node"):
                return ClassificationProposal(
                    source=source, field_name=field_name, proposed_node=verdict["node"],
                    confidence=round(float(verdict.get("confidence", score)), 4), band="flag",
                    evidence=f"middle-band ({score:.3f}); LLM: {verdict.get('reason', '')}",
                )
            # LLM says "none" -> fall through to propose/quarantine
            return self._propose_or_quarantine(
                source, field_name, sample, top, score,
                extra="LLM found no fitting node",
            )

        # score < LOW
        return self._propose_or_quarantine(source, field_name, sample, top, score)

    def _adjudicate(self, field_name: str, sample: list[str]) -> dict:
        candidates = [(n["name"], n["description"]) for n in self.nodes]
        if self.cache is not None:
            key = VerdictCache.key(field_name, sample)
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            verdict = self.adjudicator(field_name, sample, candidates)
            self.cache.put(key, verdict)
            return verdict
        return self.adjudicator(field_name, sample, candidates)

    def _propose_or_quarantine(self, source, field_name, sample, top, score, extra="") -> ClassificationProposal:
        # a real, coherent concept absent from the ontology -> propose_new; else quarantine
        meaningful = bool(sample) and any(c.isalpha() for c in field_name)
        note = f" ({extra})" if extra else ""
        if meaningful:
            return ClassificationProposal(
                source=source, field_name=field_name, proposed_node=None,
                confidence=round(score, 4), band="propose_new",
                evidence=(f"'{field_name}' with sample {sample} matches no node "
                          f"(closest '{top}' = {score:.3f} < LOW {self.low}); looks like a new concept{note}"),
            )
        return self._quarantine(source, field_name,
                                f"no coherent concept for '{field_name}' (closest '{top}' = {score:.3f}){note}")

    def _quarantine(self, source, field_name, evidence) -> ClassificationProposal:
        return ClassificationProposal(
            source=source, field_name=field_name, proposed_node=None,
            confidence=0.0, band="quarantine", evidence=evidence,
        )
