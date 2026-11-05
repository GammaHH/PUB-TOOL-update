"""UTF-8 RIS interchange for EndNote; never invent unfetched citations."""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from .literature import LiteratureStore


def ris_field(tag: str, value) -> str:
    if value is None or value == "":
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise ValueError("RIS 欄位含不支援的控制字元")
    # Six spaces mark continuation lines, including text that looks like a RIS
    # tag. A malicious title/abstract must never inject an ER/TY record boundary.
    lines = text.split("\n")
    return f"{tag}  - {lines[0]}\r\n" + "".join("      " + line + "\r\n" for line in lines[1:])


def export_ris(store, project_id: int, run_id: int, target: Path, scope="ALL"):
    if scope not in {"ALL", "KEEP", "MAYBE", "EXCLUDE", "UNREVIEWED"}:
        raise ValueError("不支援的匯出範圍")
    target = Path(target)
    if target.suffix.lower() != ".ris":
        raise ValueError("請選擇 .ris 檔案")
    with store._connect() as db:
        db.execute("BEGIN")
        run = LiteratureStore(store).require_run(db, project_id, run_id)
        rows = [dict(r) for r in db.execute('''SELECT m.pmid AS member_pmid,a.*,
            COALESCE(s.status,'UNREVIEWED') AS screening_status,
            COALESCE(s.notes,'') AS notes,COALESCE(s.exclusion_reason,'') AS reason
            FROM search_run_pmids m LEFT JOIN articles a ON a.pmid=m.pmid
            LEFT JOIN screenings s ON s.article_id=a.id AND s.project_id=?
            WHERE m.search_run_id=? ORDER BY m.ordinal''', (project_id, run_id))]
    chosen = [r for r in rows if scope == "ALL" or r["screening_status"] == scope]
    ready = [r for r in chosen if r["id"] is not None and r["title"]]
    if not ready:
        raise ValueError("匯出範圍沒有已抓取標題的文獻，請先抓取文章")
    output = []
    for article in ready:
        pmid = article["member_pmid"]
        raw = json.loads(article["raw_payload_json"] or "{}")
        try:
            source = ET.fromstring(raw.get("xml", ""))
        except (ET.ParseError, TypeError):
            source = None
        output.append(ris_field("TY", "GEN" if source is not None and source.tag == "PubmedBookArticle" else "JOUR"))
        for tag, value in (("ID", pmid), ("AN", "PMID:" + pmid), ("TI", article["title"]),
                           ("JO", article["journal"]), ("PY", article["publication_year"]),
                           ("DO", article["doi"]), ("UR", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"),
                           ("DB", "PubMed"), ("AB", article["abstract"])):
            output.append(ris_field(tag, value))
        if source is not None:
            for tag, xpath in (("VL", ".//JournalIssue/Volume"), ("IS", ".//JournalIssue/Issue"),
                               ("J2", ".//Journal/ISOAbbreviation"), ("SN", ".//Journal/ISSN")):
                output.append(ris_field(tag, source.findtext(xpath)))
            pages = source.findtext(".//Pagination/MedlinePgn")
            if pages:
                start, separator, end = pages.partition("-")
                output.append(ris_field("SP", start))
                if separator:
                    output.append(ris_field("EP", end))
        for author in json.loads(article["authors_json"] or "[]"):
            output.append(ris_field("AU", author))
        for keyword in dict.fromkeys(json.loads(article["mesh_terms_json"] or "[]") + json.loads(article["keywords_json"] or "[]")):
            output.append(ris_field("KW", keyword))
        abstract_status = "AVAILABLE" if article["abstract"] else "NO_ABSTRACT"
        for note in (f"PUB-TOOL run {run_id}; screening={article['screening_status']}; abstract={abstract_status}",
                     "Original query: " + run["query"], "Notes: " + article["notes"],
                     "Exclusion reason: " + article["reason"], "PMCID: " + (article["pmcid"] or "")):
            output.append(ris_field("N1", note))
        output.append("ER  - \r\n\r\n")
    payload = "".join(output).encode("utf-8")
    # Exclusive create: a native overwrite confirmation is not permission to
    # destroy an older export, and a concurrent exporter cannot replace it.
    try:
        with target.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise ValueError("檔案已存在，請另選新檔名") from exc
    return {"path": str(target), "rows": len(ready), "skipped": len(chosen) - len(ready)}
