"""Local user preference storage; authentication secrets remain in environment."""
import json
import os

from ..api.local_model import DEFAULT_MODEL_SETTINGS, validate_model_settings


def local_model_settings(store):
    warning = None
    with store._connect() as db:
        row = db.execute("SELECT value_json FROM app_settings WHERE key='local_model'").fetchone()
    try:
        value = validate_model_settings(json.loads(row[0])) if row else dict(DEFAULT_MODEL_SETTINGS)
    except (ValueError, TypeError):
        value = dict(DEFAULT_MODEL_SETTINGS)
        warning = "本地模型設定無效，請重新儲存"
    return {"settings": value, "auth_configured": bool(os.getenv("PUB_TOOL_LM_API_KEY")), "warning": warning}


def save_local_model_settings(store, value):
    settings = validate_model_settings(value)
    with store._connect() as db:
        db.execute("""INSERT INTO app_settings(key,value_json) VALUES('local_model',?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                   (json.dumps(settings, ensure_ascii=False),))
    return local_model_settings(store)
