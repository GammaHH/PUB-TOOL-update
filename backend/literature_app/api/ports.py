from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Sequence

@dataclass(frozen=True)
class AliasCandidate:
    value: str
    source: str
    verified: bool = False
    normalized_value: str | None = None
    selected: bool = False
    retrieved_at: str | None = None
    source_metadata: dict[str, object] = field(default_factory=dict)
    sources: tuple[str, ...] = ()
    classification: str = "other"
    query_eligible: bool = True
    default_selected: bool = False

    def with_normalized_value(self) -> "AliasCandidate":
        normalized = " ".join(self.value.casefold().split())
        return AliasCandidate(
            value=self.value,
            source=self.source,
            verified=self.verified,
            normalized_value=normalized,
            selected=self.selected,
            retrieved_at=self.retrieved_at,
            source_metadata=self.source_metadata,
            sources=self.sources or (self.source,),
            classification=self.classification,
            query_eligible=self.query_eligible,
            default_selected=self.default_selected,
        )


@dataclass(frozen=True)
class EntityCandidate:
    canonical_name: str | None
    entity_type: str
    source: str
    mesh_ui: str | None = None
    ncbi_mesh_entrez_id: str | None = None
    pubchem_cid: str | None = None
    aliases: tuple[AliasCandidate, ...] = ()
    ambiguity_reason: str | None = None

@dataclass(frozen=True)
class TermResolution:
    input_term: str
    canonical_name: str | None
    entity_type: str
    mesh_ui: str | None = None
    ncbi_mesh_entrez_id: str | None = None
    pubchem_cid: str | None = None
    aliases: tuple[AliasCandidate, ...] = ()
    ambiguous: bool = False
    raw_sources: dict[str, object] = field(default_factory=dict)
    normalized_input: str | None = None
    detected_source: str | None = None
    retrieved_at: str | None = None
    cache_hit: bool = False
    cache_key: str | None = None
    provider_errors: dict[str, str] = field(default_factory=dict)
    candidates: tuple[EntityCandidate, ...] = ()
    raw_aliases: tuple[AliasCandidate, ...] = ()

    @property
    def mesh_id(self) -> str | None:
        """Backward-compatible alias for mesh_ui; never contains Entrez IDs."""
        return self.mesh_ui

    @property
    def has_result(self) -> bool:
        return bool(self.canonical_name or self.aliases or self.candidates)

@dataclass(frozen=True)
class PubMedSearchResult:
    query: str
    database: str
    count: int
    pmids: tuple[str, ...]
    history_server: str | None = None
    history_key: str | None = None

@dataclass(frozen=True)
class ArticleRecord:
    pmid: str
    pmcid: str | None = None
    doi: str | None = None
    title: str | None = None
    authors: tuple[str, ...] = ()
    journal: str | None = None
    publication_year: int | None = None
    abstract: str | None = None
    publication_types: tuple[str, ...] = ()
    mesh_terms: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    raw_payload: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class FetchBatchResult:
    records: tuple[ArticleRecord, ...]
    failed_pmids: tuple[str, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)

class MeshProvider(Protocol):
    def resolve(self, term: str) -> TermResolution: ...

class PubChemProvider(Protocol):
    def resolve_compound(self, term: str) -> TermResolution: ...

class PubMedProvider(Protocol):
    def search(self, query: str, *, batch_size: int = 500) -> PubMedSearchResult: ...
    def fetch(self, pmids: Sequence[str], *, batch_size: int = 200) -> FetchBatchResult: ...


class ResolutionCache(Protocol):
    def get(self, key: str) -> TermResolution | None: ...
    def set(self, key: str, value: TermResolution) -> None: ...
