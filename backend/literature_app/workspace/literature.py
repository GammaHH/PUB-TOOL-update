from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone

from .store import WorkspaceStore
from .bibliography_errors import read_errors
from .screening_history import snapshot, write_snapshot, entries, save_entry, event, prefix


def now():
    return datetime.now(timezone.utc).isoformat()


def abstract_state(abstract, fetched_at, fetch_error=None):
    """An imported citation is not evidence that PubMed returned no abstract."""
    if not fetched_at:
        return 'FETCH_FAILED' if fetch_error else 'NOT_FETCHED'
    return 'AVAILABLE' if abstract else 'NO_ABSTRACT'


class LiteratureStore:
    def __init__(self, store: WorkspaceStore):
        self.store = store

    def save_search(self, project_id, result, *, app_version="0.3.0"):
        with self.store._connect() as db:
            run = db.execute('INSERT INTO search_runs(project_id,query,query_hash,database_name,searched_at,result_count,app_version,history_server,history_key) VALUES(?,?,?,?,?,?,?,?,?)',
                             (project_id, result['query'], hashlib.sha256(result['query'].encode()).hexdigest(), 'pubmed', now(), result['count'], app_version, result['webenv'], result['querykey'])).lastrowid
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
                WHERE m.search_run_id=? AND COALESCE(a.fetched_at,'')='' AND
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
            errors = read_errors(db, project_id)
        for row in rows:
            row['abstract_status'] = abstract_state(row['abstract'], row['fetched_at'], row['fetch_error'])
            row['bibliography_error'] = errors.get(row['pmid'])
        return rows

    def screen(self, project_id, run_id, pmid, status, notes, reason):
        if status not in {'UNREVIEWED', 'KEEP', 'MAYBE', 'EXCLUDE'}:
            raise ValueError('不支援的篩選狀態')
        if not isinstance(notes, str) or not isinstance(reason, str) or len(notes) > 20000 or len(reason) > 2000:
            raise ValueError('備註或排除理由過長')
        if status == 'EXCLUDE' and not reason.strip():
            raise ValueError('排除需填寫理由')
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_active_project(project_id, db)
            self.require_run(db, project_id, run_id)
            row = db.execute('SELECT a.id FROM articles a JOIN search_run_pmids m ON m.pmid=a.pmid WHERE m.search_run_id=? AND a.pmid=?', (run_id, pmid)).fetchone()
            if row is None:
                raise ValueError('文章尚未抓取或不在此搜尋紀錄')
            before = snapshot(db, project_id, row['id'])
            after = {'status': status, 'notes': notes, 'exclusion_reason': reason if status == 'EXCLUDE' else '', 'reviewed_at': now()}
            if before is not None and all(before[key] == after[key] for key in ('status', 'notes', 'exclusion_reason')):
                return
            write_snapshot(db, project_id, row['id'], after)
            save_entry(db, project_id, event(pmid, before, after))

    def screening_history(self, project_id, limit=25):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('歷史顯示筆數需為 1–500')
        with self.store._connect() as db:
            db.execute('BEGIN')
            self.store.require_active_project(project_id, db)
            history = entries(db, project_id)
            history.sort(key=lambda item: (item['changed_at'], item['id']), reverse=True)
            visible = []
            for item in history[:limit]:
                article = db.execute('SELECT id,title FROM articles WHERE pmid=?', (item['pmid'],)).fetchone()
                can_undo = article is not None and not item.get('undone_by') and snapshot(db, project_id, article['id']) == item['after']
                visible.append({**item, 'title': article['title'] if article else None, 'can_undo': can_undo})
        return {'total': len(history), 'items': visible}

    def undo_screening(self, project_id, event_id):
        if not isinstance(event_id, str) or len(event_id) != 32 or any(char not in '0123456789abcdef' for char in event_id):
            raise ValueError('歷史 ID 無效')
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_active_project(project_id, db)
            row = db.execute('SELECT value_json FROM app_settings WHERE key=?', (prefix(project_id) + event_id,)).fetchone()
            if row is None:
                raise ValueError('找不到此專案的篩選歷史')
            original = json.loads(row[0])
            article = db.execute('SELECT id FROM articles WHERE pmid=?', (original['pmid'],)).fetchone()
            if original.get('undone_by') or article is None or snapshot(db, project_id, article['id']) != original['after']:
                raise ValueError('此紀錄已撤銷或該篇有較新的變更，請重新整理歷史')
            inverse = event(original['pmid'], original['after'], original['before'], 'undo', original['id'])
            write_snapshot(db, project_id, article['id'], original['before'])
            original['undone_by'] = inverse['id']
            save_entry(db, project_id, original)
            save_entry(db, project_id, inverse)
        return {'undone': True, 'pmid': original['pmid']}

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
