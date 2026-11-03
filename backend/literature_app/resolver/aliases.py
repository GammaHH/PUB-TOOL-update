from __future__ import annotations

import re
from collections.abc import Iterable

from ..api.ports import AliasCandidate

_IDENTIFIER_PATTERNS = (
    re.compile(r"^\d{2,7}-\d{2}-\d$"),
    re.compile(r"^(?:UNII|CHEBI:?|CHEMBL|DTXSID|DTXCID)[A-Z0-9:-]*$", re.IGNORECASE),
    re.compile(r"^(?:SCHEMBL|MFCD|NCGC|NSC|NINDS|EINECS|EN\d|ID\d|KBIO|STL|ST|REFCHEM|SPECTRUM|PRESTWICK|PRESTWCK|PRESTW|PRECK|ORB|SBI)[\s_:.-]*\d[\w\s:.,()-]*$", re.IGNORECASE),
    re.compile(r"^(?:KBIO\w*|FT|Q)[\s_:.-]*\d[\w\s:.,()-]*$", re.IGNORECASE),
)
_RESEARCH_CODE = re.compile(r"^[A-Z]{1,5}[ -]?\d{2,}[A-Z0-9-]*$", re.IGNORECASE)
_SYSTEMATIC = re.compile(r'(?:\d.*(?:methyl|ethyl|naphth|oxolan|tetrahydro)|(?:N|O|S)-.*(?:methyl|ethyl|naphth|tetrahydro)|\.(?:alpha|beta|gamma)\.)', re.IGNORECASE)
_ANNOTATION = re.compile(r'[\[\]()]')
_SIMPLE_NAME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+(?:[ -][A-Za-zÀ-ÖØ-öø-ÿ]+){0,5}$")


def normalize_alias(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def classify_alias(value: str, *, source: str, preferred: bool = False, mesh_entry: bool = False) -> tuple[str, bool, bool]:
    cleaned = value.strip()
    if mesh_entry:
        return "mesh_entry_term", True, True
    if any(pattern.fullmatch(value.strip()) for pattern in _IDENTIFIER_PATTERNS):
        return "database_identifier", False, False
    if len(cleaned) > 80 or _SYSTEMATIC.search(cleaned):
        return "systematic_name", False, False
    # PubChem's first synonym may itself be an ID or systematic chemical name.
    # A preferred-name flag must not bypass the raw-identifier safeguards above.
    if _ANNOTATION.search(cleaned):
        return "annotated_name", False, False
    if preferred:
        return "preferred_name", True, True
    if _RESEARCH_CODE.fullmatch(value.strip()):
        return "research_code", True, False
    if _SIMPLE_NAME.fullmatch(cleaned):
        # Typography alone cannot verify that a name is a marketed trade name.
        return "name_candidate", True, False
    return "other", False, False


def make_alias(value: str, source: str, *, preferred: bool = False, mesh_entry: bool = False, retrieved_at: str | None = None) -> AliasCandidate:
    classification, query_eligible, default_selected = classify_alias(value, source=source, preferred=preferred, mesh_entry=mesh_entry)
    return AliasCandidate(
        value=value,
        source=source,
        sources=(source,),
        normalized_value=normalize_alias(value),
        selected=False,
        retrieved_at=retrieved_at,
        classification=classification,
        query_eligible=query_eligible,
        default_selected=default_selected,
    )


def merge_aliases(aliases: Iterable[AliasCandidate]) -> tuple[AliasCandidate, ...]:
    merged: dict[str, AliasCandidate] = {}
    for alias in aliases:
        normalized = alias.normalized_value or normalize_alias(alias.value)
        current = merged.get(normalized)
        if current is None:
            merged[normalized] = alias.with_normalized_value()
            continue
        sources = tuple(sorted(set(current.sources or (current.source,)) | set(alias.sources or (alias.source,)), key=str.casefold))
        classifications = {current.classification, alias.classification}
        query_eligible = current.query_eligible or alias.query_eligible
        default_selected = current.default_selected or alias.default_selected
        preferred_order = {"preferred_name": 0, "mesh_entry_term": 1, "generic_name": 2, "trade_name": 3, "name_candidate": 4, "research_code": 5, "other": 6, "annotated_name": 7, "database_identifier": 8, "systematic_name": 9}
        classification = min(classifications, key=lambda item: preferred_order.get(item, 99))
        merged[normalized] = AliasCandidate(
            value=current.value,
            source=sources[0],
            sources=sources,
            verified=current.verified or alias.verified,
            normalized_value=normalized,
            selected=current.selected or alias.selected,
            retrieved_at=current.retrieved_at or alias.retrieved_at,
            source_metadata={**current.source_metadata, **alias.source_metadata},
            classification=classification,
            query_eligible=query_eligible,
            default_selected=default_selected,
        )
    return tuple(merged[key] for key in sorted(merged))
