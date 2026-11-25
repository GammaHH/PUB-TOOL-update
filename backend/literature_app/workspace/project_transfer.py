"""Portable project data, independent of local SQLite row identifiers."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .graph import validate_graph
from .bibliography_errors import read_errors, save_errors
from .screening_history import entries as history_entries, save_entry, validate_entries

FORMAT = 'pub-tool-project'
MAX_BYTES = 20_000_000
APPLICATION_ID = 0x50554254  # PUBT
ARTICLE_FIELDS = ('pmid', 'pmcid', 'doi', 'title', 'authors_json', 'journal', 'publication_year',
                  'abstract', 'publication_types_json', 'mesh_terms_json', 'keywords_json',
                  'raw_payload_json', 'fetched_at', 'updated_at')


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, label, maximum=100_000, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f'{label} 必須為文字，最多 {maximum} 字')
    return value


def rows(value, label, maximum=10_000):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(row, dict) for row in value):
        raise ValueError(f'{label} 必須為物件陣列，最多 {maximum} 筆')
    return value


def pmid(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', value):
        raise ValueError('文章需提供真實 PMID（1–10 位正整數文字）')
    return value


def validate_bundle(value):
    if not isinstance(value, dict) or value.get('format') != FORMAT or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('請選擇 PUB-TOOL 專案 JSON（pub-tool-project，version 1）；畫布草稿請從搜尋畫布匯入')
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (TypeError, ValueError) as exc:
        raise ValueError('專案內容不是有效 JSON') from exc
    if size > MAX_BYTES:
        raise ValueError('專案資料超過 20 MB；請將文獻分成多個專案')
    project = value.get('project')
    if not isinstance(project, dict):
        raise ValueError('缺少 project')
    name = text(project.get('name'), '專案名稱', 120).strip()
    if not name:
        raise ValueError('專案名稱不可空白')
    clean = {'format': FORMAT, 'version': 1, 'project': {'name': name,
             'description': text(project.get('description'), '專案說明', nullable=True)},
             'workspace_revisions': [], 'articles': [], 'runs': [], 'screenings': []}
    if 'provenance' in value:
        clean['provenance'] = text(value['provenance'], '資料來源', 10_000)
    revision_ids = set()
    for row in rows(value.get('workspace_revisions', []), '畫布版本', 1000):
        revision = row.get('revision')
        if type(revision) is not int or revision < 1 or revision in revision_ids:
            raise ValueError('畫布版本需為不重複的正整數')
        revision_ids.add(revision)
        clean['workspace_revisions'].append({'revision': revision, 'graph': validate_graph(row.get('graph')),
            'query': text(row.get('query', ''), '搜尋式'), 'created_at': text(row.get('created_at', now()), '畫布時間', 100)})
    clean['workspace_revisions'].sort(key=lambda row: row['revision'])
    article_ids = set()
    for row in rows(value.get('articles', []), '文獻'):
        ident = pmid(row.get('pmid'))
        if ident in article_ids:
            raise ValueError(f'文章 PMID 重複：{ident}')
        article_ids.add(ident)
        article = {'pmid': ident}
        for field in ARTICLE_FIELDS[1:]:
            item = row.get(field)
            if field == 'publication_year':
                if item is not None and (type(item) is not int or not 1 <= item <= 9999):
                    raise ValueError('出版年份無效')
            else:
                item = text(item, field, nullable=True)
                if field.endswith('_json') and item is not None:
                    try:
                        decoded = json.loads(item, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
                    except (ValueError, TypeError) as exc:
                        raise ValueError(f'{field} 不是有效 JSON') from exc
                    if field != 'raw_payload_json' and not isinstance(decoded, list):
                        raise ValueError(f'{field} 需為 JSON 陣列文字')
            article[field] = item
        article['updated_at'] = article['updated_at'] or now()
        clean['articles'].append(article)
    for row in rows(value.get('runs', []), '文獻清單', 1000):
        identifiers = row.get('pmids')
        if not isinstance(identifiers, list) or len(identifiers) > 10_000:
            raise ValueError('文獻清單 pmids 無效')
        identifiers = [pmid(item) for item in identifiers]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('同一文獻清單內 PMID 不可重複')
        count = row.get('result_count', len(identifiers))
        limit = row.get('requested_limit', len(identifiers))
        if type(count) is not int or count < len(identifiers) or type(limit) is not int or limit < len(identifiers):
            raise ValueError('命中篇數或收集上限小於清單篇數')
        warnings = row.get('warnings', [])
        # ESearch stores warninglist as an object; the portable format displays
        # those structured warnings as lossless JSON text in its string list.
        if isinstance(warnings, dict):
            warnings = [json.dumps(warnings, ensure_ascii=False, allow_nan=False)] if warnings else []
        if not isinstance(warnings, list) or any(not isinstance(item, str) or len(item) > 10_000 for item in warnings):
            raise ValueError('文獻清單 warnings 無效')
        failures = rows(row.get('failures', []), '抓取失敗')
        failure_ids = set()
        clean_failures = []
        for failure in failures:
            ident = pmid(failure.get('pmid'))
            if ident not in identifiers or ident in failure_ids:
                raise ValueError('抓取失敗 PMID 不屬於清單或重複')
            failure_ids.add(ident)
            clean_failures.append({'pmid': ident, 'error': text(failure.get('error'), '失敗原因', 10_000),
                'attempted_at': text(failure.get('attempted_at', now()), '抓取時間', 100)})
        clean['runs'].append({'query': text(row.get('query', ''), '清單搜尋式'),
            'database_name': text(row.get('database_name', 'curated_import'), '清單來源', 100),
            'searched_at': text(row.get('searched_at', now()), '清單時間', 100),
            'result_count': count, 'requested_limit': limit, 'pmids': identifiers,
            'query_translation': text(row.get('query_translation', ''), '搜尋式轉譯'),
            'warnings': warnings, 'failures': clean_failures})
    listed = {ident for run in clean['runs'] for ident in run['pmids']}
    if article_ids - listed:
        raise ValueError('每篇文章都需屬於至少一個 runs 文獻清單')
    screening_ids = set()
    for row in rows(value.get('screenings', []), '閱讀備註'):
        ident = pmid(row.get('pmid'))
        status = row.get('status', 'UNREVIEWED')
        if ident not in article_ids or ident in screening_ids or status not in {'UNREVIEWED', 'KEEP', 'MAYBE', 'EXCLUDE'}:
            raise ValueError('閱讀備註的文章、重複 PMID 或篩選狀態無效')
        screening_ids.add(ident)
        reason = text(row.get('exclusion_reason', ''), '排除理由', 2000)
        if status == 'EXCLUDE' and not reason.strip():
            raise ValueError('排除文獻需填寫理由')
        clean['screenings'].append({'pmid': ident, 'status': status,
            'notes': text(row.get('notes', ''), '閱讀備註', 20_000), 'exclusion_reason': reason,
            'reviewed_at': text(row.get('reviewed_at'), '篩選時間', 100, nullable=True)})
    errors = value.get('bibliography_errors', {})
    if not isinstance(errors, dict) or errors.keys() - article_ids:
        raise ValueError('書目錯誤包含無效的 PMID')
    clean['bibliography_errors'] = {}
    for ident, error in errors.items():
        if not isinstance(error, dict):
            raise ValueError('書目錯誤格式無效')
        clean['bibliography_errors'][ident] = {
            key: text(error.get(key), key) for key in ('local_doi', 'imported_doi')
        }
    clean['screening_history'] = validate_entries(value.get('screening_history', []), article_ids)
    return clean


def preview_bundle(bundle):
    clean = validate_bundle(bundle)
    return {'name': clean['project']['name'], 'description': clean['project']['description'],
            'articles': len(clean['articles']), 'runs': len(clean['runs']),
            'notes': sum(bool(row['notes'].strip()) for row in clean['screenings']),
            'screenings': len(clean['screenings']), 'revisions': len(clean['workspace_revisions']),
            'history_entries': len(clean['screening_history']),
            'provenance': clean.get('provenance', ''),
            'warnings': sorted({warning for run in clean['runs'] for warning in run['warnings']})}


def project_bundle(store, project_id):
    with store._connect() as db:
        # A consistent read snapshot across the related tables.
        db.execute('BEGIN')
        project = db.execute('SELECT name,description FROM projects WHERE id=?', (project_id,)).fetchone()
        if project is None:
            raise ValueError('找不到專案')
        bundle = {'format': FORMAT, 'version': 1, 'project': dict(project),
                  'provenance': 'PUB-TOOL 專案匯出；搜尋清單保留原始來源與時間。',
                  'workspace_revisions': [], 'articles': [], 'runs': [], 'screenings': []}
        for row in db.execute('SELECT * FROM workspace_revisions WHERE project_id=? ORDER BY revision', (project_id,)):
            bundle['workspace_revisions'].append({'revision': row['revision'], 'graph': json.loads(row['graph_json']),
                'query': row['query_text'], 'created_at': row['created_at']})
        runs = db.execute('SELECT * FROM search_runs WHERE project_id=? ORDER BY id', (project_id,)).fetchall()
        for row in runs:
            detail = db.execute('SELECT * FROM pubmed_run_details WHERE run_id=?', (row['id'],)).fetchone()
            ids = [item[0] for item in db.execute('SELECT pmid FROM search_run_pmids WHERE search_run_id=? ORDER BY ordinal', (row['id'],))]
            bundle['runs'].append({'query': row['query'], 'database_name': row['database_name'],
                'searched_at': row['searched_at'], 'result_count': row['result_count'], 'pmids': ids,
                'requested_limit': detail['requested_limit'] if detail else len(ids),
                'query_translation': detail['query_translation'] if detail else '',
                'warnings': json.loads(detail['warnings_json']) if detail else [],
                'failures': [dict(item) for item in db.execute('SELECT pmid,error,attempted_at FROM pubmed_fetch_failures WHERE run_id=?', (row['id'],))]})
        columns = ','.join('a.' + field for field in ARTICLE_FIELDS)
        bundle['articles'] = [dict(row) for row in db.execute(f'''SELECT DISTINCT {columns} FROM articles a
            JOIN search_run_pmids m ON m.pmid=a.pmid JOIN search_runs r ON r.id=m.search_run_id
            WHERE r.project_id=? ORDER BY a.pmid''', (project_id,))]
        bundle['screenings'] = [dict(row) for row in db.execute('''SELECT a.pmid,s.status,s.notes,s.exclusion_reason,s.reviewed_at
            FROM screenings s JOIN articles a ON a.id=s.article_id WHERE s.project_id=? ORDER BY a.pmid''', (project_id,))]
        bundle['screening_history'] = history_entries(db, project_id)
        bundle['bibliography_errors'] = read_errors(db, project_id)
    # Old databases can hold NULL note/reason strings; portable records use text.
    for row in bundle['screenings']:
        row['notes'] = row['notes'] or ''
        row['exclusion_reason'] = row['exclusion_reason'] or ''
    return validate_bundle(bundle)


class TransferFile:
    """Never initialize or migrate an imported file."""
    def __init__(self, path, read_only=True):
        self.path, self.read_only = Path(path), read_only

    @contextmanager
    def _connect(self):
        mode = 'ro' if self.read_only else 'rw'
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=' + mode, uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA foreign_keys=ON')
            if self.read_only:
                db.execute('PRAGMA query_only=ON')
            with db:
                yield db
        finally:
            db.close()

    def load(self, project_id):
        from .store import WorkspaceStore
        return WorkspaceStore.load(self, project_id)


def read_project_file(source):
    path = Path(source)
    if path.stat().st_size > 100_000_000:
        raise ValueError('檔案超過 100 MB')
    with path.open('rb') as stream:
        header = stream.read(16)
    if header != b'SQLite format 3\x00':
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('JSON 專案檔超過 20 MB')
        return validate_bundle(json.loads(path.read_text(encoding='utf-8-sig')))
    source_store = TransferFile(path)
    with source_store._connect() as db:
        if db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('不是支援的 PUB-TOOL 單一專案檔；請從「專案匯入／匯出」重新匯出')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():
            raise ValueError('專案檔含不支援的 view／trigger')
        if [row[0] for row in db.execute('PRAGMA integrity_check')] != ['ok'] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('專案資料庫完整性檢查失敗')
        projects = db.execute('SELECT id FROM projects').fetchall()
        if len(projects) != 1:
            raise ValueError('專案檔需包含且僅包含一個專案')
        metadata = db.execute("SELECT value_json FROM app_settings WHERE key='project_transfer'").fetchone()
        if metadata is None:
            raise ValueError('缺少專案檔版本資訊')
        metadata = json.loads(metadata[0])
        if not isinstance(metadata, dict) or metadata.get('format') != FORMAT or metadata.get('version') != 1:
            raise ValueError('不支援的專案檔版本')
    bundle = project_bundle(source_store, projects[0]['id'])
    bundle['provenance'] = text(metadata.get('provenance', bundle['provenance']), '資料來源', 10_000)
    return validate_bundle(bundle)


def export_project(store, project_id, target):
    bundle = project_bundle(store, project_id)
    path = Path(target)
    # Exclusive reservation: an older export or the live database cannot be replaced.
    with path.open('xb'):
        pass
    destination = TransferFile(path, read_only=False)
    schema_path = Path(__file__).resolve().parents[3] / 'database' / 'schema.sql'
    with destination._connect() as db:
        db.executescript(schema_path.read_text(encoding='utf-8'))
        db.execute('PRAGMA user_version=1')
        db.execute(f'PRAGMA application_id={APPLICATION_ID}')
    import_project(destination, bundle, bundle['project']['name'])
    # Store exact original description; provenance is recorded separately in this file.
    with destination._connect() as db:
        db.execute('UPDATE projects SET description=?', (bundle['project']['description'],))
        db.execute('INSERT INTO app_settings VALUES(?,?)', ('project_transfer', json.dumps({
            'format': FORMAT, 'version': 1, 'exported_at': now(), 'provenance': bundle.get('provenance', '')}, ensure_ascii=False)))
    reopened = read_project_file(path)
    if reopened != bundle:
        raise ValueError('匯出後重新讀取驗證不一致')
    return {'path': str(path), **preview_bundle(bundle)}


def import_project(store, bundle, name):
    clean = validate_bundle(bundle)
    name = text(name, '新專案名稱', 120).strip()
    if not name:
        raise ValueError('請填寫新專案名稱')
    stamp = now()
    reused = 0
    errors = dict(clean['bibliography_errors'])
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM projects WHERE name=?', (name,)).fetchone():
            raise ValueError('已有同名專案，請為匯入副本使用其他名稱')
        description = clean['project']['description'] or ''
        if clean.get('provenance'):
            description += '\n資料來源：' + clean['provenance']
        project_id = db.execute('INSERT INTO projects(name,description,created_at,updated_at) VALUES(?,?,?,?)',
                                (name, description, stamp, stamp)).lastrowid
        for row in clean['workspace_revisions']:
            db.execute('INSERT INTO workspace_revisions VALUES(?,?,?,?,?)', (project_id, row['revision'],
                json.dumps(row['graph'], ensure_ascii=False, allow_nan=False), row['query'], row['created_at']))
        article_map = {}
        for row in clean['articles']:
            existing = db.execute('SELECT id,doi,title FROM articles WHERE pmid=?', (row['pmid'],)).fetchone()
            if existing:
                # Metadata is shared among projects: never replace an existing article.
                if existing['doi'] and row['doi'] and existing['doi'].strip().lower() != row['doi'].strip().lower():
                    errors.setdefault(row['pmid'], {'local_doi': existing['doi'], 'imported_doi': row['doi']})
                ident = existing['id']
                reused += 1
            else:
                ident = db.execute(f"INSERT INTO articles({','.join(ARTICLE_FIELDS)}) VALUES({','.join('?' for _ in ARTICLE_FIELDS)})",
                                   tuple(row[field] for field in ARTICLE_FIELDS)).lastrowid
            article_map[row['pmid']] = ident
        for row in clean['runs']:
            run_id = db.execute('''INSERT INTO search_runs(project_id,query,query_hash,database_name,searched_at,
                result_count,app_version,history_server,history_key) VALUES(?,?,?,?,?,?,?,?,?)''',
                (project_id, row['query'], hashlib.sha256(row['query'].encode()).hexdigest(), row['database_name'],
                 row['searched_at'], row['result_count'], 'project-import-v1', None, None)).lastrowid
            db.executemany('INSERT INTO search_run_pmids VALUES(?,?,?)', [(run_id, ident, index) for index, ident in enumerate(row['pmids'])])
            db.execute('INSERT INTO pubmed_run_details VALUES(?,?,?,?,?)', (run_id, len(row['pmids']),
                row['requested_limit'], row['query_translation'], json.dumps(row['warnings'], ensure_ascii=False)))
            db.executemany('INSERT INTO pubmed_fetch_failures VALUES(?,?,?,?)',
                [(run_id, item['pmid'], item['error'], item['attempted_at']) for item in row['failures']])
        for row in clean['screenings']:
            db.execute('INSERT INTO screenings(project_id,article_id,status,notes,exclusion_reason,reviewed_at) VALUES(?,?,?,?,?,?)',
                (project_id, article_map[row['pmid']], row['status'], row['notes'], row['exclusion_reason'], row['reviewed_at']))
        for entry in clean['screening_history']:
            save_entry(db, project_id, entry)
        save_errors(db, project_id, errors)
    return {'workspace': store.load(project_id), 'reused_articles': reused,
            'bibliography_error_count': len(errors), **preview_bundle(clean)}
