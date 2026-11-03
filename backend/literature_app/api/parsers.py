from __future__ import annotations

from collections.abc import Mapping

from .errors import MalformedProviderResponse
from .ports import AliasCandidate, EntityCandidate, TermResolution
from ..resolver.aliases import make_alias, merge_aliases
from xml.etree import ElementTree


def _required_mapping(payload: object, provider: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise MalformedProviderResponse(f"{provider} response root must be an object")
    return payload


def parse_mesh_response(payload: object, *, input_term: str, retrieved_at: str) -> TermResolution:
    data = _required_mapping(payload, "MeSH")
    result = data.get("result")
    if not isinstance(result, Mapping):
        raise MalformedProviderResponse("MeSH response missing result object")
    uids = result.get("uids")
    if not isinstance(uids, list):
        raise MalformedProviderResponse("MeSH response missing result.uids")
    records = [result.get(str(uid)) for uid in uids if isinstance(result.get(str(uid)), Mapping)]
    if not records:
        return TermResolution(input_term, None, "unknown", detected_source="MeSH", retrieved_at=retrieved_at)

    record = records[0]
    preferred = _first_text(record, "ds_meshterms", "preferred_term", "name", "title")
    mesh_ui = _first_text(record, "ds_ui", "mesh_ui", "ui", "descriptor_ui")
    ncbi_mesh_entrez_id = _first_text(record, "uid", "ncbi_mesh_entrez_id")
    aliases = _text_list(record, "entry_terms", "synonyms", "ds_concepts")
    candidates = tuple(
        EntityCandidate(
            canonical_name=_first_text(item, "ds_meshterms", "preferred_term", "name", "title"),
            entity_type="biomedical_entity",
            source="MeSH",
            mesh_ui=_first_text(item, "ds_ui", "mesh_ui", "ui", "descriptor_ui"),
            ncbi_mesh_entrez_id=_first_text(item, "uid", "ncbi_mesh_entrez_id"),
            aliases=tuple(make_alias(alias, "MeSH", mesh_entry=True) for alias in _text_list(item, "entry_terms", "synonyms")),
        )
        for item in records
    )
    return TermResolution(
        input_term=input_term,
        canonical_name=preferred,
        entity_type="biomedical_entity",
        mesh_ui=mesh_ui,
        ncbi_mesh_entrez_id=ncbi_mesh_entrez_id,
        aliases=merge_aliases([make_alias(preferred, "MeSH", preferred=True), *(make_alias(alias, "MeSH", mesh_entry=True) for alias in aliases)] if preferred else [make_alias(alias, "MeSH", mesh_entry=True) for alias in aliases]),
        raw_aliases=tuple(make_alias(alias, "MeSH", mesh_entry=True) for alias in aliases),
        ambiguous=len(records) > 1,
        detected_source="MeSH",
        retrieved_at=retrieved_at,
        candidates=candidates,
    )


def parse_mesh_xml_response(payload: str | bytes, *, input_term: str, retrieved_at: str) -> TermResolution:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise MalformedProviderResponse("MeSH XML response is malformed") from exc
    def local(element):
        return element.tag.rsplit('}', 1)[-1]
    records = [element for element in root.iter() if local(element) in {'DescriptorRecord', 'SupplementalRecord'}]
    if not records:
        # Direct-name fixture/legacy payloads; unsupported responses remain unresolved.
        records = [root]
    candidates = []
    for record in records:
        def values(names):
            return [''.join(e.itertext()).strip() for e in record.iter() if local(e) in names and ''.join(e.itertext()).strip()]
        names = values({'DescriptorName', 'SupplementalRecordName'})
        ui_values = values({'DescriptorUI', 'SupplementalRecordUI'})
        if not names or not ui_values:
            continue
        preferred = names[0]
        # Only TermList strings are entry terms. Other concept/tree strings are
        # not aliases, and aliases from distinct records must never be flattened.
        terms = [e for e in record.iter() if local(e) == 'Term']
        aliases = [''.join(e.itertext()).strip() for term in terms for e in term.iter() if local(e) == 'String' and ''.join(e.itertext()).strip()]
        search_aliases = merge_aliases([make_alias(preferred, 'MeSH', preferred=True, retrieved_at=retrieved_at)] + [make_alias(a, 'MeSH', mesh_entry=True, retrieved_at=retrieved_at) for a in aliases])
        candidates.append(EntityCandidate(preferred, 'biomedical_entity', 'MeSH', mesh_ui=ui_values[0], aliases=search_aliases))
    single = candidates[0] if len(candidates) == 1 else None
    return TermResolution(
        input_term=input_term,
        canonical_name=single.canonical_name if single else None,
        entity_type="biomedical_entity",
        mesh_ui=single.mesh_ui if single else None,
        aliases=single.aliases if single else (),
        raw_aliases=tuple(a for c in candidates for a in c.aliases),
        candidates=tuple(candidates),
        ambiguous=len(candidates) > 1,
        detected_source="MeSH",
        retrieved_at=retrieved_at,
    )


def parse_pubchem_response(payload: object, *, input_term: str, retrieved_at: str) -> TermResolution:
    data = _required_mapping(payload, "PubChem")
    properties = data.get("properties")
    if isinstance(properties, list) and properties:
        first = properties[0]
    else:
        information_list = data.get("InformationList")
        information = information_list.get("Information") if isinstance(information_list, Mapping) else None
        first = information[0] if isinstance(information, list) and information else None
    if not isinstance(first, Mapping):
        raise MalformedProviderResponse("PubChem response missing compound information")
    cid = _first_text(first, "CID", "cid")
    synonyms = _text_list(first, "Synonym", "synonyms") or _text_list(data, "synonyms")
    canonical = _first_text(first, "Title", "IUPACName", "ConnectivitySMILES") or (synonyms[0] if synonyms else None)
    if canonical and all(" ".join(alias.casefold().split()) != " ".join(canonical.casefold().split()) for alias in synonyms):
        synonyms = [canonical, *synonyms]
    raw_aliases = tuple(make_alias(alias, "PubChem", preferred=(alias == canonical), retrieved_at=retrieved_at) for alias in synonyms)
    aliases = merge_aliases(alias for alias in raw_aliases if alias.query_eligible)
    return TermResolution(
        input_term=input_term,
        canonical_name=canonical,
        entity_type="compound",
        pubchem_cid=cid,
        aliases=aliases,
        raw_aliases=raw_aliases,
        detected_source="PubChem",
        retrieved_at=retrieved_at,
    )


def _first_text(mapping: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0].strip()
    return None


def _text_list(mapping: Mapping[str, object], *keys: str) -> list[str]:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, list):
            return [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []
