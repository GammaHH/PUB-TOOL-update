from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone

from .store import WorkspaceStore


def now():
    return datetime.now(timezone.utc).isoformat()


class LiteratureStore:
    def __init__(self, store: WorkspaceStore):
        self.store = store

    def save_search(self, project_id, result):
        with self.store._connect() as db:
            run = db.execute('INSERT INTO search_runs(project_id,query,query_hash,database_name,searched_at,result_count,app_version,history_server,history_key) VALUES(?,?,?,?,?,?,?,?,?)',
                             (project_id, result['query'], hashlib.sha256(result['query'].encode()).hexdigest(), 'pubmed', now(), result['count'], '0.3.0', result['webenv'], result['querykey'])).lastrowid
            db.executemany('INSERT INTO search_run_pmids VALUES(?,?,?)', [(run, p, i) for i, p in enumerate(result['pmids'])])
            db.execute('INSERT INTO pubmed_run_details VALUES(?,?,?,?,?)', (run, len(result['pmids']), result['limit'], result['translation'], json.dumps(result['warnings'], ensure_ascii=False)))
        return run

    def runs(self, project_id):
        with self.store._connect() as db:
            return [dict(r) for r in db.execute('''SELECT s.*, d.collected_count, d.requested_limit, d.query_translation, d.warnings_json
                FROM search_runs s JOIN pubmed_run_details d ON d.run_id=s.id WHERE s.project_id=? ORDER BY s.id DESC''', (project_id,))]

    def require_run(self, db, project_id, run_id):
        row = db.execute('SELECT * FROM search_runs WHERE id=? AND project_id=?', (run_id, project_id)).fetchone()
        if row is None:
            raise ValueError('搜尋紀錄不屬於此專案')
        return dict(row)

    def pending(self, project_id, run_id, include_failed=False):
        with self.store._connect() as db:
            self.require_run(db, project_id, run_id)
            rows = db.execute('''SELECT m.pmid FROM search_run_pmids m LEFT JOIN articles a ON a.pmid=m.pmid
                LEFT JOIN pubmed_fetch_failures f ON f.run_id=m.search_run_id AND f.pmid=m.pmid
                WHERE m.search_run_id=? AND a.id IS NULL AND
                ((?=1 AND f.pmid IS NOT NULL) OR (?=0 AND f.pmid IS NULL)) ORDER BY m.ordinal LIMIT 50''', (run_id, int(include_failed), int(include_failed)))
            return [r['pmid'] for r in rows]

    def save_batch(self, project_id, run_id, requested, records, error=None):
        stamp = now()
        with self.store._connect() as db:
            self.require_run(db, project_id, run_id)
            for r in records:
                db.execute('''INSERT INTO articles(pmid,pmcid,doi,title,authors_json,journal,publication_year,abstract,publication_types_json,mesh_terms_json,keywords_json,raw_payload_json,fetched_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(pmid) DO UPDATE SET
                    pmcid=excluded.pmcid,doi=excluded.doi,title=excluded.title,authors_json=excluded.authors_json,journal=excluded.journal,
                    publication_year=excluded.publication_year,abstract=excluded.abstract,publication_types_json=excluded.publication_types_json,
                    mesh_terms_json=excluded.mesh_terms_json,keywords_json=excluded.keywords_json,raw_payload_json=excluded.raw_payload_json,
                    fetched_at=excluded.fetched_at,updated_at=excluded.updated_at''',
                           (r.pmid, r.pmcid, r.doi, r.title, json.dumps(r.authors), r.journal, r.publication_year, r.abstract,
                            json.dumps(r.publication_types), json.dumps(r.mesh_terms), json.dumps(r.keywords), json.dumps(r.raw_payload, ensure_ascii=False), stamp, stamp))
                db.execute('DELETE FROM pubmed_fetch_failures WHERE run_id=? AND pmid=?', (run_id, r.pmid))
            returned = {r.pmid for r in records}
            for pmid in requested:
                if pmid not in returned:
                    db.execute('INSERT OR REPLACE INTO pubmed_fetch_failures VALUES(?,?,?,?)', (run_id, pmid, error or 'EFetch 未回傳此 PMID，可能刪除或暫不可取得', stamp))

    def articles(self, project_id, run_id):
        with self.store._connect() as db:
            self.require_run(db, project_id, run_id)
            rows = [dict(r) for r in db.execute('''SELECT m.pmid,m.ordinal,a.id AS article_id,a.title,a.journal,a.publication_year,
                a.abstract,a.doi,a.pmcid,a.fetched_at,COALESCE(s.status,'UNREVIEWED') AS status,
                COALESCE(s.notes,'') AS notes,COALESCE(s.exclusion_reason,'') AS exclusion_reason,f.error AS fetch_error
                FROM search_run_pmids m LEFT JOIN articles a ON a.pmid=m.pmid
                LEFT JOIN screenings s ON s.article_id=a.id AND s.project_id=?
                LEFT JOIN pubmed_fetch_failures f ON f.run_id=m.search_run_id AND f.pmid=m.pmid
                WHERE m.search_run_id=? ORDER BY m.ordinal''', (project_id, run_id))]
        for row in rows:
            row['abstract_status'] = 'FETCH_FAILED' if row['article_id'] is None and row['fetch_error'] else 'NOT_FETCHED' if row['article_id'] is None else 'AVAILABLE' if row['abstract'] else 'NO_ABSTRACT'
        return rows

    def screen(self, project_id, run_id, pmid, status, notes, reason):
        if status not in {'UNREVIEWED', 'KEEP', 'MAYBE', 'EXCLUDE'}:
            raise ValueError('不支援的篩選狀態')
        if not isinstance(notes, str) or not isinstance(reason, str) or len(notes) > 20000 or len(reason) > 2000:
            raise ValueError('備註或排除理由過長')
        if status == 'EXCLUDE' and not reason.strip():
            raise ValueError('排除需填寫理由')
        with self.store._connect() as db:
            self.require_run(db, project_id, run_id)
            row = db.execute('SELECT a.id FROM articles a JOIN search_run_pmids m ON m.pmid=a.pmid WHERE m.search_run_id=? AND a.pmid=?', (run_id, pmid)).fetchone()
            if row is None:
                raise ValueError('文章尚未抓取或不在此搜尋紀錄')
            db.execute('''INSERT INTO screenings(project_id,article_id,status,notes,exclusion_reason,reviewed_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(project_id,article_id) DO UPDATE SET status=excluded.status,notes=excluded.notes,exclusion_reason=excluded.exclusion_reason,reviewed_at=excluded.reviewed_at''',
                       (project_id, row['id'], status, notes, reason if status == 'EXCLUDE' else '', now()))

    def export_html(self, project_id, run_id, only_kept=False, pmid=None):
        with self.store._connect() as db:
            run = self.require_run(db, project_id, run_id)
        articles = self.articles(project_id, run_id)
        if only_kept:
            articles = [a for a in articles if a['status'] == 'KEEP']
        if pmid is not None:
            articles = [a for a in articles if a['pmid'] == pmid]
        e = lambda value: html.escape(str(value or ''))
        content = ['<!DOCTYPE html><html><head><meta charset="utf-8"/><title>PUB-TOOL Reading Pack</title></head><body>',
                   '<h1>PubMed reading pack</h1><p>Search: ' + e(run['query']) + '</p>',
                   f'<p>Run {run_id} · total {run["result_count"]} · collected {len(self.articles(project_id, run_id))} · exported {len(articles)} · {e(now())}</p>']
        for a in articles:
            content += ['<h2>' + e(a['title'] or ('PMID ' + a['pmid'])) + '</h2>',
                        '<p>' + e(a['journal']) + ' · ' + e(a['publication_year']) + ' · ' + e(a['status']) + '</p>',
                        f'<p><a href="https://pubmed.ncbi.nlm.nih.gov/{a["pmid"]}/">PMID {a["pmid"]}</a> · DOI: {e(a["doi"])}</p>',
                        '<p>' + e(a['abstract'] or a['abstract_status']).replace('\n', '<br/>') + '</p>',
                        '<p>Notes: ' + e(a['notes']).replace('\n', '<br/>') + '</p>',
                        '<p>Exclusion reason: ' + e(a['exclusion_reason']) + '</p>']
        return ''.join(content) + '</body></html>'
