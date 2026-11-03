from __future__ import annotations

import argparse
import json

from ..api.providers import HttpxMeshProvider, HttpxPubChemProvider
from .service import TermResolver


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a biomedical term using MeSH and PubChem.")
    parser.add_argument("term")
    parser.add_argument("--entity-type", default="compound")
    parser.add_argument("--verbose", action="store_true", help="include raw provider aliases")
    args = parser.parse_args()
    result = TermResolver(HttpxMeshProvider(), HttpxPubChemProvider()).resolve(args.term, entity_type=args.entity_type)
    print(json.dumps(_jsonable(result, verbose=args.verbose), ensure_ascii=False, indent=2))
    return 0


def _jsonable(result, *, verbose: bool = False):
    output = {
        "input_term": result.input_term,
        "normalized_input": result.normalized_input,
        "entity_type": result.entity_type,
        "detected_source": result.detected_source,
        "canonical_name": result.canonical_name,
        "mesh_ui": result.mesh_ui,
        "ncbi_mesh_entrez_id": result.ncbi_mesh_entrez_id,
        "pubchem_cid": result.pubchem_cid,
        "ambiguous": result.ambiguous,
        "provider_errors": result.provider_errors,
        "cache_hit": result.cache_hit,
        "retrieved_at": result.retrieved_at,
        "search_aliases": [
            {"value": alias.value, "normalized_value": alias.normalized_value, "sources": list(alias.sources or (alias.source,)), "classification": alias.classification, "query_eligible": alias.query_eligible, "default_selected": alias.default_selected, "selected": alias.selected}
            for alias in result.aliases
        ],
        "raw_alias_count": len(result.raw_aliases),
        "candidates": [
            {"canonical_name": candidate.canonical_name, "entity_type": candidate.entity_type, "source": candidate.source, "mesh_ui": candidate.mesh_ui, "ncbi_mesh_entrez_id": candidate.ncbi_mesh_entrez_id, "pubchem_cid": candidate.pubchem_cid}
            for candidate in result.candidates
        ],
    }
    if verbose:
        output["raw_aliases"] = [
            {"value": alias.value, "normalized_value": alias.normalized_value, "sources": list(alias.sources or (alias.source,)), "classification": alias.classification}
            for alias in result.raw_aliases
        ]
    return output


if __name__ == "__main__":
    raise SystemExit(main())
