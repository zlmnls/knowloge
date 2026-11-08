from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DEFAULT_CONFIG = {
    "api_base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key": "",
    "knowledge_dir": "knowledge-base",
}

DEFAULT_BLACKLIST = [
    "前端", "后端", "数据库", "接口", "字段", "列表页", "详情页", "页面", "按钮", "弹窗",
    "方案", "方案A", "方案B", "需求", "问题", "Bug", "测试环境", "线上环境", "生产环境",
    "代码", "表格", "文档", "用户", "开发", "研发", "产品", "测试", "会议", "录音",
    "说话人", "说话人1", "说话人2", "说话人3", "说话人 1", "说话人 2", "说话人 3",
]

DEFAULT_PROFILE = {
    "my_name": "我",
    "my_role": "",
    "domain_context": "",
    "speaker_defaults": {
        "说话人 1": "我",
        "说话人1": "我",
    },
}

VALID_CATEGORIES = ("BusinessEntity", "SystemEntity", "Project", "Person")
VALID_RELATIONS = ("PART_OF", "IMPLEMENTED_BY", "DEPENDS_ON", "OWNED_BY", "RELATES_TO", "IS_A")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def kb_path() -> Path:
    p = (ROOT / load_config().get("knowledge_dir", "knowledge-base")).resolve()
    if ROOT not in p.parents and p != ROOT:
        raise ValueError("知识库目录必须位于工具目录内")
    return p


def read_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_kb() -> Path:
    base = kb_path()
    for folder in ("00_sources", "10_wiki", "20_facts", "30_ontology"):
        (base / folder).mkdir(parents=True, exist_ok=True)

    ontology_dir = base / "30_ontology"
    schema_file = ontology_dir / "schema.yaml"
    if not schema_file.exists():
        schema_file.write_text(
            "# Work Memory Ontology Schema v2.0\n"
            "entity_categories:\n  - BusinessEntity\n  - SystemEntity\n  - Project\n  - Person\n"
            "relation_types:\n  - PART_OF\n  - IMPLEMENTED_BY\n  - DEPENDS_ON\n  - OWNED_BY\n  - RELATES_TO\n  - IS_A\n",
            encoding="utf-8",
        )
    if not (ontology_dir / "profile.json").exists():
        write_json(ontology_dir / "profile.json", DEFAULT_PROFILE)
    if not (ontology_dir / "entities.json").exists():
        write_json(ontology_dir / "entities.json", [])
    if not (ontology_dir / "relations.json").exists():
        write_json(ontology_dir / "relations.json", [])
    if not (ontology_dir / "blacklist.json").exists():
        write_json(ontology_dir / "blacklist.json", DEFAULT_BLACKLIST)
    return base


def norm_key(text: str) -> str:
    return re.sub(r"[\s\-_—·.,，。:：;；/\\()（）\[\]【】\"'`]+", "", str(text or "")).lower()


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", str(name or "").strip())
    return cleaned[:80] or "未命名实体"


def wiki_rel_path(category: str, entity_name: str) -> str:
    cat = category if category in VALID_CATEGORIES else "BusinessEntity"
    return f"10_wiki/{cat}__{safe_filename(entity_name)}.md"


def load_entities(base: Path | None = None) -> list[dict]:
    base = base or ensure_kb()
    items = read_json(base / "30_ontology" / "entities.json", [])
    return items if isinstance(items, list) else []


def save_entities(entities: list[dict], base: Path | None = None) -> None:
    base = base or ensure_kb()
    write_json(base / "30_ontology" / "entities.json", entities)


def load_relations(base: Path | None = None) -> list[dict]:
    base = base or ensure_kb()
    items = read_json(base / "30_ontology" / "relations.json", [])
    return items if isinstance(items, list) else []


def save_relations(relations: list[dict], base: Path | None = None) -> None:
    base = base or ensure_kb()
    write_json(base / "30_ontology" / "relations.json", relations)


def load_blacklist(base: Path | None = None) -> list[str]:
    base = base or ensure_kb()
    items = read_json(base / "30_ontology" / "blacklist.json", DEFAULT_BLACKLIST)
    return list(dict.fromkeys(str(x).strip() for x in items if str(x).strip()))


def save_blacklist(words: list[str], base: Path | None = None) -> None:
    base = base or ensure_kb()
    cleaned = list(dict.fromkeys(str(x).strip() for x in words if str(x).strip()))
    write_json(base / "30_ontology" / "blacklist.json", cleaned)


def load_profile(base: Path | None = None) -> dict:
    base = base or ensure_kb()
    prof = read_json(base / "30_ontology" / "profile.json", DEFAULT_PROFILE)
    return {**DEFAULT_PROFILE, **(prof if isinstance(prof, dict) else {})}


def save_profile(profile: dict, base: Path | None = None) -> dict:
    base = base or ensure_kb()
    cur = load_profile(base)
    merged = {
        "my_name": str(profile.get("my_name", cur.get("my_name", "我"))).strip() or "我",
        "my_role": str(profile.get("my_role", cur.get("my_role", ""))).strip(),
        "domain_context": str(profile.get("domain_context", cur.get("domain_context", ""))).strip(),
        "speaker_defaults": profile.get("speaker_defaults", cur.get("speaker_defaults", {})),
    }
    write_json(base / "30_ontology" / "profile.json", merged)
    return merged


def load_all_facts(base: Path | None = None) -> list[dict]:
    base = base or ensure_kb()
    facts_dir = base / "20_facts"
    results = []
    for fp in sorted(facts_dir.glob("*.jsonl"), reverse=True):
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj["_file"] = fp.name
                obj["_line"] = idx
                results.append(obj)
            except Exception:
                continue
    return results


def find_matching_entity(name: str, entities: list[dict], extra_hint: str = "") -> dict | None:
    targets = [norm_key(name)]
    if extra_hint:
        targets.append(norm_key(extra_hint))
    targets = [t for t in targets if t]
    if not targets:
        return None
    for ent in entities:
        keys = [norm_key(ent.get("name", ""))] + [norm_key(a) for a in ent.get("aliases", [])]
        keys = [k for k in keys if k]
        if any(t in keys for t in targets):
            return ent
    return None


def find_existing_wiki_path(base: Path, category: str, entity_name: str, entities: list[dict] | None = None) -> Path | None:
    if entities:
        matched = find_matching_entity(entity_name, entities)
        if matched and matched.get("wiki_file"):
            p = base / matched["wiki_file"]
            if p.exists():
                return p
    primary = base / wiki_rel_path(category, entity_name)
    if primary.exists():
        return primary
    for cat in VALID_CATEGORIES:
        candidate = base / wiki_rel_path(cat, entity_name)
        if candidate.exists():
            return candidate
    legacy = base / "10_wiki" / f"{safe_filename(entity_name)}.md"
    if legacy.exists():
        return legacy
    return None


def strip_frontmatter(md_text: str) -> str:
    text = str(md_text or "").strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text


def build_living_wiki_markdown(
    entity_name: str,
    category: str,
    existing_md: str,
    summary_delta: str,
    confirmed_rules: list[str],
    open_questions: list[str],
    changelog_entry: str,
    relations: list[dict],
    properties: dict | None = None,
    aliases: list[str] | None = None,
    date_str: str = "",
) -> str:
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    rel_lines = []
    for r in relations:
        frm = r.get("from", "")
        to = r.get("to", "")
        rtype = r.get("type", "RELATES_TO")
        reason = r.get("reason", "")
        if norm_key(frm) == norm_key(entity_name):
            rel_lines.append(f"- **{rtype}** → `{to}`" + (f"（{reason}）" if reason else ""))
        elif norm_key(to) == norm_key(entity_name):
            rel_lines.append(f"- `{frm}` → **{rtype}** → 本实体" + (f"（{reason}）" if reason else ""))

    prop_lines = []
    if properties and isinstance(properties, dict):
        for k, v in properties.items():
            if v is not None and str(v).strip():
                prop_lines.append(f"- **{k}**：{v}")

    if not existing_md.strip():
        sec1 = []
        if summary_delta:
            sec1.append(summary_delta.strip())
        if aliases:
            sec1.append(f"- **常用简称/别名**：{'、'.join(aliases)}")
        if prop_lines:
            sec1.extend(prop_lines)
        if confirmed_rules:
            sec1.append("\n### 已确认业务/系统规则")
            for r in confirmed_rules:
                sec1.append(f"- {r}")
        if not sec1:
            sec1.append("- 暂无已确认规则，待后续讨论补充。")

        sec2 = rel_lines if rel_lines else ["- 暂无关联上下游节点"]
        sec3 = [f"- [待定] {q}" for q in open_questions] if open_questions else ["- 当前无待定争议项"]
        sec4 = [f"- **{today}**：{changelog_entry or summary_delta or '首次建立实体知识档案'}"]

        return (
            f"# {entity_name}\n\n"
            f"## 1. 核心定义与当前生效规则 (Confirmed)\n\n" + "\n".join(sec1) + "\n\n"
            f"## 2. 关联图谱与上下游边界 (Graph Context)\n\n" + "\n".join(sec2) + "\n\n"
            f"## 3. 待定争议与讨论中方案 (Discussing)\n\n" + "\n".join(sec3) + "\n\n"
            f"## 4. 演进历史与决策溯源 (Changelog)\n\n" + "\n".join(sec4) + "\n"
        )

    body = strip_frontmatter(existing_md)
    sections = {1: "", 2: "", 3: "", 4: ""}
    current_sec = 0
    buf: list[str] = []
    for line in body.splitlines():
        if re.match(r"^##\s*1[\.\s、]", line):
            if current_sec in sections:
                sections[current_sec] = "\n".join(buf).strip()
            current_sec = 1
            buf = []
        elif re.match(r"^##\s*2[\.\s、]", line):
            if current_sec in sections:
                sections[current_sec] = "\n".join(buf).strip()
            current_sec = 2
            buf = []
        elif re.match(r"^##\s*3[\.\s、]", line):
            if current_sec in sections:
                sections[current_sec] = "\n".join(buf).strip()
            current_sec = 3
            buf = []
        elif re.match(r"^##\s*4[\.\s、]", line):
            if current_sec in sections:
                sections[current_sec] = "\n".join(buf).strip()
            current_sec = 4
            buf = []
        elif line.startswith("# ") and current_sec == 0:
            continue
        else:
            buf.append(line)
    if current_sec in sections:
        sections[current_sec] = "\n".join(buf).strip()

    if not any(sections.values()):
        sections[1] = body

    # Incremental update Section 1
    s1_parts = [sections[1]] if sections[1] and "暂无已确认规则" not in sections[1] else []
    new_s1_items = []
    if summary_delta and summary_delta.strip() not in sections[1]:
        new_s1_items.append(f"- **补充说明（{today}）**：{summary_delta.strip()}")
    for p in prop_lines:
        if p not in sections[1]:
            new_s1_items.append(p)
    for r in confirmed_rules:
        if r.strip() and r.strip() not in sections[1]:
            new_s1_items.append(f"- {r.strip()}")
    if new_s1_items:
        s1_parts.append("\n".join(new_s1_items))
    sec1_final = "\n\n".join([p for p in s1_parts if p.strip()]) or "- 暂无已确认规则"

    # Incremental update Section 2
    s2_lines = [l for l in sections[2].splitlines() if l.strip() and "暂无关联" not in l]
    for rl in rel_lines:
        if rl not in s2_lines:
            s2_lines.append(rl)
    sec2_final = "\n".join(s2_lines) if s2_lines else "- 暂无关联上下游节点"

    # Incremental update Section 3
    s3_lines = [l for l in sections[3].splitlines() if l.strip() and "当前无待定" not in l]
    for q in open_questions:
        item = f"- [待定 {today}] {q.strip()}"
        if q.strip() and q.strip() not in sections[3]:
            s3_lines.append(item)
    sec3_final = "\n".join(s3_lines) if s3_lines else "- 当前无待定争议项"

    # Incremental update Section 4 (prepend newest changelog)
    s4_lines = [l for l in sections[4].splitlines() if l.strip()]
    log_text = changelog_entry or summary_delta or ("更新了规则：" + "；".join(confirmed_rules[:2]) if confirmed_rules else "补充讨论记录")
    new_log = f"- **{today}**：{log_text}"
    if new_log not in s4_lines:
        s4_lines.insert(0, new_log)
    sec4_final = "\n".join(s4_lines) if s4_lines else new_log

    return (
        f"# {entity_name}\n\n"
        f"## 1. 核心定义与当前生效规则 (Confirmed)\n\n{sec1_final}\n\n"
        f"## 2. 关联图谱与上下游边界 (Graph Context)\n\n{sec2_final}\n\n"
        f"## 3. 待定争议与讨论中方案 (Discussing)\n\n{sec3_final}\n\n"
        f"## 4. 演进历史与决策溯源 (Changelog)\n\n{sec4_final}\n"
    )


def apply_speaker_map(text: str, speaker_map: dict | None) -> str:
    if not speaker_map or not isinstance(speaker_map, dict):
        return text
    out = text
    # Sort by length descending so "说话人 10" is replaced before "说话人 1"
    for spk, target in sorted(speaker_map.items(), key=lambda x: len(x[0]), reverse=True):
        spk = str(spk).strip()
        target = str(target).strip()
        if spk and target and spk != target:
            out = re.sub(rf"\b{re.escape(spk)}\b|(?<!\w){re.escape(spk)}(?!\w)", f"{target}({spk})", out)
    return out


def call_llm_json(system_prompt: str, user_prompt: str, timeout: int = 140) -> dict:
    cfg = load_config()
    if not cfg.get("api_key"):
        raise ValueError("请先在「设置与冷启动锚点」中填写模型 API Key")
    base = cfg.get("api_base_url", "").rstrip("/")
    if not base:
        raise ValueError("API Base URL 不能为空")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload = {
        "model": cfg.get("model", "gpt-4o-mini"),
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["api_key"],
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        # Strip markdown fences if model ignored response_format
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        return json.loads(cleaned)
    except HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:1200]
        raise ValueError(f"模型接口返回 HTTP {e.code}: {detail}") from e
    except (URLError, TimeoutError) as e:
        raise ValueError(f"无法连接模型接口：{e}") from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise ValueError(f"模型响应格式不符合预期：{e}") from e


def enrich_and_ground_draft(raw_draft: dict, base: Path | None = None) -> dict:
    base = base or ensure_kb()
    existing_entities = load_entities(base)
    blacklist = load_blacklist(base)
    blacklist_norm = {norm_key(w) for w in blacklist if norm_key(w)}
    all_facts = load_all_facts(base)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. Ground and resolve entities
    raw_candidates = raw_draft.get("entity_candidates") or []
    resolved_entities = []
    alias_redirect: dict[str, tuple[str, str]] = {}  # norm_key -> (canonical_name, category)

    for e in existing_entities:
        cname = e.get("name", "")
        cat = e.get("category", "BusinessEntity")
        if cname:
            alias_redirect[norm_key(cname)] = (cname, cat)
            for al in e.get("aliases", []):
                if al:
                    alias_redirect[norm_key(al)] = (cname, cat)

    seen_canonical: dict[str, dict] = {}
    for cand in raw_candidates:
        raw_name = str(cand.get("name") or "").strip()
        if not raw_name or norm_key(raw_name) in blacklist_norm:
            continue
        matched_hint = str(cand.get("matched_existing_name") or "").strip()
        matched_ent = find_matching_entity(raw_name, existing_entities, matched_hint)
        cat = str(cand.get("category") or "BusinessEntity").strip()
        if cat not in VALID_CATEGORIES:
            cat = "BusinessEntity"

        cand_aliases = [str(a).strip() for a in (cand.get("aliases") or []) if str(a).strip()]
        if matched_ent:
            canonical_name = matched_ent["name"]
            cat = matched_ent.get("category", cat)
            existing_aliases = matched_ent.get("aliases") or []
            existing_norm = {norm_key(canonical_name)} | {norm_key(a) for a in existing_aliases}
            new_aliases = []
            if norm_key(raw_name) not in existing_norm:
                new_aliases.append(raw_name)
            for a in cand_aliases:
                if norm_key(a) not in existing_norm and a not in new_aliases:
                    new_aliases.append(a)
            item = {
                "name": canonical_name,
                "raw_mention": raw_name,
                "category": cat,
                "entity_type": cand.get("entity_type") or matched_ent.get("entity_type") or "核心对象",
                "action": "match",
                "existing_id": matched_ent.get("id", ""),
                "existing_aliases": existing_aliases,
                "aliases": list(dict.fromkeys(existing_aliases + new_aliases)),
                "new_aliases": new_aliases,
                "description": cand.get("description") or matched_ent.get("description") or "",
                "properties": {**(matched_ent.get("properties") or {}), **(cand.get("properties") or {})},
                "reason": cand.get("reason") or f"匹配已有实体「{canonical_name}」",
                "approved": True,
            }
        else:
            canonical_name = raw_name
            item = {
                "name": canonical_name,
                "raw_mention": raw_name,
                "category": cat,
                "entity_type": cand.get("entity_type") or "核心对象",
                "action": "create",
                "existing_id": "",
                "existing_aliases": [],
                "aliases": list(dict.fromkeys([a for a in cand_aliases if norm_key(a) != norm_key(canonical_name)])),
                "new_aliases": list(dict.fromkeys([a for a in cand_aliases if norm_key(a) != norm_key(canonical_name)])),
                "description": cand.get("description") or "",
                "properties": cand.get("properties") if isinstance(cand.get("properties"), dict) else {},
                "reason": cand.get("reason") or "从本次对话识别出的稳定对象",
                "approved": True,
            }

        ckey = norm_key(canonical_name)
        alias_redirect[ norm_key(raw_name) ] = (canonical_name, cat)
        for a in item["aliases"]:
            alias_redirect[norm_key(a)] = (canonical_name, cat)

        if ckey in seen_canonical:
            prev = seen_canonical[ckey]
            prev["aliases"] = list(dict.fromkeys(prev["aliases"] + item["aliases"]))
            prev["new_aliases"] = list(dict.fromkeys(prev["new_aliases"] + item["new_aliases"]))
            prev["properties"] = {**prev["properties"], **item["properties"]}
        else:
            seen_canonical[ckey] = item
            resolved_entities.append(item)

    def resolve_name(n: str) -> str:
        nk = norm_key(n)
        if nk in alias_redirect:
            return alias_redirect[nk][0]
        return str(n or "").strip()

    def resolve_cat(n: str) -> str:
        nk = norm_key(n)
        if nk in alias_redirect:
            return alias_redirect[nk][1]
        return "BusinessEntity"

    # 2. Ground relations
    raw_rels = raw_draft.get("relation_candidates") or []
    resolved_relations = []
    seen_rels = set()
    for rel in raw_rels:
        frm = resolve_name(rel.get("from", ""))
        to = resolve_name(rel.get("to", ""))
        rtype = str(rel.get("type") or "RELATES_TO").strip().upper()
        if rtype not in VALID_RELATIONS:
            rtype = "RELATES_TO"
        if not frm or not to or norm_key(frm) == norm_key(to):
            continue
        if norm_key(frm) in blacklist_norm or norm_key(to) in blacklist_norm:
            continue
        rkey = (norm_key(frm), rtype, norm_key(to))
        if rkey in seen_rels:
            continue
        seen_rels.add(rkey)
        resolved_relations.append({
            "from": frm,
            "type": rtype,
            "to": to,
            "reason": str(rel.get("reason") or "").strip(),
            "approved": True,
        })

    # 3. Ground facts & detect version conflicts against existing confirmed facts
    existing_confirmed_map: dict[tuple[str, str], dict] = {}
    for ef in all_facts:
        if ef.get("status") == "confirmed":
            k = (norm_key(ef.get("subject", "")), norm_key(ef.get("predicate", "")))
            if k[0] and k[1] and k not in existing_confirmed_map:
                existing_confirmed_map[k] = ef

    raw_facts = raw_draft.get("facts") or []
    resolved_facts = []
    for f in raw_facts:
        subj = resolve_name(f.get("subject", ""))
        pred = str(f.get("predicate") or "").strip()
        obj = str(f.get("object") or "").strip()
        if not subj or not pred or not obj:
            continue
        status = str(f.get("status") or "discussing").strip()
        if status not in ("confirmed", "discussing", "proposed", "historical", "unknown"):
            status = "discussing"
        f_date = str(f.get("date") or today_str).strip()
        if f_date.lower() in ("unknown", "未知", ""):
            f_date = today_str

        conflict = None
        ck = (norm_key(subj), norm_key(pred))
        if ck in existing_confirmed_map:
            old_f = existing_confirmed_map[ck]
            if norm_key(old_f.get("object", "")) != norm_key(obj):
                conflict = {
                    "old_object": old_f.get("object", ""),
                    "old_date": old_f.get("date", ""),
                    "old_source_id": old_f.get("source_id", ""),
                }

        resolved_facts.append({
            "date": f_date,
            "subject": subj,
            "predicate": pred,
            "object": obj,
            "status": status,
            "evidence": str(f.get("evidence") or "").strip(),
            "conflict": conflict,
            "supersede_old": bool(conflict and status == "confirmed"),
            "approved": True,
        })

    # 4. Build Living Wiki previews (merging with existing entity wikis if present!)
    wiki_by_entity: dict[str, dict] = {}
    for w in (raw_draft.get("wiki") or []):
        ent_name = resolve_name(w.get("entity") or w.get("title") or "")
        if not ent_name or norm_key(ent_name) in blacklist_norm:
            continue
        ekey = norm_key(ent_name)
        cat = resolve_cat(ent_name)
        confirmed_rules = w.get("confirmed_rules") if isinstance(w.get("confirmed_rules"), list) else []
        open_questions = w.get("open_questions") if isinstance(w.get("open_questions"), list) else []
        summary_delta = str(w.get("summary_delta") or w.get("content") or "").strip()
        changelog_entry = str(w.get("changelog_entry") or "").strip()

        wiki_by_entity[ekey] = {
            "entity": ent_name,
            "category": cat,
            "knowledge_type": w.get("knowledge_type") or "business_rule",
            "summary_delta": summary_delta,
            "confirmed_rules": confirmed_rules,
            "open_questions": open_questions,
            "changelog_entry": changelog_entry,
        }

    # Ensure every approved entity that has facts or relations gets a Living Wiki entry
    for ent in resolved_entities:
        ename = ent["name"]
        ekey = norm_key(ename)
        ent_facts = [f for f in resolved_facts if norm_key(f["subject"]) == ekey]
        ent_rels = [r for r in resolved_relations if norm_key(r["from"]) == ekey or norm_key(r["to"]) == ekey]
        if ekey not in wiki_by_entity and (ent_facts or ent_rels or ent.get("description")):
            wiki_by_entity[ekey] = {
                "entity": ename,
                "category": ent["category"],
                "knowledge_type": "business_rule" if ent["category"] == "BusinessEntity" else "solution",
                "summary_delta": ent.get("description", ""),
                "confirmed_rules": [],
                "open_questions": [],
                "changelog_entry": "",
            }

    final_wikis = []
    for ekey, winfo in wiki_by_entity.items():
        ename = winfo["entity"]
        cat = winfo["category"]
        ent_obj = seen_canonical.get(ekey) or {}
        ent_facts = [f for f in resolved_facts if norm_key(f["subject"]) == ekey]
        ent_rels = [r for r in resolved_relations if norm_key(r["from"]) == ekey or norm_key(r["to"]) == ekey]

        c_rules = list(winfo["confirmed_rules"])
        o_questions = list(winfo["open_questions"])
        for f in ent_facts:
            line = f"**{f['predicate']}**：{f['object']}" + (f"（证据：{f['evidence']}）" if f.get("evidence") else "")
            if f["status"] == "confirmed":
                if line not in c_rules:
                    c_rules.append(line)
            elif f["status"] in ("discussing", "proposed", "unknown"):
                if line not in o_questions:
                    o_questions.append(line)

        existing_path = find_existing_wiki_path(base, cat, ename, existing_entities)
        existing_md = ""
        if existing_path and existing_path.exists():
            try:
                existing_md = strip_frontmatter(existing_path.read_text(encoding="utf-8"))
            except Exception:
                existing_md = ""

        merged_md = build_living_wiki_markdown(
            entity_name=ename,
            category=cat,
            existing_md=existing_md,
            summary_delta=winfo["summary_delta"],
            confirmed_rules=c_rules,
            open_questions=o_questions,
            changelog_entry=winfo["changelog_entry"],
            relations=ent_rels,
            properties=ent_obj.get("properties"),
            aliases=ent_obj.get("aliases"),
            date_str=today_str,
        )
        final_wikis.append({
            "title": ename,
            "entity": ename,
            "category": cat,
            "knowledge_type": winfo["knowledge_type"],
            "is_update": bool(existing_md.strip()),
            "existing_content": existing_md,
            "content": strip_frontmatter(merged_md),
            "approved": True,
        })

    return {
        "title": raw_draft.get("title") or "工作录音知识整理",
        "summary": raw_draft.get("summary") or "",
        "filtered_chitchat": raw_draft.get("filtered_chitchat") or [],
        "speaker_inferences": raw_draft.get("speaker_inferences") or [],
        "topics": raw_draft.get("topics") or [],
        "entity_candidates": resolved_entities,
        "relation_candidates": resolved_relations,
        "facts": resolved_facts,
        "wiki": final_wikis,
        "uncertainties": raw_draft.get("uncertainties") or [],
    }


def extract_text(text: str, speaker_map: dict | None = None) -> dict:
    base = ensure_kb()
    profile = load_profile(base)
    existing_entities = load_entities(base)
    blacklist = load_blacklist(base)
    today_str = datetime.now().strftime("%Y-%m-%d")

    effective_speaker_map = {**(profile.get("speaker_defaults") or {}), **(speaker_map or {})}
    processed_text = apply_speaker_map(text, effective_speaker_map)

    # Format existing Ontology context for prompt grounding
    entity_catalog_lines = []
    for e in existing_entities[:120]:
        aliases_str = f" (别名/简称: {', '.join(e.get('aliases', []))})" if e.get("aliases") else ""
        desc_str = f" - {e.get('description')}" if e.get("description") else ""
        entity_catalog_lines.append(f"- [{e.get('category', 'BusinessEntity')}] {e.get('name')}{aliases_str}{desc_str}")
    entity_catalog_text = "\n".join(entity_catalog_lines) if entity_catalog_lines else "（当前知识库尚无已入库实体，本次为冷启动抽取）"

    profile_context = (
        f"当前日期：{today_str}\n"
        f"我的标识/姓名：{profile.get('my_name', '我')}\n"
        f"我的角色与职责：{profile.get('my_role') or '未指定'}\n"
        f"负责业务域与系统背景：{profile.get('domain_context') or '未指定'}\n"
    )

    system_prompt = f"""你是「个人工作记忆与Ontology知识图谱架构师」。你的任务是从飞书语音转写文本（可能含多人对话、口语简称、同音错别字、日常闲聊）中过滤噪音，精准抽取【Ontology实体节点】、【知识图谱关系边】、【时序事实Facts】和【实体Living Wiki增量要素】。

=== 一、个人锚点与当前已知上下文 ===
{profile_context}
=== 二、正式知识库中已有的实体清单（优先对齐！） ===
{entity_catalog_text}

=== 三、严格禁抽黑名单（以下泛化词严禁作为独立实体！） ===
{", ".join(blacklist[:60])}

=== 四、实体抽取与知识图谱铁律（必须严格遵守） ===
1. 【闲聊与噪音过滤】：
   - 飞书全天录音中包含午饭、快递、八卦、天气、无效寒暄或与工作无关的旁人闲聊。请将纯闲聊片段摘要放入 `filtered_chitchat` 数组，**绝不要从闲聊中抽实体或事实**。
2. 【实体边界金标准（4大类）】：
   - 一个词只有当它在未来多场不同讨论中会被反复提及、有独立生命周期或职责边界时，才配成为 Entity！
   - 只允许 4 个类别 (`category`)：
     * `BusinessEntity`：核心业务对象/单据/规则概念（如：结算单、采购单、优惠券、会员等级、商品SKU、退货单）。
     * `SystemEntity`：具体系统、微服务、功能模块、核心库表、三方平台（如：OMS订单中心、ERP库存服务、商详页PDP、企微通知接口）。
     * `Project`：专项、业务线、大版本项目（如：Q4履约改造专项、跨境合规一期）。
     * `Person`：具体干系人、同事、部门团队（如：王工、财务结算组；注：未指明身份的“说话人1/2”不要建为实体，但若能从对话推断出身份请在 `speaker_inferences` 说明）。
   - 【反例警告】：
     * 保质期、导出上限10万条、超时时间、必填/选填 等属于实体的【属性/Facts】，严禁建为实体！
     * 某次报错修复、单条小需求、讨论结论 属于【Facts/Wiki】，严禁建为实体！
3. 【同名归一与飞书同音字纠错（Entity Resolution）】：
   - 若对话中提到的词（如“订单后台”、“老订单”、“欧姆S”）对应已有实体清单中的某个实体（如 `OMS订单系统`），`name` 必须直接填写已有标准名 `OMS订单系统`，`matched_existing_name` 填 `OMS订单系统`，并把口语词/同音错字放入 `aliases` 数组！
4. 【图谱关系边（relation_candidates）】：
   - `from` 和 `to` 必须严格使用 `entity_candidates` 或已有实体清单中的标准实体名，消灭悬空边！
   - `type` 仅限：`PART_OF`（从属/子模块）、`IMPLEMENTED_BY`（业务由某系统承载）、`DEPENDS_ON`（依赖/上下游）、`OWNED_BY`（归属某人/某团队负责）、`RELATES_TO`（业务协同）、`IS_A`（分类继承）。
5. 【事实状态区分（facts）】：
   - `subject` 必须对齐到标准实体名！
   - 严格区分 `status`：已拍板定论/线上现状用 `confirmed`；讨论中未定论用 `discussing`；某人单方面提议/AI建议用 `proposed`；明确被推翻的旧规则用 `historical`。保留说话人和原话证据 `evidence`。

请输出严格 JSON 对象（不要使用 Markdown 代码围栏），结构如下：
{{
  "title": "本次工作对话核心标题",
  "summary": "100字以内的核心有效工作内容摘要",
  "filtered_chitchat": ["已过滤的闲聊或噪音片段简述，无则空数组"],
  "speaker_inferences": [{{"speaker": "说话人1", "inferred_name": "推断出的身份或姓名", "evidence": "推断依据"}}],
  "topics": [{{"name": "议题名称", "project": "所属项目或业务域", "content": "议题核心结论与背景"}}],
  "entity_candidates": [
    {{
      "name": "标准实体名（若匹配已有实体则填已有标准名）",
      "matched_existing_name": "匹配到的已有实体名，新实体则填空字符串",
      "category": "BusinessEntity|SystemEntity|Project|Person",
      "entity_type": "细分类型，如：核心单据/微服务/功能模块/干系人",
      "aliases": ["口语简称", "飞书同音错字"],
      "description": "一句话定义该实体的职责或业务含义",
      "properties": {{"关键固定属性名": "属性值"}},
      "reason": "为什么它是稳定实体"
    }}
  ],
  "relation_candidates": [
    {{
      "from": "起点标准实体名",
      "type": "PART_OF|IMPLEMENTED_BY|DEPENDS_ON|OWNED_BY|RELATES_TO|IS_A",
      "to": "终点标准实体名",
      "reason": "关系建立的业务或系统依据"
    }}
  ],
  "facts": [
    {{
      "date": "{today_str}",
      "subject": "归属的标准实体名",
      "predicate": "规则点/属性项/动作断言（如：导出上限、保质期校验规则、接口超时时间）",
      "object": "具体规则内容或取值",
      "status": "confirmed|discussing|proposed|historical|unknown",
      "evidence": "说话人及原话关键证据"
    }}
  ],
  "wiki": [
    {{
      "entity": "对应的标准实体名",
      "knowledge_type": "business_rule|decision|solution|background|experience",
      "summary_delta": "本次对话关于该实体的核心定位或机制补充",
      "confirmed_rules": ["本次已确认的完整规则条文（可直接写入Wiki正文）"],
      "open_questions": ["本次未定论、待跟进确认的争议问题"],
      "changelog_entry": "一句话总结本次对话对该实体的决策演进或背景原因（如：因老数据兼容问题，将保质期由必填改为选填）"
    }}
  ],
  "uncertainties": ["对话中指代不明、信息缺失或需人工确认的疑问点"]
}}"""

    raw_result = call_llm_json(system_prompt, processed_text)
    return enrich_and_ground_draft(raw_result, base)


def commit_draft(draft: dict, source: str, speaker_map: dict | None = None) -> dict:
    base = ensure_kb()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sid = "SRC-" + stamp + "-" + uuid.uuid4().hex[:6]
    now = now_iso()

    # 1. Save raw source
    spk_meta = json.dumps(speaker_map or {}, ensure_ascii=False)
    title = str(draft.get("title") or "工作录音整理").strip()
    (base / "00_sources" / f"{sid}.md").write_text(
        f"---\nid: {sid}\ntitle: {title}\ncreated_at: {now}\nspeaker_map: {spk_meta}\n---\n\n{source}\n",
        encoding="utf-8",
    )

    # 2. Update blacklist if user blacklisted any items in review
    new_blacklisted = draft.get("blacklisted_terms") or []
    if new_blacklisted:
        cur_bl = load_blacklist(base)
        save_blacklist(cur_bl + [str(x) for x in new_blacklisted], base)

    # 3. Update canonical entities (`30_ontology/entities.json`)
    entities = load_entities(base)
    ent_candidates = [e for e in (draft.get("entity_candidates") or []) if e.get("approved", True) is not False]
    entities_added = 0
    entities_updated = 0

    for cand in ent_candidates:
        cname = str(cand.get("name") or "").strip()
        if not cname:
            continue
        cat = str(cand.get("category") or "BusinessEntity").strip()
        if cat not in VALID_CATEGORIES:
            cat = "BusinessEntity"
        cand_aliases = [str(a).strip() for a in (cand.get("aliases") or []) if str(a).strip() and norm_key(a) != norm_key(cname)]
        raw_mention = str(cand.get("raw_mention") or "").strip()
        if raw_mention and norm_key(raw_mention) != norm_key(cname) and raw_mention not in cand_aliases:
            cand_aliases.append(raw_mention)

        matched = find_matching_entity(cname, entities, cand.get("existing_id", ""))
        if matched:
            merged_aliases = list(dict.fromkeys((matched.get("aliases") or []) + cand_aliases))
            matched["aliases"] = merged_aliases
            matched["category"] = cat
            if cand.get("entity_type"):
                matched["entity_type"] = cand["entity_type"]
            if cand.get("description") and (not matched.get("description") or len(str(cand["description"])) >= len(str(matched.get("description", "")))):
                matched["description"] = cand["description"]
            matched["properties"] = {**(matched.get("properties") or {}), **(cand.get("properties") or {})}
            sids = matched.get("source_ids") or []
            if sid not in sids:
                sids.append(sid)
            matched["source_ids"] = sids
            matched["updated_at"] = now
            entities_updated += 1
        else:
            eid = "ENT-" + uuid.uuid4().hex[:8].upper()
            entities.append({
                "id": eid,
                "name": cname,
                "category": cat,
                "entity_type": cand.get("entity_type") or "核心对象",
                "aliases": list(dict.fromkeys(cand_aliases)),
                "description": str(cand.get("description") or "").strip(),
                "properties": cand.get("properties") if isinstance(cand.get("properties"), dict) else {},
                "wiki_file": wiki_rel_path(cat, cname),
                "created_at": now,
                "updated_at": now,
                "source_ids": [sid],
            })
            entities_added += 1

    # 4. Update canonical relations (`30_ontology/relations.json`)
    relations = load_relations(base)
    rel_candidates = [r for r in (draft.get("relation_candidates") or []) if r.get("approved", True) is not False]
    relations_saved = 0

    for rc in rel_candidates:
        frm = str(rc.get("from") or "").strip()
        to = str(rc.get("to") or "").strip()
        rtype = str(rc.get("type") or "RELATES_TO").strip().upper()
        if not frm or not to or norm_key(frm) == norm_key(to):
            continue
        existing_rel = next(
            (r for r in relations if norm_key(r.get("from", "")) == norm_key(frm) and r.get("type") == rtype and norm_key(r.get("to", "")) == norm_key(to)),
            None,
        )
        if existing_rel:
            if rc.get("reason"):
                existing_rel["reason"] = rc["reason"]
            sids = existing_rel.get("source_ids") or []
            if sid not in sids:
                sids.append(sid)
            existing_rel["source_ids"] = sids
            existing_rel["updated_at"] = now
        else:
            relations.append({
                "id": "REL-" + uuid.uuid4().hex[:8].upper(),
                "from": frm,
                "type": rtype,
                "to": to,
                "reason": str(rc.get("reason") or "").strip(),
                "created_at": now,
                "updated_at": now,
                "source_ids": [sid],
            })
        relations_saved += 1

    # 5. Update Living Wikis (`10_wiki/`)
    saved_wikis = []
    wiki_items = [w for w in (draft.get("wiki") or []) if w.get("approved", True) is not False]
    for item in wiki_items:
        entity_name = str(item.get("entity") or item.get("title") or "未命名知识").strip()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        matched_ent = find_matching_entity(entity_name, entities)
        cat = item.get("category") or (matched_ent.get("category") if matched_ent else "BusinessEntity")
        if cat not in VALID_CATEGORIES:
            cat = "BusinessEntity"

        existing_wiki = find_existing_wiki_path(base, cat, entity_name, entities)
        target_path = existing_wiki if existing_wiki else (base / wiki_rel_path(cat, entity_name))
        rel_str = str(target_path.relative_to(base)).replace("\\", "/")

        body_md = strip_frontmatter(content)
        if not body_md.startswith("# "):
            body_md = f"# {entity_name}\n\n{body_md}"

        target_path.write_text(
            f"---\nsource_id: {sid}\nentity: {entity_name}\ncategory: {cat}\n"
            f"knowledge_type: {item.get('knowledge_type', 'business_rule')}\nupdated_at: {now}\n---\n\n{body_md}\n",
            encoding="utf-8",
        )
        saved_wikis.append(rel_str)
        if matched_ent:
            matched_ent["wiki_file"] = rel_str

    save_entities(entities, base)
    save_relations(relations, base)

    # 6. Save Facts (`20_facts/YYYY-MM.jsonl`) & supersede conflicting confirmed facts
    approved_facts = [f for f in (draft.get("facts") or []) if f.get("approved", True) is not False]
    supersede_keys = {
        (norm_key(f.get("subject", "")), norm_key(f.get("predicate", "")))
        for f in approved_facts
        if f.get("status") == "confirmed" and f.get("supersede_old", True)
    }
    if supersede_keys:
        for fp in (base / "20_facts").glob("*.jsonl"):
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
                changed = False
                new_lines = []
                for line in lines:
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    k = (norm_key(obj.get("subject", "")), norm_key(obj.get("predicate", "")))
                    if obj.get("status") == "confirmed" and k in supersede_keys:
                        obj["status"] = "historical"
                        obj["superseded_by"] = sid
                        obj["superseded_at"] = now
                        changed = True
                    new_lines.append(json.dumps(obj, ensure_ascii=False))
                if changed:
                    fp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            except Exception:
                continue

    if approved_facts:
        fp = base / "20_facts" / (datetime.now().strftime("%Y-%m") + ".jsonl")
        with fp.open("a", encoding="utf-8") as f:
            for fact in approved_facts:
                clean_fact = {
                    "id": "FACT-" + uuid.uuid4().hex[:8].upper(),
                    "date": fact.get("date") or datetime.now().strftime("%Y-%m-%d"),
                    "subject": fact.get("subject", ""),
                    "predicate": fact.get("predicate", ""),
                    "object": fact.get("object", ""),
                    "status": fact.get("status", "discussing"),
                    "evidence": fact.get("evidence", ""),
                    "source_id": sid,
                    "recorded_at": now,
                }
                f.write(json.dumps(clean_fact, ensure_ascii=False) + "\n")

    # 7. Append audit log to candidates.jsonl
    candidates_audit = {
        "source_id": sid,
        "created_at": now,
        "entities": ent_candidates,
        "relations": rel_candidates,
    }
    if ent_candidates or rel_candidates:
        cp = base / "30_ontology" / "candidates.jsonl"
        with cp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(candidates_audit, ensure_ascii=False) + "\n")

    return {
        "ok": True,
        "source_id": sid,
        "wiki_files": saved_wikis,
        "facts_count": len(approved_facts),
        "entity_candidates": len(ent_candidates),
        "entities_added": entities_added,
        "entities_updated": entities_updated,
        "relation_candidates": relations_saved,
    }


def merge_two_entities(source_id_or_name: str, target_id_or_name: str) -> dict:
    base = ensure_kb()
    entities = load_entities(base)
    src = next((e for e in entities if e.get("id") == source_id_or_name or norm_key(e.get("name", "")) == norm_key(source_id_or_name)), None)
    tgt = next((e for e in entities if e.get("id") == target_id_or_name or norm_key(e.get("name", "")) == norm_key(target_id_or_name)), None)
    if not src or not tgt:
        raise ValueError("未找到待合并的源实体或目标实体")
    if src["id"] == tgt["id"]:
        raise ValueError("不能将实体合并到自身")

    src_name = src["name"]
    tgt_name = tgt["name"]
    now = now_iso()

    # 1. Merge aliases, properties, source_ids
    aliases = list(dict.fromkeys((tgt.get("aliases") or []) + [src_name] + (src.get("aliases") or [])))
    aliases = [a for a in aliases if norm_key(a) != norm_key(tgt_name)]
    tgt["aliases"] = aliases
    tgt["properties"] = {**(src.get("properties") or {}), **(tgt.get("properties") or {})}
    tgt["source_ids"] = list(dict.fromkeys((tgt.get("source_ids") or []) + (src.get("source_ids") or [])))
    tgt["updated_at"] = now

    # 2. Rewrite relations
    relations = load_relations(base)
    dedup_rels = []
    seen = set()
    for r in relations:
        if norm_key(r.get("from", "")) == norm_key(src_name):
            r["from"] = tgt_name
        if norm_key(r.get("to", "")) == norm_key(src_name):
            r["to"] = tgt_name
        if norm_key(r.get("from", "")) == norm_key(r.get("to", "")):
            continue
        k = (norm_key(r.get("from", "")), r.get("type", ""), norm_key(r.get("to", "")))
        if k not in seen:
            seen.add(k)
            dedup_rels.append(r)

    # 3. Rewrite facts
    for fp in (base / "20_facts").glob("*.jsonl"):
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
            changed = False
            out_lines = []
            for line in lines:
                if not line.strip():
                    continue
                obj = json.loads(line)
                if norm_key(obj.get("subject", "")) == norm_key(src_name):
                    obj["subject"] = tgt_name
                    changed = True
                out_lines.append(json.dumps(obj, ensure_ascii=False))
            if changed:
                fp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        except Exception:
            continue

    # 4. Merge Wiki if source has one
    src_wiki = find_existing_wiki_path(base, src.get("category", "BusinessEntity"), src_name, [src])
    tgt_wiki = find_existing_wiki_path(base, tgt.get("category", "BusinessEntity"), tgt_name, [tgt])
    if src_wiki and src_wiki.exists():
        src_body = strip_frontmatter(src_wiki.read_text(encoding="utf-8"))
        if tgt_wiki and tgt_wiki.exists():
            tgt_body = strip_frontmatter(tgt_wiki.read_text(encoding="utf-8"))
            merged_body = f"{tgt_body}\n\n---\n### 合并自原实体「{src_name}」的知识记录\n\n{src_body}\n"
            tgt_wiki.write_text(
                f"---\nentity: {tgt_name}\ncategory: {tgt.get('category', 'BusinessEntity')}\nupdated_at: {now}\n---\n\n{merged_body}",
                encoding="utf-8",
            )
        else:
            new_path = base / wiki_rel_path(tgt.get("category", "BusinessEntity"), tgt_name)
            new_path.write_text(
                f"---\nentity: {tgt_name}\ncategory: {tgt.get('category', 'BusinessEntity')}\nupdated_at: {now}\n---\n\n# {tgt_name}\n\n{src_body}\n",
                encoding="utf-8",
            )
            tgt["wiki_file"] = str(new_path.relative_to(base)).replace("\\", "/")
        try:
            src_wiki.unlink()
        except Exception:
            pass

    entities = [e for e in entities if e["id"] != src["id"]]
    save_entities(entities, base)
    save_relations(dedup_rels, base)
    return {"ok": True, "target": tgt}


def get_kb_snapshot() -> dict:
    base = ensure_kb()
    entities = load_entities(base)
    relations = load_relations(base)
    facts = load_all_facts(base)
    profile = load_profile(base)
    blacklist = load_blacklist(base)

    wikis = []
    for wp in sorted((base / "10_wiki").glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            raw = wp.read_text(encoding="utf-8")
            body = strip_frontmatter(raw)
            stem = wp.stem
            if "__" in stem:
                cat, ename = stem.split("__", 1)
            else:
                cat, ename = "BusinessEntity", stem
            wikis.append({
                "file": str(wp.relative_to(base)).replace("\\", "/"),
                "entity": ename,
                "category": cat,
                "updated_at": datetime.fromtimestamp(wp.stat().st_mtime).isoformat(timespec="seconds"),
                "content": body,
            })
        except Exception:
            continue

    sources = []
    for sp in sorted((base / "00_sources").glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        try:
            raw = sp.read_text(encoding="utf-8")
            body = strip_frontmatter(raw)
            sources.append({
                "id": sp.stem,
                "created_at": datetime.fromtimestamp(sp.stat().st_mtime).isoformat(timespec="seconds"),
                "preview": body[:160],
            })
        except Exception:
            continue

    return {
        "ok": True,
        "profile": profile,
        "entities": entities,
        "relations": relations,
        "facts": facts[:300],
        "wikis": wikis,
        "sources": sources,
        "blacklist": blacklist,
        "stats": {
            "entities_count": len(entities),
            "relations_count": len(relations),
            "facts_count": len(facts),
            "wikis_count": len(wikis),
            "sources_count": len(list((base / "00_sources").glob("*.md"))),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(raw.__len__()))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 8_000_000:
            raise ValueError("单次文本不能超过8MB")
        if size == 0:
            return {}
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/config":
            c = load_config()
            self.send_json({
                "api_base_url": c["api_base_url"],
                "model": c["model"],
                "api_key_set": bool(c["api_key"]),
                "knowledge_dir": c["knowledge_dir"],
            })
            return
        if path == "/api/health":
            self.send_json({"ok": True, "knowledge_dir": str(ensure_kb())})
            return
        if path == "/api/kb":
            self.send_json(get_kb_snapshot())
            return
        if path == "/api/source":
            sid = (qs.get("id") or [""])[0].strip()
            base = ensure_kb()
            sp = (base / "00_sources" / f"{safe_filename(sid)}.md").resolve()
            if not sp.exists():
                self.send_json({"error": "未找到该原始记录"}, 404)
                return
            self.send_json({"ok": True, "id": sid, "content": sp.read_text(encoding="utf-8")})
            return

        raw = (ROOT / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        try:
            data = self.body()
            if self.path == "/api/config":
                old = load_config()
                new = {
                    **old,
                    "api_base_url": data.get("api_base_url", old["api_base_url"]).strip(),
                    "model": data.get("model", old["model"]).strip(),
                    "knowledge_dir": data.get("knowledge_dir", old["knowledge_dir"]).strip(),
                }
                if data.get("api_key"):
                    new["api_key"] = data["api_key"].strip()
                CONFIG_PATH.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
                self.send_json({"ok": True})
                return

            if self.path == "/api/test_llm":
                res = call_llm_json(
                    "你是一个JSON连通性测试助手。请返回严格JSON：{\"status\":\"ok\",\"reply\":\"连接成功\"}",
                    "请回复连接测试结果",
                    timeout=25,
                )
                self.send_json({"ok": True, "result": res})
                return

            if self.path == "/api/profile":
                saved = save_profile(data)
                self.send_json({"ok": True, "profile": saved})
                return

            if self.path == "/api/extract":
                text = data.get("text", "").strip()
                if len(text) < 10:
                    raise ValueError("请输入至少10个字符的工作文本")
                speaker_map = data.get("speaker_map") or {}
                self.send_json({"ok": True, "draft": extract_text(text, speaker_map)})
                return

            if self.path == "/api/realign_draft":
                draft = data.get("draft") or {}
                self.send_json({"ok": True, "draft": enrich_and_ground_draft(draft)})
                return

            if self.path == "/api/commit":
                result = data.get("draft") or {}
                source = data.get("source", "").strip()
                if not source:
                    raise ValueError("原始文本不能为空")
                speaker_map = data.get("speaker_map") or {}
                self.send_json(commit_draft(result, source, speaker_map))
                return

            if self.path == "/api/entity/save":
                base = ensure_kb()
                entities = load_entities(base)
                eid = str(data.get("id") or "").strip()
                name = str(data.get("name") or "").strip()
                if not name:
                    raise ValueError("实体名称不能为空")
                cat = str(data.get("category") or "BusinessEntity").strip()
                if cat not in VALID_CATEGORIES:
                    cat = "BusinessEntity"
                aliases = [str(a).strip() for a in (data.get("aliases") or []) if str(a).strip() and norm_key(a) != norm_key(name)]
                desc = str(data.get("description") or "").strip()
                etype = str(data.get("entity_type") or "核心对象").strip()
                props = data.get("properties") if isinstance(data.get("properties"), dict) else {}
                now = now_iso()

                target = next((e for e in entities if (eid and e.get("id") == eid) or norm_key(e.get("name", "")) == norm_key(name)), None)
                if target:
                    target["name"] = name
                    target["category"] = cat
                    target["entity_type"] = etype
                    target["aliases"] = list(dict.fromkeys(aliases))
                    target["description"] = desc
                    target["properties"] = props
                    target["updated_at"] = now
                else:
                    target = {
                        "id": "ENT-" + uuid.uuid4().hex[:8].upper(),
                        "name": name,
                        "category": cat,
                        "entity_type": etype,
                        "aliases": list(dict.fromkeys(aliases)),
                        "description": desc,
                        "properties": props,
                        "wiki_file": wiki_rel_path(cat, name),
                        "created_at": now,
                        "updated_at": now,
                        "source_ids": ["MANUAL"],
                    }
                    entities.append(target)
                save_entities(entities, base)
                self.send_json({"ok": True, "entity": target})
                return

            if self.path == "/api/entity/merge":
                src_id = str(data.get("source") or "").strip()
                tgt_id = str(data.get("target") or "").strip()
                self.send_json(merge_two_entities(src_id, tgt_id))
                return

            if self.path == "/api/entity/delete":
                base = ensure_kb()
                eid = str(data.get("id") or "").strip()
                add_bl = bool(data.get("add_to_blacklist", False))
                entities = load_entities(base)
                target = next((e for e in entities if e.get("id") == eid or norm_key(e.get("name", "")) == norm_key(eid)), None)
                if not target:
                    raise ValueError("未找到要删除的实体")
                entities = [e for e in entities if e.get("id") != target.get("id")]
                save_entities(entities, base)
                if add_bl and target.get("name"):
                    cur_bl = load_blacklist(base)
                    save_blacklist(cur_bl + [target["name"]], base)
                self.send_json({"ok": True})
                return

            if self.path == "/api/relation/save":
                base = ensure_kb()
                relations = load_relations(base)
                frm = str(data.get("from") or "").strip()
                to = str(data.get("to") or "").strip()
                rtype = str(data.get("type") or "RELATES_TO").strip().upper()
                reason = str(data.get("reason") or "").strip()
                if not frm or not to or norm_key(frm) == norm_key(to):
                    raise ValueError("起点和终点实体不能为空且不能相同")
                if rtype not in VALID_RELATIONS:
                    rtype = "RELATES_TO"
                now = now_iso()
                existing = next((r for r in relations if norm_key(r.get("from", "")) == norm_key(frm) and r.get("type") == rtype and norm_key(r.get("to", "")) == norm_key(to)), None)
                if existing:
                    existing["reason"] = reason
                    existing["updated_at"] = now
                else:
                    relations.append({
                        "id": "REL-" + uuid.uuid4().hex[:8].upper(),
                        "from": frm,
                        "type": rtype,
                        "to": to,
                        "reason": reason,
                        "created_at": now,
                        "updated_at": now,
                        "source_ids": ["MANUAL"],
                    })
                save_relations(relations, base)
                self.send_json({"ok": True})
                return

            if self.path == "/api/relation/delete":
                base = ensure_kb()
                rid = str(data.get("id") or "").strip()
                relations = [r for r in load_relations(base) if r.get("id") != rid]
                save_relations(relations, base)
                self.send_json({"ok": True})
                return

            if self.path == "/api/blacklist":
                base = ensure_kb()
                words = data.get("blacklist")
                if isinstance(words, list):
                    save_blacklist(words, base)
                elif data.get("add"):
                    cur = load_blacklist(base)
                    save_blacklist(cur + [str(data["add"])], base)
                elif data.get("remove"):
                    target_norm = norm_key(str(data["remove"]))
                    cur = [w for w in load_blacklist(base) if norm_key(w) != target_norm]
                    save_blacklist(cur, base)
                self.send_json({"ok": True, "blacklist": load_blacklist(base)})
                return

            if self.path == "/api/wiki/save":
                base = ensure_kb()
                entities = load_entities(base)
                entity_name = str(data.get("entity") or "").strip()
                cat = str(data.get("category") or "BusinessEntity").strip()
                content = str(data.get("content") or "").strip()
                if not entity_name or not content:
                    raise ValueError("实体名与 Wiki 内容不能为空")
                existing_p = find_existing_wiki_path(base, cat, entity_name, entities)
                target_p = existing_p if existing_p else (base / wiki_rel_path(cat, entity_name))
                body_md = strip_frontmatter(content)
                if not body_md.startswith("# "):
                    body_md = f"# {entity_name}\n\n{body_md}"
                target_p.write_text(
                    f"---\nentity: {entity_name}\ncategory: {cat}\nupdated_at: {now_iso()}\n---\n\n{body_md}\n",
                    encoding="utf-8",
                )
                rel_str = str(target_p.relative_to(base)).replace("\\", "/")
                matched = find_matching_entity(entity_name, entities)
                if matched:
                    matched["wiki_file"] = rel_str
                    save_entities(entities, base)
                self.send_json({"ok": True, "file": rel_str})
                return

            if self.path == "/api/fact/update":
                base = ensure_kb()
                fid = str(data.get("id") or "").strip()
                new_status = str(data.get("status") or "").strip()
                delete_flag = bool(data.get("delete", False))
                updated = False
                for fp in (base / "20_facts").glob("*.jsonl"):
                    try:
                        lines = fp.read_text(encoding="utf-8").splitlines()
                        out_lines = []
                        changed = False
                        for line in lines:
                            if not line.strip():
                                continue
                            obj = json.loads(line)
                            if obj.get("id") == fid:
                                changed = True
                                updated = True
                                if delete_flag:
                                    continue
                                if new_status in ("confirmed", "discussing", "proposed", "historical", "unknown"):
                                    obj["status"] = new_status
                            out_lines.append(json.dumps(obj, ensure_ascii=False))
                        if changed:
                            fp.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
                    except Exception:
                        continue
                self.send_json({"ok": True, "updated": updated})
                return

            self.send_json({"error": "Not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)


if __name__ == "__main__":
    ensure_kb()
    print("Work Memory running at http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
