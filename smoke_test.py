import json
import shutil
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

import app


def http_json(path: str, payload: dict | None = None) -> dict:
    url = f"http://127.0.0.1:8765{path}"
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def test_pipeline_isolated():
    tmp_dir = Path(tempfile.mkdtemp(prefix="wm_test_"))
    orig_kb_path = app.kb_path
    app.kb_path = lambda: tmp_dir
    try:
        app.ensure_kb()
        # 1. First cold-start commit
        raw_draft_1 = {
            "title": "结算单导出初次对齐",
            "summary": "对齐OMS订单系统与结算单导出规则",
            "filtered_chitchat": ["09:30-09:31 咖啡快递闲聊"],
            "entity_candidates": [
                {
                    "name": "OMS订单系统",
                    "category": "SystemEntity",
                    "entity_type": "核心系统",
                    "aliases": ["欧姆S", "老订单"],
                    "description": "负责订单履约与结算推送",
                },
                {
                    "name": "结算单",
                    "category": "BusinessEntity",
                    "entity_type": "核心单据",
                    "aliases": [],
                    "description": "财务结算凭证单据",
                },
                {
                    "name": "接口",  # Should be filtered by blacklist!
                    "category": "SystemEntity",
                },
            ],
            "relation_candidates": [
                {"from": "结算单", "type": "IMPLEMENTED_BY", "to": "欧姆S", "reason": "结算单由OMS生成并导出"}
            ],
            "facts": [
                {
                    "date": "2026-11-07",
                    "subject": "结算单",
                    "predicate": "单次异步导出上限",
                    "object": "5万条",
                    "status": "confirmed",
                    "evidence": "说话人1：单次异步导出上限先定在5万条",
                }
            ],
            "wiki": [
                {
                    "entity": "结算单",
                    "knowledge_type": "business_rule",
                    "summary_delta": "用于财务结算的核心单据",
                    "confirmed_rules": ["生鲜商品保质期必填"],
                    "open_questions": ["历史老商品无批次号如何兼容"],
                    "changelog_entry": "首次定义结算单导出上限5万条",
                }
            ],
        }
        grounded_1 = app.enrich_and_ground_draft(raw_draft_1, tmp_dir)
        ent_names_1 = [e["name"] for e in grounded_1["entity_candidates"]]
        assert "接口" not in ent_names_1, "Blacklist word '接口' should be filtered out"
        assert grounded_1["relation_candidates"][0]["to"] == "OMS订单系统", "Alias '欧姆S' should resolve to 'OMS订单系统'"

        res_1 = app.commit_draft(grounded_1, "说话人 1: 结算单导出上限5万条", {"说话人 1": "我"})
        assert res_1["entities_added"] == 2
        assert len(res_1["wiki_files"]) >= 1

        # 2. Second transcript: alias matching + rule change conflict detection + Living Wiki incremental update
        raw_draft_2 = {
            "title": "结算单规则变更",
            "summary": "导出上限从5万条调整为10万条",
            "entity_candidates": [
                {
                    "name": "欧姆S",  # Should auto-match existing OMS订单系统!
                    "category": "SystemEntity",
                    "aliases": ["订单中心后台"],
                },
                {
                    "name": "结算单",
                    "category": "BusinessEntity",
                },
            ],
            "relation_candidates": [],
            "facts": [
                {
                    "date": "2026-11-07",
                    "subject": "结算单",
                    "predicate": "单次异步导出上限",
                    "object": "10万条",
                    "status": "confirmed",
                    "evidence": "说话人1：导出上限从5万条正式上调到10万条",
                }
            ],
            "wiki": [
                {
                    "entity": "结算单",
                    "knowledge_type": "decision",
                    "summary_delta": "",
                    "confirmed_rules": ["单次异步导出上限上调至10万条"],
                    "open_questions": [],
                    "changelog_entry": "因大促账单量翻倍，导出上限由5万条上调为10万条",
                }
            ],
        }
        grounded_2 = app.enrich_and_ground_draft(raw_draft_2, tmp_dir)
        oms_cand = next(e for e in grounded_2["entity_candidates"] if e["name"] == "OMS订单系统")
        assert oms_cand["action"] == "match", "Should match existing entity via alias '欧姆S'"
        assert "订单中心后台" in oms_cand["new_aliases"]

        fact_2 = grounded_2["facts"][0]
        assert fact_2["conflict"] is not None and fact_2["conflict"]["old_object"] == "5万条", "Should detect rule conflict"

        wiki_2 = next(w for w in grounded_2["wiki"] if w["entity"] == "结算单")
        assert wiki_2["is_update"] is True, "Should incrementally update existing Living Wiki"
        assert "首次定义结算单导出上限5万条" in wiki_2["content"] and "10万条" in wiki_2["content"]

        res_2 = app.commit_draft(grounded_2, "说话人 1: 导出上限正式上调到10万条", {"说话人 1": "我"})
        assert res_2["entities_updated"] == 2 and res_2["entities_added"] == 0

        all_facts = app.load_all_facts(tmp_dir)
        statuses = {f["object"]: f["status"] for f in all_facts if f["predicate"] == "单次异步导出上限"}
        assert statuses.get("5万条") == "historical", "Old conflicting fact should be archived as historical"
        assert statuses.get("10万条") == "confirmed", "New fact should be confirmed"
        print("PASS isolated pipeline test (Entity Resolution + Blacklist + Fact Superseding + Living Wiki Merge)")
    finally:
        app.kb_path = orig_kb_path
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_pipeline_isolated()
    try:
        health = http_json("/api/health")
        assert health["ok"] is True
        config = http_json("/api/config")
        assert "api_base_url" in config and "api_key" not in config
        kb = http_json("/api/kb")
        assert kb["ok"] is True and "entities" in kb and "relations" in kb
        print("PASS live HTTP server endpoints (/api/health, /api/config, /api/kb)")
        print("Knowledge base:", health["knowledge_dir"])
    except Exception as e:
        print("Note: HTTP server not running on 8765 during test, skipped live HTTP check:", e)
