"""Requirement → validator registry (doc §22).

Maps each discriminated requirement type from the scenario schema to its
deterministic validator. The registry is data, not logic: scenario authors
never write Python, and the engine never hard-codes scenario-specific checks.
"""

from __future__ import annotations

from collections.abc import Callable

from netlab.core.errors import GradingError
from netlab.grading.checks import (
    CheckOutcome,
    check_access_vlan,
    check_host_gateway,
    check_host_ip,
    check_interface_mode,
    check_ip_address,
    check_subinterface_encapsulation,
    check_trunk_allowed_vlans,
    check_trunk_native_vlan,
    check_vlan_exists,
    check_vlan_name,
)
from netlab.scenarios.schema import Requirement

ValidatorFn = Callable[[object, object], CheckOutcome]

_REGISTRY: dict[str, ValidatorFn] = {
    "vlan_exists": check_vlan_exists,
    "vlan_name": check_vlan_name,
    "interface_mode": check_interface_mode,
    "access_vlan": check_access_vlan,
    "trunk_native_vlan": check_trunk_native_vlan,
    "trunk_allowed_vlans": check_trunk_allowed_vlans,
    "ip_address": check_ip_address,
    "host_ip": check_host_ip,
    "host_gateway": check_host_gateway,
    "subinterface_encapsulation": check_subinterface_encapsulation,
}


def validator_for(requirement: Requirement) -> ValidatorFn:
    """Return the deterministic validator for ``requirement``'s type."""
    validator = _REGISTRY.get(requirement.type)
    if validator is None:
        raise GradingError(
            f"no validator registered for requirement type {requirement.type!r}"
        )
    return validator


def registered_types() -> tuple[str, ...]:
    """All requirement types the engine can grade (stable order)."""
    return tuple(sorted(_REGISTRY))


__all__ = ["registered_types", "validator_for"]
