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
db.py           sqlite connection helper + init_all()
ontology.yaml   hand-authored nodes (deliberately omits `region` — an intentional gap)
seed/           make_seed.py -> crm_a.csv, crm_b.csv (two divergent CRMs, planted identities)
twin_core/      Unit 1 (built)
onboarding/ resolution/ fetch/ audit/   Units 2–5 (stubs)
tests/          acceptance tests
```

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

- **Unit 0 (shared foundation)** — done: contract, db, ontology, seed, packaging.
- **Unit 1 (twin core)** — done: `SqliteMasterTableStore`, `build_cells`, `MasterTable`, tests.
- **Units 2–5** — stubs; owned by other agents.
