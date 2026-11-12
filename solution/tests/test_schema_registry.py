from __future__ import annotations

from au_ledger.schema.registry import normalize_header


def test_normalize_header_collapses_underscores_and_whitespace():
    assert normalize_header("Mobile_No") == "mobile no"
    assert normalize_header("  Mobile   No  ") == "mobile no"
    assert normalize_header("mobile___no") == "mobile no"


def test_resolve_header_matches_name_and_alias(registry):
    schema = registry.get_schema("motor_renewal")
    assert schema.resolve_header("Mobile No") == "phone"
    assert schema.resolve_header("policy_number") == "policy_number"
    assert schema.resolve_header("Policy No") == "policy_number"
    assert schema.resolve_header("agent_notes") is None


def test_reference_and_pii_fields(registry):
    schema = registry.get_schema("motor_renewal")
    assert schema.reference_field.name == "policy_number"
    pii_names = {f.name for f in schema.pii_fields}
    assert pii_names == {"customer_name", "phone"}
