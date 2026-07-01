# Context Twin

A governed digital twin. The core idea: **facts carry references and types, never values.**
The map holds typed reference-cells; a value only ever materialises when it is fetched under a
capability that the cell's policy allows, and the fetch is audited. This lets a single twin serve
many viewers, each seeing only what their capability permits — fail-closed by default.

Built by five units against a **frozen** contract (`contract.py`) over a shared SQLite substrate
(`twin.db`). Each unit implements the seam interface(s) it owns and imports only interfaces
(never another unit's concrete class) for what it consumes, so all five build in parallel.

| Unit | Dir | Owns |
|------|-----|------|
| 1 — Twin Core | `twin_core/` | `MasterTableStore`, cell construction, reference inversion |
| 2 — Onboarding | `onboarding/` | `Onboarder`, `ControlPlane` |
| 3 — Resolution | `resolution/` | `Gate`, `Projector`, `Resolver`, `PolicyRegistry` |
| 4 — Fetch | `fetch/` | `SourceReader`, `Adapter`, `FetchLadder` |
| 5 — Audit | `audit/` | `AuditSink` |

## Layout

```
contract.py     FROZEN types + seam interfaces. Changed by nobody without flagging the owner.
locators.py     FROZEN behaviour. The ONE place a cell locator is minted/parsed.
db.py           sqlite connection helper + init_all()
ontology.yaml   hand-authored nodes (deliberately omits `region` — an intentional gap)
seed/           make_seed.py -> crm_a.csv, crm_b.csv (two divergent CRMs, planted identities)
twin_core/      Unit 1 (built)
onboarding/ resolution/ fetch/ audit/   Units 2–5 (stubs)
tests/          acceptance tests
```

## Locators & identity grouping (shared foundation — read before minting)

A **locator is opaque**: `source:row_key:field`, each component percent-encoded so a `:`
inside a component can never be mistaken for the separator. It says *where* a value lives,
never *who* it belongs to. **Always** mint/parse via `locators.make_locator` / `parse_locator`
— never hand-format or `split(":")` a locator anywhere. There is deliberately no
`principal_id_of(locator)`: deriving identity from a locator is impossible by construction.

**Identity grouping is a separate, overridable column** on `cells`, set at write time — not
encoded in the locator:

- `store.put_cell(cell)` — frozen contract path; writes the cell **ungrouped** (`principal_id`
  NULL).
- `store.put_cell_for(principal_id, cell)` — write with a known grouping. Re-put with `None`
  never clobbers an existing grouping (`COALESCE`).
- `store.set_grouping(cell_id, principal_id)` — re-group live. Unit 3 calls this **only** under
  a capability authorising durable re-grouping; by default query-time resolution keeps its merge
  in an in-memory overlay and persists nothing.
- `twin_core.seed_grouping(records)` — cheap deterministic mint-time seed (exact normalised-email
  match; no embeddings, no LLM). Groups Colin (shared email) at ingest; deliberately leaves Dana
  (blank second email) for the live resolver to merge on stage.

Each unit's contract with this seam: Unit 4 `read_value` parses `ref.locator` via `parse_locator`
to get `(source, row_key, field)` — for the week `row_key` is the CSV row id. Unit 3 decides
identity from values under the gate and applies groupings (overlay by default, `set_grouping`
only when the capability permits). No unit reads identity from a locator.

> Note on `build_cells`: identity is out of minting entirely — it takes `(source, row_key,
> classified_fields, reader, default_policy_id)` and no `principal_id`. Grouping is applied at the
> write site via `put_cell_for`, per the overridable-grouping decision.

## Setup

```bash
uv venv && uv pip install -e ".[dev]"     # or: pip install -e ".[dev]"
cp .env.example .env                        # fill ANTHROPIC_API_KEY when Units 2/3 need it
python seed/make_seed.py                     # generate the seed CSVs
uv run pytest tests/test_unit1.py -v         # Unit 1 acceptance tests
```

## The seed (what the demo lives on)

Two CRMs with divergent schemas and planted structure:

- **Colin Marsh** — shared email across A/B → AUTO-band resolution; `title` vs `job_role`, both
  dated → `conflict_ordered`.
- **Dana Osei** — no shared email (B email blank) → middle-band (LLM) resolution on name + org;
  one title undated → `conflict_unordered`.
- **Colin Marsh-Jones** — near-miss at a different org; must NOT resolve to Colin Marsh.
- `region` (crm_b only) — has no ontology node → quarantine + propose (Unit 2).

## Status

- **Unit 0 (shared foundation)** — done: contract, `locators.py`, db, ontology, seed, packaging.
- **Unit 1 (twin core)** — done: `SqliteMasterTableStore` (overridable grouping), `build_cells`,
  `seed_grouping`, `MasterTable`. 12/12 tests pass.
- **Units 2–5** — stubs; owned by other agents.
