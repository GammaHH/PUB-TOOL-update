"""Central declarative style registry shared by all PUB-TOOL modules.

Modules register controls here and consume the corresponding CSS variable.
User imports contain values only, never selectors, JavaScript or raw CSS.
"""
from __future__ import annotations

import json
import math
import re


def color(ident, title, default):
    return {"id": ident, "title": title, "type": "color", "default": default, "css_var": "--pub-" + ident}


def slider(ident, title, default, minimum, maximum, step, unit=""):
    return {"id": ident, "title": title, "type": "slider", "default": default,
            "min": minimum, "max": maximum, "step": step, "unit": unit, "css_var": "--pub-" + ident}


def toggle(ident, title, default):
    return {"id": ident, "title": title, "type": "toggle", "default": default}


GROUPS = [
    {"id": "interface", "title": "全域介面", "description": "共用的背景、文字、重點色與側邊欄。", "settings": [
        color("ui-background", "主背景", "#0c1220"), color("ui-sidebar", "側邊欄", "#101827"),
        color("ui-panel", "面板背景", "#121c2d"), color("ui-text", "主要文字", "#dbe4f1"),
        color("ui-accent", "重點色", "#a9c6ef"), color("ui-border", "邊框", "#253047"),
        slider("ui-sidebar-width", "側邊欄寬度", 235, 170, 320, 5, "px"),
        toggle("ui-compact", "緊湊工具列", False),
    ]},
    {"id": "canvas", "title": "搜尋畫布", "description": "畫布顯示設定；搜尋條件與節點邏輯保持獨立。", "settings": [
        color("canvas-background", "畫布背景", "#0b1424"),
        toggle("canvas-grid-visible", "顯示點狀格線", True),
        slider("canvas-grid-gap", "格線間距", 24, 12, 60, 1, "px"),
        slider("canvas-edge-width", "連線寬度", 1.6, 1, 4, 0.2, "px"),
    ]},
    {"id": "nodes", "title": "節點卡片", "description": "保留節點編號與內容，個別調整不同節點的外觀。", "settings": [
        color("node-concept", "概念節點背景", "#1b2c45"), color("node-logic", "AND／OR 節點背景", "#1b3439"),
        color("node-output", "搜尋式節點背景", "#2b2944"),
        slider("node-font-size", "節點標題字級", 13, 10, 20, 1, "px"),
        slider("node-radius", "節點圓角", 9, 0, 20, 1, "px"),
        toggle("node-shadow", "節點陰影", True),
    ]},
    {"id": "reader", "title": "文獻閱讀", "description": "摘要內容與閱讀面板的顯示。", "settings": [
        slider("reader-font-size", "摘要字級", 15, 12, 24, 1, "px"),
        slider("reader-line-height", "摘要行距", 1.95, 1.3, 2.4, 0.05),
        {"id": "reader-font", "title": "摘要字體", "type": "select", "default": "serif", "options": [
            {"value": "serif", "label": "襯線（原始）"}, {"value": "sans", "label": "無襯線"},
        ]},
    ]},
]
CONTROLS = {item["id"]: item for group in GROUPS for item in group["settings"]}
DEFAULTS = {key: item["default"] for key, item in CONTROLS.items()}
PRESETS = [
    {"id": "original", "title": "原始深藍", "values": DEFAULTS},
    {"id": "graphite", "title": "石墨灰", "values": {**DEFAULTS,
        "ui-background": "#17191d", "ui-sidebar": "#1c1e23", "ui-panel": "#25282e",
        "ui-text": "#e0e2e6", "ui-accent": "#b4b8e0", "ui-border": "#3a3e47", "canvas-background": "#15171b"}},
    {"id": "warm", "title": "暖棕", "values": {**DEFAULTS,
        "ui-background": "#211c19", "ui-sidebar": "#28221e", "ui-panel": "#302923",
        "ui-text": "#ece1d5", "ui-accent": "#d9b58a", "ui-border": "#514137", "canvas-background": "#1d1916"}},
]


def validate_style_values(value: object) -> dict:
    if not isinstance(value, dict) or any(key not in CONTROLS for key in value):
        raise ValueError("樣式設定含不支援的項目")
    result = dict(DEFAULTS)
    for key, entry in value.items():
        control = CONTROLS[key]
        kind = control["type"]
        if kind == "color":
            if not isinstance(entry, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", entry) is None:
                raise ValueError(f"{control['title']} 需為 #RRGGBB 色碼")
            entry = entry.lower()
        elif kind == "slider":
            if type(entry) not in (int, float) or not control["min"] <= entry <= control["max"] or not math.isfinite(entry):
                raise ValueError(f"{control['title']} 超出可調整範圍")
            ticks = (entry - control["min"]) / control["step"]
            if not math.isclose(ticks, round(ticks), abs_tol=1e-6):
                raise ValueError(f"{control['title']} 不符合滑桿刻度")
        elif kind == "toggle":
            if type(entry) is not bool:
                raise ValueError(f"{control['title']} 需為開關")
        elif kind == "select":
            if entry not in [option["value"] for option in control["options"]]:
                raise ValueError(f"{control['title']} 的選項無效")
        result[key] = entry
    return result


def style_settings(store) -> dict:
    with store._connect() as connection:
        row = connection.execute("SELECT value_json FROM app_settings WHERE key='style_settings'").fetchone()
    values = validate_style_values(json.loads(row["value_json"])) if row else dict(DEFAULTS)
    return {"version": 1, "groups": GROUPS, "presets": PRESETS, "values": values}


def save_style_settings(store, value: object) -> dict:
    values = validate_style_values(value)
    with store._connect() as connection:
        connection.execute("""INSERT INTO app_settings(key,value_json) VALUES('style_settings',?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""", (json.dumps(values, allow_nan=False),))
    return style_settings(store)
