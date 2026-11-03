from __future__ import annotations
from dataclasses import dataclass

def _normalize_term(value: str) -> str:
    """Normalize one user/provider term while keeping PubMed quoting balanced."""
    cleaned = " ".join(value.strip().split())
    if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        raise ValueError("term cannot be empty")
    # A literal double quote would terminate the PubMed phrase. Use a single
    # quote inside the deterministic outer double-quote pair instead.
    return cleaned.replace('"', "'")


def format_pubmed_term(value: str, field: str) -> str:
    """Format every PubMed term with one explicit quoted value and field tag."""
    if field not in {"MeSH Terms", "Title/Abstract"}:
        raise ValueError(f"unsupported PubMed field: {field}")
    return f'"{_normalize_term(value)}"[{field}]'


def escape_title_abstract(value: str) -> str:
    return format_pubmed_term(value, "Title/Abstract")

@dataclass(frozen=True)
class QueryBlock:
    label: str
    mesh_term: str | None
    aliases: tuple[str, ...]

    def render(self) -> str:
        parts: list[str] = []
        if self.mesh_term:
            parts.append(format_pubmed_term(self.mesh_term, "MeSH Terms"))

        seen: set[str] = set()
        for alias in self.aliases:
            if not alias.strip():
                continue
            normalized_key = " ".join(alias.strip().split()).casefold()
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            parts.append(escape_title_abstract(alias))

        if not parts:
            raise ValueError(f"query block {self.label!r} has no terms")
        return "(\n  " + "\n  OR ".join(parts) + "\n)"

def combine_blocks(*blocks: QueryBlock, operator: str = "AND") -> str:
    if not blocks:
        raise ValueError("at least one query block is required")
    if operator not in {"AND", "OR"}:
        raise ValueError("operator must be AND or OR")
    return f"\n{operator}\n".join(block.render() for block in blocks)
