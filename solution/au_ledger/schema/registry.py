"""Loads and queries per-workflow lead schemas (``assignment-a-data/schemas/*.json``).

Kept separate from ingestion so the ingestion code only ever asks the registry
"which field (if any) does this header mean?" and "what are this workflow's fields,
in order?" -- it has no knowledge of the on-disk JSON format itself. This is the
single place that implements the header-matching rule from the spec (lower-case,
trim, collapse ``_``/whitespace runs to a single space, compare against the field
name or any alias, normalized the same way).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_WHITESPACE_OR_UNDERSCORE = re.compile(r"[_\s]+")


def normalize_header(raw: str) -> str:
    """Lower-case, trim, and collapse runs of ``_``/whitespace to a single space."""
    return _WHITESPACE_OR_UNDERSCORE.sub(" ", raw.strip().lower()).strip()


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    required: bool
    pii: bool
    aliases: tuple[str, ...]
    reference: bool = False
    values: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class WorkflowSchema:
    workflow_id: str
    fields: tuple[FieldSpec, ...]
    _header_lookup: dict[str, str] = field(default_factory=dict, compare=False)

    def field_by_name(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    def resolve_header(self, raw_header: str) -> str | None:
        """Return the schema field name this CSV header maps to, or ``None`` if unmatched."""
        return self._header_lookup.get(normalize_header(raw_header))

    @property
    def reference_field(self) -> FieldSpec:
        for f in self.fields:
            if f.reference:
                return f
        raise ValueError(f"workflow {self.workflow_id} has no reference field")

    @property
    def pii_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(f for f in self.fields if f.pii)


def _build_schema(raw: dict) -> WorkflowSchema:
    fields = []
    lookup: dict[str, str] = {}
    for raw_field in raw["fields"]:
        spec = FieldSpec(
            name=raw_field["name"],
            type=raw_field["type"],
            required=bool(raw_field.get("required", False)),
            pii=bool(raw_field.get("pii", False)),
            aliases=tuple(raw_field.get("aliases", [])),
            reference=bool(raw_field.get("reference", False)),
            values=tuple(raw_field.get("values", [])),
            default=raw_field.get("default"),
        )
        fields.append(spec)
        lookup[normalize_header(spec.name)] = spec.name
        for alias in spec.aliases:
            lookup[normalize_header(alias)] = spec.name
    return WorkflowSchema(workflow_id=raw["workflow_id"], fields=tuple(fields), _header_lookup=lookup)


class SchemaRegistry:
    """Loads all workflow schemas and the disposition catalogue from a schemas directory."""

    def __init__(self, schemas_dir: Path) -> None:
        self._schemas: dict[str, WorkflowSchema] = {}
        self._dispositions: dict[str, dict[str, tuple[bool, bool]]] = {}
        self._load_workflow_schemas(schemas_dir)
        self._load_dispositions(schemas_dir / "dispositions.csv")

    def _load_workflow_schemas(self, schemas_dir: Path) -> None:
        for path in sorted(schemas_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            schema = _build_schema(raw)
            self._schemas[schema.workflow_id] = schema

    def _load_dispositions(self, path: Path) -> None:
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                wf = row["workflow_id"]
                self._dispositions.setdefault(wf, {})[row["code"].lower()] = (
                    row["is_terminal"].strip().lower() == "true",
                    row["is_success"].strip().lower() == "true",
                )

    def workflow_ids(self) -> list[str]:
        return sorted(self._schemas.keys())

    def get_schema(self, workflow_id: str) -> WorkflowSchema:
        return self._schemas[workflow_id]

    def is_known_disposition(self, workflow_id: str, code: str) -> bool:
        return code.lower() in self._dispositions.get(workflow_id, {})

    def disposition_flags(self, workflow_id: str, code: str) -> tuple[bool, bool]:
        """Return ``(is_terminal, is_success)`` for a known disposition code."""
        return self._dispositions[workflow_id][code.lower()]

    def all_disposition_flags(self, workflow_id: str) -> dict[str, tuple[bool, bool]]:
        """Return the full ``{code: (is_terminal, is_success)}`` map for a workflow."""
        return dict(self._dispositions.get(workflow_id, {}))
