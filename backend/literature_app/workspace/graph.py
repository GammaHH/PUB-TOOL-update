from __future__ import annotations

import json
import math
from collections import defaultdict

from ..search.query_builder import QueryBlock


def validate_graph(value: object) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("不支援的畫布格式，需為 version 1")
    nodes, edges = value.get("nodes"), value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > 200 or len(edges) > 500:
        raise ValueError("畫布最多 200 個節點、500 條連線")
    ids: set[str] = set()
    clean_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("無效節點")
        ident, kind, data = node.get("id"), node.get("type"), node.get("data")
        if not isinstance(ident, str) or not ident or len(ident) > 100 or ident in ids:
            raise ValueError("節點 ID 重複或無效")
        if kind != "queryNode" or not isinstance(data, dict) or data.get("kind") not in {"concept", "and", "or", "output"}:
            raise ValueError("不支援的節點種類")
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
        clean_nodes.append({"id": ident, "type": kind, "position": {k: pos[k] for k in ("x", "y")}, "data": data})
    node_map = {n["id"]: n for n in clean_nodes}
    edge_ids, pairs, clean_edges = set(), set(), []
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("連線資料無效")
        ident, source, target = edge.get("id"), edge.get("source"), edge.get("target")
        if not isinstance(ident, str) or not ident or ident in edge_ids or source not in ids or target not in ids or source == target or (source, target) in pairs:
            raise ValueError("連線 ID、端點或重複連線無效")
        if node_map[source]["data"]["kind"] == "output" or node_map[target]["data"]["kind"] == "concept":
            raise ValueError("概念節點僅輸出，搜尋式節點僅輸入")
        edge_ids.add(ident)
        pairs.add((source, target))
        clean_edges.append({"id": ident, "source": source, "target": target})
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


def compile_graph(value: object) -> dict:
    graph = validate_graph(value)
    nodes = {n["id"]: n for n in graph["nodes"]}
    outputs = [n for n in graph["nodes"] if n["data"]["kind"] == "output"]
    if len(outputs) != 1:
        raise ValueError("產生搜尋式需恰好一個「搜尋式」節點")
    incoming = defaultdict(list)
    order = {n["id"]: i for i, n in enumerate(graph["nodes"])}
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])
    used = set()

    def render(ident):
        used.add(ident)
        data = nodes[ident]["data"]
        kind = data["kind"]
        if kind == "concept":
            if data.get("ambiguous"):
                raise ValueError(f"{data.get('label', ident)} 尚有歧義，請確認詞彙後解除")
            return QueryBlock(data.get("label", ident), data.get("mesh_term") or None,
                              tuple(a["value"] for a in data.get("aliases", []) if a.get("selected") is True)).render()
        parents = sorted(incoming[ident], key=order.get)
        if kind == "output":
            if len(parents) != 1:
                raise ValueError("搜尋式節點需連接一個概念或 AND／OR 群組")
            return render(parents[0])
        if len(parents) < 2:
            raise ValueError("AND／OR 群組至少需兩個輸入")
        return "(\n" + (f"\n{kind.upper()}\n").join(render(p) for p in parents) + "\n)"

    query = render(outputs[0]["id"])
    unused = [n["data"].get("label", n["id"]) for n in graph["nodes"] if n["id"] not in used]
    return {"query": query, "warnings": ["未連到輸出的節點未納入搜尋式：" + "、".join(unused)] if unused else []}
