"""NCBI ESearch/EFetch adapter. No abstract snippets or automatic retries."""
from __future__ import annotations

import os
from xml.etree import ElementTree as ET

import httpx

from .ports import ArticleRecord
from .reliability import RateLimiter

NCBI_LIMITER = RateLimiter(3)


def text(element) -> str:
    return ''.join(element.itertext()).strip() if element is not None else ''


def parse_articles(xml: str) -> list[ArticleRecord]:
    root = ET.fromstring(xml)
    if root.tag != 'PubmedArticleSet' or root.find('.//ERROR') is not None:
        raise ValueError('PubMed 回傳的 XML 不是有效文章集')
    records = []
    for item in root:
        if item.tag not in {'PubmedArticle', 'PubmedBookArticle'}:
            continue
        citation = item.find('MedlineCitation') if item.tag == 'PubmedArticle' else item.find('BookDocument')
        if citation is None:
            continue
        article = citation.find('Article') if item.tag == 'PubmedArticle' else citation
        if article is None:
            continue
        pmid = text(citation.find('PMID'))
        if not pmid.isdigit():
            raise ValueError('PubMed XML 缺少有效 PMID')
        sections = []
        for part in article.findall('Abstract/AbstractText'):
            # itertext preserves nested italic/sup/sub text without truncating.
            sections.append({'label': part.get('Label') or part.get('NlmCategory') or '', 'text': text(part)})
        abstract = '\n\n'.join((p['label'] + ': ' if p['label'] else '') + p['text'] for p in sections)
        ids = {p.get('IdType'): text(p) for p in item.findall('.//ArticleIdList/ArticleId')}
        authors = []
        for author in article.findall('AuthorList/Author'):
            name = text(author.find('CollectiveName')) or ' '.join(filter(None, [text(author.find('ForeName')), text(author.find('LastName'))]))
            if name:
                authors.append(name)
        year = text(article.find('Journal/JournalIssue/PubDate/Year'))
        if not year:
            import re
            match = re.search(r'\b(\d{4})\b', text(article.find('Journal/JournalIssue/PubDate/MedlineDate')))
            year = match.group(1) if match else ''
        records.append(ArticleRecord(
            pmid=pmid, title=text(article.find('ArticleTitle')), abstract=abstract or None,
            authors=tuple(authors), journal=text(article.find('Journal/Title')),
            publication_year=int(year) if year.isdigit() else None, doi=ids.get('doi'), pmcid=ids.get('pmc'),
            publication_types=tuple(text(p) for p in article.findall('PublicationTypeList/PublicationType')),
            mesh_terms=tuple(text(p) for p in citation.findall('MeshHeadingList/MeshHeading/DescriptorName')),
            keywords=tuple(text(p) for p in citation.findall('KeywordList/Keyword')),
            raw_payload={'xml': ET.tostring(item, encoding='unicode'), 'abstract_sections': sections,
                         'abstract_status': 'AVAILABLE' if abstract else 'NO_ABSTRACT',
                         'copyright': text(article.find('Abstract/CopyrightInformation'))},
        ))
    if len({r.pmid for r in records}) != len(records):
        raise ValueError('PubMed XML 包含重複 PMID')
    return records


class PubMedClient:
    base = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'

    def __init__(self, client: httpx.Client):
        self.client = client
        self.limiter = NCBI_LIMITER

    def _post(self, endpoint, params):
        self.limiter.wait()
        config = {'tool': os.getenv('NCBI_TOOL_NAME', 'PUB_TOOL')}
        for env, key in [('NCBI_EMAIL', 'email'), ('NCBI_API_KEY', 'api_key')]:
            if os.getenv(env):
                config[key] = os.environ[env]
        try:
            response = self.client.post(self.base + endpoint, data={**config, **params})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Never expose request URL/body containing API credentials.
            code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 'network/timeout'
            raise ValueError(f'NCBI 請求失敗（{code}）；未自動重試，已儲存資料保留。') from None
        return response

    def search(self, query: str, limit: int = 500):
        if not isinstance(query, str) or not query.strip() or len(query) > 100000:
            raise ValueError('請提供非空搜尋式（最多 100000 字）')
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('本次收集上限需為 1–10000')
        # One ESearch snapshot, no drift across separate searches/pages.
        payload = self._post('esearch.fcgi', {'db': 'pubmed', 'term': query, 'retmode': 'json', 'retmax': limit, 'usehistory': 'y', 'sort': 'pub_date'}).json()
        result = payload.get('esearchresult')
        if not isinstance(result, dict) or payload.get('error') or result.get('ERROR') or result.get('errorlist'):
            raise ValueError('PubMed 搜尋回應無效，請檢查搜尋式')
        count = int(result['count'])
        pmids = result.get('idlist')
        if count < 0 or not isinstance(pmids, list) or any(not isinstance(p, str) or not p.isdigit() for p in pmids) or len(set(pmids)) != len(pmids) or len(pmids) != min(count, limit):
            raise ValueError('PubMed PMID 清單不完整或無效；未建立搜尋紀錄')
        return {'query': query, 'count': count, 'pmids': pmids, 'limit': limit,
                'complete': count == len(pmids), 'translation': result.get('querytranslation', ''),
                'warnings': result.get('warninglist', {}), 'webenv': result.get('webenv'), 'querykey': result.get('querykey')}

    def fetch(self, pmids: list[str]):
        if not pmids or len(pmids) > 100 or any(not p.isdigit() for p in pmids):
            raise ValueError('每批需為 1–100 個 PMID')
        response = self._post('efetch.fcgi', {'db': 'pubmed', 'id': ','.join(pmids), 'retmode': 'xml', 'rettype': 'abstract'})
        records = parse_articles(response.text)
        if any(r.pmid not in pmids for r in records):
            raise ValueError('EFetch 回傳非請求的 PMID；整批未寫入')
        return records
