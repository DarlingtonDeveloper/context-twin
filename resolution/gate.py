"""Unit 3 — the single gate and the policy registry.

ONE gate interface, TWO physical call sites: adapter fetch (Unit 4) and projection (here).
This week it evaluates a plain callable predicate; the signature is kept identical so a
biscuit/authorisation engine drops in later without touching callers.

The registry owns the policy CALLABLES. Cells store only `policy_id` (Unit 1); the callables
never touch the db. A handful of demo policies are registered here.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from contract import Capability, CellPolicy, Context, Predicate

_log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PredicateGate:
    """Evaluates capability expiry, then the predicate. Fail-closed on both."""

    def check(self, cap: Capability, predicate: Predicate, ctx: Context) -> bool:
        expiry = cap.expiry
        if expiry.tzinfo is None:                 # tolerate naive expiries defensively
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < _now():
            return False
        return bool(predicate(cap, ctx))


# ---- demo predicates -------------------------------------------------------------
def allow(cap: Capability, ctx: Context) -> bool:
    return True


def deny(cap: Capability, ctx: Context) -> bool:
    return False


def hr_dereference(cap: Capability, ctx: Context) -> bool:
    """Dereference only for cross-source / DSAR purposes AND with HR clearance."""
    return cap.purpose in {"cross_source_query", "dsar_response"} and "clearance:hr" in cap.caveats


# resolution's identifying reads (name/email) are dereferences — gated by the same rule
dereference_predicate: Predicate = hr_dereference


# ---- demo policies (callables live here, never in the db) ------------------------
OPEN = CellPolicy(policy_id="open", see_existence=allow, see_type=allow,
                  see_state=allow, dereference=allow)

ROLE_GATED = CellPolicy(policy_id="role_gated", see_existence=allow, see_type=allow,
                        see_state=allow, dereference=hr_dereference)

# deny-by-dissimulation: existence itself is hidden, so the cell is ABSENT, not masked.
SECRET = CellPolicy(policy_id="secret", see_existence=deny, see_type=deny,
                    see_state=deny, dereference=deny)

# existence itself is capability-scoped: visible ONLY to an HR-cleared viewer. Two different
# capabilities therefore see two different MAPS (not just two different fetch outcomes).
HR_SCOPED = CellPolicy(policy_id="hr_scoped", see_existence=hr_dereference,
                       see_type=hr_dereference, see_state=allow, dereference=hr_dereference)

# The registered DEFAULT a cell points at when no explicit policy is chosen at mint time. The
# honest "you can see it exists, you cannot read it" posture: visible in the map (existence +
# type + state) but NOT dereferenceable without a specific grant. It is a REGISTERED policy so
# the common mint path is intentional, never a lookup for an id the registry doesn't know.
DEFAULT = CellPolicy(policy_id="default", see_existence=allow, see_type=allow,
                     see_state=allow, dereference=deny)

# The fail-closed fallback `get` returns for an UNKNOWN policy_id: every facet denies, so the
# cell is invisible to projection (Gate 1) and unreadable at fetch (Gate 2) — a governance gap
# fails closed (deny), never open, and never crashes a live query.
DENY_ALL = CellPolicy(policy_id="__deny_all__", see_existence=deny, see_type=deny,
                      see_state=deny, dereference=deny)


class PolicyRegistry:
    """policy_id -> CellPolicy (with live callables). Unknown ids fail CLOSED (deny-all)."""

    def __init__(self, policies: list[CellPolicy] | None = None) -> None:
        self._by_id: dict[str, CellPolicy] = {}
        for p in (policies if policies is not None else [OPEN, ROLE_GATED, SECRET, HR_SCOPED, DEFAULT]):
            self._by_id[p.policy_id] = p
        self.unknown_lookups = 0   # observable counter: an unknown id is a gap you want to see

    def get(self, policy_id: str) -> CellPolicy:
        """Fail CLOSED. An unregistered policy_id is a governance gap (typo, unmigrated source,
        renamed policy): return a deny-all policy so the cell is invisible and unreadable, rather
        than raising `KeyError` and crashing a live query. The lookup is counted + logged so the
        gap is observable, not silent."""
        policy = self._by_id.get(policy_id)
        if policy is None:
            self.unknown_lookups += 1
            _log.warning("PolicyRegistry.get: unknown policy_id %r -> deny-all (fail-closed)", policy_id)
            return DENY_ALL
        return policy

    def register(self, policy: CellPolicy) -> None:
        self._by_id[policy.policy_id] = policy
