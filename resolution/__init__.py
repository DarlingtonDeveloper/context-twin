"""Unit 3 — Resolution + Gate + Projection (wired together, one unit)."""
from resolution.gate import (
    PredicateGate, PolicyRegistry, dereference_predicate, hr_dereference,
    allow, deny, OPEN, ROLE_GATED, SECRET, HR_SCOPED,
)
from resolution.projection import Projector
from resolution.resolver import Resolver, GroupingOverlay
from resolution.capability import mint, demo_analyst_cap, demo_restricted_cap

__all__ = [
    "PredicateGate", "PolicyRegistry", "dereference_predicate", "hr_dereference",
    "allow", "deny", "OPEN", "ROLE_GATED", "SECRET", "HR_SCOPED",
    "Projector", "Resolver", "GroupingOverlay",
    "mint", "demo_analyst_cap", "demo_restricted_cap",
]
