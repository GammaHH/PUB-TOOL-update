"""Persistent before/after screening journal. No destructive history pruning."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

STATES = {'UNREVIEWED', 'KEEP', 'MAYBE', 'EXCLUDE'}


def prefix(project_id):
    return f'screen_history:{int(project_id)}:'


def snapshot(db, project_id, article_id):
    row = db.execute('SELECT status,notes,exclusion_reason,reviewed_at FROM screenings WHERE project_id=? AND article_id=?',
                     (project_id, article_id)).fetchone()
    if row is None:
        return None
    return {'status': row['status'], 'notes': row['notes'] or '',
            'exclusion_reason': row['exclusion_reason'] or '', 'reviewed_at': row['reviewed_at']}


def write_snapshot(db, project_id, article_id, state):
    if state is None:
        db.execute('DELETE FROM screenings WHERE project_id=? AND article_id=?', (project_id, article_id))
        return
    db.execute('''INSERT INTO screenings(project_id,article_id,status,notes,exclusion_reason,reviewed_at) VALUES(?,?,?,?,?,?)
        ON CONFLICT(project_id,article_id) DO UPDATE SET status=excluded.status,notes=excluded.notes,
        exclusion_reason=excluded.exclusion_reason,reviewed_at=excluded.reviewed_at''',
        (project_id, article_id, state['status'], state['notes'], state['exclusion_reason'], state['reviewed_at']))


def entries(db, project_id):
    start = prefix(project_id)
    return [json.loads(row[0]) for row in db.execute(
        'SELECT value_json FROM app_settings WHERE substr(key,1,?)=?', (len(start), start))]


def save_entry(db, project_id, entry):
    db.execute('INSERT INTO app_settings(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
               (prefix(project_id) + entry['id'], json.dumps(entry, ensure_ascii=False)))


def event(pmid, before, after, action='change', undo_of=None):
    return {'id': uuid.uuid4().hex, 'pmid': pmid, 'changed_at': datetime.now(timezone.utc).isoformat(),
            'before': before, 'after': after, 'action': action, 'undo_of': undo_of, 'undone_by': None}


def validate_entries(value, article_ids):
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError('篩選歷史需為陣列，最多 10000 筆')
    clean, identifiers = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('篩選歷史項目無效')
        ident = item.get('id')
        if not isinstance(ident, str) or len(ident) != 32 or any(char not in '0123456789abcdef' for char in ident) or ident in identifiers:
            raise ValueError('篩選歷史 ID 無效或重複')
        identifiers.add(ident)
        if item.get('pmid') not in article_ids or item.get('action') not in {'change', 'undo'}:
            raise ValueError('篩選歷史的 PMID 或操作類型無效')
        stamp = item.get('changed_at')
        if not isinstance(stamp, str) or not stamp or len(stamp) > 100:
            raise ValueError('篩選歷史時間無效')
        states = {}
        for field in ('before', 'after'):
            state = item.get(field)
            if state is not None:
                if not isinstance(state, dict) or not isinstance(state.get('status'), str) or state['status'] not in STATES:
                    raise ValueError('歷史篩選狀態無效')
                for key, maximum in (('notes', 20000), ('exclusion_reason', 2000)):
                    if not isinstance(state.get(key), str) or len(state[key]) > maximum:
                        raise ValueError('歷史筆記或排除理由無效')
                if state['status'] == 'EXCLUDE' and not state['exclusion_reason'].strip():
                    raise ValueError('歷史排除狀態缺少理由')
                reviewed = state.get('reviewed_at')
                if reviewed is not None and (not isinstance(reviewed, str) or len(reviewed) > 100):
                    raise ValueError('歷史審閱時間無效')
                state = {key: state.get(key) for key in ('status', 'notes', 'exclusion_reason', 'reviewed_at')}
            states[field] = state
        clean.append({'id': ident, 'pmid': item['pmid'], 'changed_at': stamp, **states,
            'action': item['action'], 'undo_of': item.get('undo_of'), 'undone_by': item.get('undone_by')})
    by_id = {item['id']: item for item in clean}
    for item in clean:
        for key in ('undo_of', 'undone_by'):
            linked = item[key]
            if linked is not None and (not isinstance(linked, str) or linked == item['id'] or linked not in by_id or by_id[linked]['pmid'] != item['pmid']):
                raise ValueError('篩選歷史撤銷連結無效')
    return sorted(clean, key=lambda item: (item['changed_at'], item['id']))
