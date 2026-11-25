from __future__ import annotations

import json
import math
import re
from calendar import monthrange
from collections import defaultdict
from datetime import date

from ..search.query_builder import PUBMED_LITERAL_FIELDS, QueryBlock, format_pubmed_term


DATE_FIELDS = {"dp", "edat", "crdt", "mhda", "epdat", "ppdat"}
NODE_KINDS = {"concept", "and", "or", "module", "output", "screen", "category"}
SINGLE_INPUT = {"module", "output", "screen", "category"}


def _text_field(value, maximum):
    return isinstance(value, str) and len(value) <= maximum


def _validate_node_options(data):
    if "query_text" in data and (data["kind"] != "output" or not _text_field(data["query_text"], 100_000)):
        raise ValueError("OUTPUT 的搜尋式內容無效")
    if "input" in data:
        spec = data["input"]
        if data["kind"] != "concept" or not isinstance(spec, dict) or not isinstance(spec.get("field"), str):
            raise ValueError("無效的 INPUT 欄位設定")
        if spec.get("kind") == "field" and spec["field"] in PUBMED_LITERAL_FIELDS and _text_field(spec.get("value"), 500):
            return
        if (spec.get("kind") == "date" and spec["field"] in DATE_FIELDS
                and all(_text_field(spec.get(key), 10) for key in ("start", "end"))):
            return
        raise ValueError("無效的 INPUT 詞彙欄位或日期設定")
    if data["kind"] == "module" and any(not _text_field(data.get(key, ""), 1000) for key in ("note", "highlights")):
        raise ValueError("MODULE 提示或標色詞過長")
    if data["kind"] == "screen" and (data.get("screen_field") not in ("title", "abstract", "both") or not _text_field(data.get("screen_term"), 200)):
        raise ValueError("YES／NO 分組節點設定無效")


def _date_part(value, *, upper=False):
    match = re.fullmatch(r"([0-9]{4})(?:[-/]([0-9]{2})(?:[-/]([0-9]{2}))?)?", value.strip())
    if not match:
        raise ValueError("日期格式請用 YYYY、YYYY/MM 或 YYYY/MM/DD")
    year, month, day = match.groups()
    month_number = int(month) if month else (12 if upper else 1)
    try:
        day_number = int(day) if day else (monthrange(int(year), month_number)[1] if upper else 1)
        bound = date(int(year), month_number, day_number)
    except ValueError as exc:
        raise ValueError("日期不是有效的日曆日期") from exc
    return "/".join(part for part in (year, month, day) if part), bound


def _render_input(spec):
    if spec["kind"] == "field":
        if not spec["value"].strip():
            raise ValueError("欄位 INPUT 請填入搜尋詞")
        return format_pubmed_term(spec["value"], spec["field"])
    start, lower = _date_part(spec["start"])
    if not spec["end"].strip():
        return f'{start}[{spec["field"]}]'
    end, upper = _date_part(spec["end"], upper=True)
    if lower > upper:
        raise ValueError("日期 INPUT 的結束日期早於起始日期")
    return f'{start}:{end}[{spec["field"]}]'


def validate_graph(value: object) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("不支援的畫布格式，需為 version 1")
    nodes, edges = value.get("nodes"), value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > 200 or len(edges) > 500:
        raise ValueError("畫布最多 200 個節點、500 條連線")
    ids: set[str] = set()
    node_numbers: set[int] = set()
    clean_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("無效節點")
        ident, kind, data = node.get("id"), node.get("type"), node.get("data")
        if not isinstance(ident, str) or not ident or len(ident) > 100 or ident in ids:
            raise ValueError("節點 ID 重複或無效")
        if kind != "queryNode" or not isinstance(data, dict) or not isinstance(data.get("kind"), str) or data["kind"] not in NODE_KINDS:
            raise ValueError("不支援的節點種類")
        _validate_node_options(data)
        if "node_number" in data:
            number = data["node_number"]
            if type(number) is not int or not 1 <= number <= 1_000_000_000 or number in node_numbers:
                raise ValueError("節點編號需為不重複的正整數（上限 1000000000）")
            node_numbers.add(number)
        pos = node.get("position", {})
        if not isinstance(pos, dict) or any(type(pos.get(axis)) not in (float, int) or not math.isfinite(pos[axis]) or abs(pos[axis]) > 100000 for axis in ("x", "y")):
            raise ValueError("節點座標無效")
        if not isinstance(data.get("label", ""), str) or len(data.get("label", "")) > 200:
            raise ValueError("節點名稱過長")
        for key in ("term", "mesh_term"):
            if not isinstance(data.get(key, ""), str) or len(data.get(key, "")) > 500:
                raise ValueError("詞彙內容無效")
        aliases = data.get("aliases", [])
        if not isinstance(aliases, list) or len(aliases) > 300 or any(not isinstance(a, dict) or not isinstance(a.get("value"), str) or len(a["value"]) > 500 or type(a.get("selected", False)) is not bool for a in aliases):
            raise ValueError("別名資料無效")
        for alias in aliases:
            _validate_alias_metadata(alias)
        if "ambiguous" in data and type(data["ambiguous"]) is not bool:
            raise ValueError("歧義狀態無效")
        if data.get("entity_type", "compound") not in {"compound", "unknown"}:
            raise ValueError("詞彙類型無效")
        if "resolution" in data:
            _validate_resolution(data["resolution"])
        ids.add(ident)
        clean_nodes.append({"id": ident, "type": kind, "position": {k: pos[k] for k in ("x", "y")}, "data": {key: entry for key, entry in data.items() if key != "_runtime"}})
    node_map = {n["id"]: n for n in clean_nodes}
    edge_ids, pairs, clean_edges = set(), set(), []
    input_counts = defaultdict(int)
    output_ports = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("連線資料無效")
        ident, source, target = edge.get("id"), edge.get("source"), edge.get("target")
        if (not isinstance(ident, str) or not ident or ident in edge_ids or not isinstance(source, str)
                or not isinstance(target, str) or source not in ids or target not in ids or source == target or (source, target) in pairs):
            raise ValueError("連線 ID、端點或重複連線無效")
        source_kind = node_map[source]["data"]["kind"]
        target_kind = node_map[target]["data"]["kind"]
        port = edge.get("sourceHandle")
        if target_kind == "concept" or source_kind == "category":
            raise ValueError("INPUT 僅能送出條件，分類僅能接收分組結果")
        if (source_kind == "output") != (target_kind == "screen") or (source_kind == "screen") != (target_kind == "category"):
            raise ValueError("分組路徑需由 OUTPUT 接到 YES／NO，再接到分類")
        if target_kind in SINGLE_INPUT and input_counts[target]:
            raise ValueError("MODULE、OUTPUT、YES／NO 與分類都只能接一個輸入")
        if source_kind == "screen" and port not in ("yes", "no"):
            raise ValueError("分組連線需從 YES 或 NO 接點送出")
        if source_kind != "screen" and port is not None:
            raise ValueError("只有 YES／NO 節點有分支接點")
        if source_kind in {"output", "screen"} and (source, port) in output_ports:
            raise ValueError("OUTPUT 只能接一個分組節點，YES／NO 各只能接一個分類")
        input_counts[target] += 1
        output_ports.add((source, port))
        edge_ids.add(ident)
        pairs.add((source, target))
        clean = {"id": ident, "source": source, "target": target}
        if source_kind == "screen":
            clean["sourceHandle"] = port
        clean_edges.append(clean)
    incoming = defaultdict(list)
    for edge in clean_edges:
        incoming[edge["target"]].append(edge["source"])
    visited, active = set(), set()

    def visit(ident):
        if ident in active:
            raise ValueError("搜尋畫布不可形成循環")
        if ident in visited:
            return
        active.add(ident)
        for parent in incoming[ident]:
            visit(parent)
        active.remove(ident)
        visited.add(ident)

    for ident in ids:
        visit(ident)
    result = {"version": 1, "nodes": clean_nodes, "edges": clean_edges}
    if "active_output_id" in value:
        active_output = value["active_output_id"]
        if not isinstance(active_output, str) or active_output not in node_map or node_map[active_output]["data"]["kind"] != "output":
            raise ValueError("目前選取的 CHAIN 不是有效的 OUTPUT")
        result["active_output_id"] = active_output
    # Reject non-finite / non-JSON data even when nested in provider metadata.
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > 2_000_000:
        raise ValueError("畫布資料過大")
    return result


def _validate_alias_metadata(alias):
    sources = alias.get("sources", [])
    if not isinstance(sources, list) or any(not isinstance(s, str) or len(s) > 100 for s in sources):
        raise ValueError("別名來源無效")
    if not isinstance(alias.get("classification", "other"), str):
        raise ValueError("別名分類無效")


def _validate_resolution(result):
    # Imported drafts must satisfy the same shape rendered by the inspector.
    if not isinstance(result, dict):
        raise ValueError("解析來源資料無效")
    for key in ("canonical_name", "mesh_ui", "ncbi_mesh_entrez_id", "pubchem_cid"):
        if result.get(key) is not None and not isinstance(result[key], str):
            raise ValueError("解析識別碼無效")
    if type(result.get("ambiguous")) is not bool or not isinstance(result.get("retrieved_at"), str):
        raise ValueError("解析狀態或時間無效")
    errors = result.get("provider_errors")
    if not isinstance(errors, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in errors.items()):
        raise ValueError("解析錯誤資料無效")
    for key in ("raw_aliases", "search_aliases"):
        aliases = result.get(key)
        if not isinstance(aliases, list) or len(aliases) > 10000:
            raise ValueError("解析別名清單無效")
        for alias in aliases:
            if not isinstance(alias, dict) or not isinstance(alias.get("value"), str):
                raise ValueError("解析別名無效")
            _validate_alias_metadata(alias)
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or any(not isinstance(c, dict) or (c.get("canonical_name") is not None and not isinstance(c["canonical_name"], str)) or not isinstance(c.get("source"), str) for c in candidates):
        raise ValueError("解析候選資料無效")


def compile_graph(value: object, *, _node_states: dict | None = None) -> dict:
    graph = validate_graph(value)
    nodes = {n["id"]: n for n in graph["nodes"]}
    outputs = [n for n in graph["nodes"] if n["data"]["kind"] == "output"]
    if not outputs:
        raise ValueError("請新增一個 OUTPUT 定義搜尋 CHAIN")
    output_id = graph.get("active_output_id", outputs[0]["id"])
    incoming = defaultdict(list)
    order = {n["id"]: i for i, n in enumerate(graph["nodes"])}
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])
    used = set()
    rendered = {}

    def render(ident):
        used.add(ident)
        if ident in rendered:
            return rendered[ident]
        if _node_states is not None:
            _node_states[ident] = {"state": "running", "detail": "正在驗證搜尋條件"}
        try:
            result = render_node(ident)
            if len(result) > 100_000:
                raise ValueError("搜尋式超過 100000 字，請縮小 CHAIN 的條件")
        except ValueError as exc:
            if _node_states is not None:
                _node_states[ident] = {"state": "error", "detail": str(exc)}
            raise
        if _node_states is not None:
            kind = nodes[ident]["data"]["kind"]
            detail = {"concept": "已驗證輸入條件", "and": "已合併 AND 條件", "or": "已合併 OR 條件", "module": "已傳遞上游條件", "output": "搜尋式已產生"}[kind]
            _node_states[ident] = {"state": "success", "detail": detail}
        rendered[ident] = result
        return result

    def render_node(ident):
        data = nodes[ident]["data"]
        kind = data["kind"]
        if kind == "concept":
            if "input" in data:
                return _render_input(data["input"])
            if data.get("ambiguous"):
                raise ValueError(f"{data.get('label', ident)} 尚有歧義，請確認詞彙後解除")
            return QueryBlock(data.get("label", ident), data.get("mesh_term") or None,
                              tuple(a["value"] for a in data.get("aliases", []) if a.get("selected") is True)).render()
        parents = sorted(incoming[ident], key=order.get)
        if kind in {"output", "module"}:
            if len(parents) != 1:
                raise ValueError(f"{kind.upper()} 需連接一個上游條件，先用 AND／OR 合併多個輸入")
            return render(parents[0])
        if len(parents) < 2:
            raise ValueError("AND／OR 群組至少需兩個輸入")
        parts = [render(parent) for parent in parents]
        separator = f"\n{kind.upper()}\n"
        if sum(map(len, parts)) + len(separator) * (len(parts) - 1) + 4 > 100_000:
            raise ValueError("搜尋式超過 100000 字，請縮小 CHAIN 的條件")
        return "(\n" + separator.join(parts) + "\n)"

    query = render(output_id)
    connected = set(used)
    pending = [node["id"] for node in outputs if node["id"] != output_id]
    while pending:
        ident = pending.pop()
        if ident not in connected:
            connected.add(ident)
            pending.extend(incoming[ident])
    unused = [n["data"].get("label", n["id"]) for n in graph["nodes"]
              if n["id"] not in connected and n["data"]["kind"] not in {"screen", "category"}]
    warnings = ["未連到輸出的節點未納入搜尋式：" + "、".join(unused)] if unused else []
    screen = next((nodes[edge["target"]] for edge in graph["edges"] if edge["source"] == output_id), None)
    if screen:
        branches = {edge["sourceHandle"] for edge in graph["edges"] if edge["source"] == screen["id"]}
        if branches != {"yes", "no"} or not screen["data"]["screen_term"].strip():
            warnings.append("搜尋式已產生。請填寫分組條件並接好 YES、NO 分類，閱讀頁才會顯示分組。")
    return {"query": query, "warnings": warnings}


def compile_graph_with_status(value: object) -> dict:
    nodes = value.get("nodes", []) if isinstance(value, dict) else []
    states = {n["id"]: {"state": "idle", "detail": "未納入此次產生"}
              for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)} if isinstance(nodes, list) else {}
    try:
        result = compile_graph(value, _node_states=states)
        return {"ok": True, **result, "node_states": states}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "node_states": states}
