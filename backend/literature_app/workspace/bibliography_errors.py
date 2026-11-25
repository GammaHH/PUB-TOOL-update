import json


def read_errors(db, project_id):
    row = db.execute('SELECT value_json FROM app_settings WHERE key=?',
                     (f'bibliography_errors:{project_id}',)).fetchone()
    return json.loads(row[0]) if row else {}


def save_errors(db, project_id, errors):
    if errors:
        db.execute('INSERT INTO app_settings(key,value_json) VALUES(?,?)',
                   (f'bibliography_errors:{project_id}', json.dumps(errors, ensure_ascii=False)))
