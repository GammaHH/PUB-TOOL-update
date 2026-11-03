from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from urllib.parse import quote

import httpx

from .errors import MalformedProviderResponse, ProviderError, ProviderTimeout
from .parsers import parse_mesh_response, parse_mesh_xml_response, parse_pubchem_response
from .ports import MeshProvider, PubChemProvider, TermResolution


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HttpxMeshProvider(MeshProvider):
    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 20.0):
        self._client = client or httpx.Client(timeout=timeout)

    def resolve(self, term: str) -> TermResolution:
        try:
            search = self._client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "mesh", "term": term, "retmode": "json"},
            )
            search.raise_for_status()
            search_payload = search.json()
            ids = search_payload.get("esearchresult", {}).get("idlist", [])
            if not ids:
                return TermResolution(term, None, "unknown", detected_source="MeSH", retrieved_at=_now())
            fetch = self._client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={"db": "mesh", "id": ",".join(ids), "retmode": "xml"},
            )
            fetch.raise_for_status()
            result = parse_mesh_xml_response(fetch.text, input_term=term, retrieved_at=_now())
            return replace(result, ncbi_mesh_entrez_id=str(ids[0])) if len(ids) == 1 and not result.ambiguous else result
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"MeSH request timed out: {term}") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"MeSH request failed: {term}") from exc


class HttpxPubChemProvider(PubChemProvider):
    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 20.0):
        self._client = client or httpx.Client(timeout=timeout)

    def resolve_compound(self, term: str) -> TermResolution:
        encoded = quote(term, safe="")
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/synonyms/JSON"
        try:
            response = self._client.get(url)
            if response.status_code == 404:
                return TermResolution(term, None, "compound", detected_source="PubChem", retrieved_at=_now())
            response.raise_for_status()
            payload = response.json()
            return parse_pubchem_response(payload, input_term=term, retrieved_at=_now())
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"PubChem request timed out: {term}") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"PubChem request failed: {term}") from exc
