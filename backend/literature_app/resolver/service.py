from __future__ import annotations

from datetime import datetime, timezone

from ..api.errors import ProviderError
from ..api.ports import AliasCandidate, EntityCandidate, MeshProvider, PubChemProvider, ResolutionCache, TermResolution
from .aliases import merge_aliases, normalize_alias
from .cache import MemoryResolutionCache


def normalize_input(value: str) -> str:
    normalized = " ".join(value.strip().casefold().split())
    if not normalized:
        raise ValueError("term cannot be empty")
    return normalized


class TermResolver:
    def __init__(self, mesh: MeshProvider, pubchem: PubChemProvider | None = None, cache: ResolutionCache | None = None):
        self.mesh = mesh
        self.pubchem = pubchem
        self.cache = cache or MemoryResolutionCache()

    def resolve(self, term: str, *, entity_type: str = "compound") -> TermResolution:
        normalized_input = normalize_input(term)
        cache_key = f"{entity_type}:{normalized_input}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        resolutions: list[TermResolution] = []
        errors: dict[str, str] = {}
        for source, operation in (("MeSH", lambda: self.mesh.resolve(term)), ("PubChem", lambda: self.pubchem.resolve_compound(term) if self.pubchem and entity_type == "compound" else None)):
            if operation is None:
                continue
            try:
                result = operation()
                if result and result.has_result:
                    resolutions.append(result)
            except (ProviderError, TimeoutError, OSError) as exc:
                errors[source] = str(exc)

        merged = merge_resolutions(*resolutions) if resolutions else TermResolution(term, None, entity_type)
        merged = TermResolution(
            input_term=term,
            canonical_name=merged.canonical_name,
            entity_type=entity_type,
            mesh_ui=merged.mesh_ui,
            ncbi_mesh_entrez_id=merged.ncbi_mesh_entrez_id,
            pubchem_cid=merged.pubchem_cid,
            aliases=merged.aliases,
            ambiguous=merged.ambiguous or _distinct_entity_count(resolutions) > 1,
            raw_sources=merged.raw_sources,
            normalized_input=normalized_input,
            detected_source=" + ".join(r.detected_source for r in resolutions if r.detected_source) or None,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            cache_hit=False,
            cache_key=cache_key,
            provider_errors=errors,
            candidates=tuple(candidate for resolution in resolutions for candidate in resolution.candidates),
            raw_aliases=tuple(alias for resolution in resolutions for alias in resolution.raw_aliases),
        )
        self.cache.set(cache_key, merged)
        return merged


def merge_resolutions(*resolutions: TermResolution) -> TermResolution:
    if not resolutions:
        raise ValueError("at least one resolution is required")
    first = resolutions[0]
    ordered_aliases = merge_aliases(alias for resolution in resolutions for alias in resolution.aliases)
    return TermResolution(
        input_term=first.input_term,
        canonical_name=next((r.canonical_name for r in resolutions if r.canonical_name), None),
        entity_type=first.entity_type,
        mesh_ui=next((r.mesh_ui for r in resolutions if r.mesh_ui), None),
        ncbi_mesh_entrez_id=next((r.ncbi_mesh_entrez_id for r in resolutions if r.ncbi_mesh_entrez_id), None),
        pubchem_cid=next((r.pubchem_cid for r in resolutions if r.pubchem_cid), None),
        aliases=ordered_aliases,
        ambiguous=any(r.ambiguous for r in resolutions),
        raw_sources={source: resolution.raw_sources.get(source) for resolution in resolutions for source in resolution.raw_sources},
        candidates=tuple(candidate for resolution in resolutions for candidate in resolution.candidates),
        raw_aliases=tuple(alias for resolution in resolutions for alias in resolution.raw_aliases),
    )


def _distinct_entity_count(resolutions: list[TermResolution]) -> int:
    groups: list[set[str]] = []
    for resolution in resolutions:
        candidates = resolution.candidates or (EntityCandidate(
            canonical_name=resolution.canonical_name,
            entity_type=resolution.entity_type,
            source=resolution.detected_source or "unknown",
            mesh_ui=resolution.mesh_ui,
            ncbi_mesh_entrez_id=resolution.ncbi_mesh_entrez_id,
            pubchem_cid=resolution.pubchem_cid,
            aliases=resolution.aliases,
        ),)
        for candidate in candidates:
            keys = {normalize_alias(candidate.canonical_name)} if candidate.canonical_name else set()
            keys.update(alias.normalized_value or normalize_alias(alias.value) for alias in candidate.aliases)
            keys.update(value for value in (candidate.mesh_ui, candidate.pubchem_cid) if value)
            if not keys:
                keys.add(f"{candidate.source}:{candidate.entity_type}")
            overlaps = [group for group in groups if group & keys]
            if overlaps:
                merged = set(keys)
                for group in overlaps:
                    merged.update(group)
                    groups.remove(group)
                groups.append(merged)
            else:
                groups.append(set(keys))
    return len(groups)
