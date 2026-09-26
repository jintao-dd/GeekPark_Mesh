"""实体别名归一化：解决 FieldAI/Field AI、FounderPark/Founder Park 等同一实体不同写法。

原则：
- 只做轻量规则 + 可选 LLM fallback，不建持久知识图谱。
- 归一 key 仅用于候选生成阶段的 bucket/匹配；展示仍用原始最长/最规范名。
- 所有 LLM 调用必须带 audit trace；LLM 仅作为 alias suggestion，最终仍由代码硬规则把关。
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import llm

# 硬规则别名表：key 是归一 key，value 是「可接受的等价写法集合」。
# 只放高频、无歧义、经业务确认的别名；不放需 LLM 判断的模糊等价。
_HARDCODED_ALIAS_RULES: dict[str, set[str]] = {
    "fieldai": {"fieldai", "field ai", "field.ai"},
    "founderpark": {"founderpark", "founder park", "founder-park"},
    "reverie": {"reverie", "reverie ai", "reverieai"},
    "oppo": {"oppo"},
    "claude": {"claude", "claude opus", "claudeai"},
    "qwen": {"qwen", "千问", "qwen2"},
    "doubao": {"doubao", "豆包"},
    "bytedance": {"bytedance", "字节跳动", "字节"},
    "aliyun": {"aliyun", "阿里云"},
    "xiaomi": {"xiaomi", "小米"},
    "huawei": {"huawei", "华为"},
    "apple": {"apple", "苹果"},
    "google": {"google", "谷歌"},
    "microsoft": {"microsoft", "微软"},
    "openai": {"openai", "open ai"},
    "anthropic": {"anthropic"},
    "xai": {"xai", "x.ai"},
    "meta": {"meta", "facebook", "脸书"},
    "amazon": {"amazon", "亚马逊"},
    "nvidia": {"nvidia", "英伟达"},
    "tesla": {"tesla", "特斯拉"},
    "52du": {"52度眼镜", "52 度眼镜", "52du", "52度"},
    "noitom": {"noitom", "诺亦腾"},
    "seedstudio": {"seeed studio", "seeedstudio", "seeed"},
    "hellboss": {"helloboss", "hello boss"},
    "nota": {"notta"},
    "armaro": {"armaro"},
    "poko": {"破壳", "破壳创智"},
    "notta": {"notta.ai", "张岩"},
}

# 排序规则：优先命中长 key，避免 "oppo" 先被 "op" 误匹配。
_HARDCODED_KEYS = sorted(_HARDCODED_ALIAS_RULES.keys(), key=lambda k: -len(k))

_WS = re.compile(r"[\s\-_.]+")
_PUNCT_TRIM = re.compile(r"[·•|/／、,，.。:：;；\-—_（）()【】\[\]「」]+$")


def _norm_for_alias(name: str) -> str:
    """用于别名匹配的内部归一 key：折空白/标点/大小写，但保留 CJK 字符边界。"""
    # 对英文/数字做连接，CJK 字符之间不做连接（避免 "破壳创智" -> "破壳创智" 仍保留）
    s = (name or "").strip().lower()
    # 仅替换 ASCII 空白、连字符、点
    s = re.sub(r"[\s\-_.]+", "", s)
    return _PUNCT_TRIM.sub("", s)


def _norm_for_bucket(name: str) -> str:
    """用于共现 bucket 的 key：保留字符骨架，避免 'FieldAI' 与 'Field AI' 分桶。"""
    s = _WS.sub("", (name or "").strip().lower())
    return _PUNCT_TRIM.sub("", s)


def canonical_key(name: str) -> str:
    """公开归一 key：候选生成用。先尝试硬规则别名，否则用 bucket key。"""
    hard = _hardcoded_canonical(name)
    if hard:
        return hard
    return _norm_for_bucket(name)


def _hardcoded_canonical(name: str) -> str | None:
    """若 name 命中硬规则别名表，返回该规则的 canonical key。"""
    k = _norm_for_alias(name)
    if not k:
        return None
    for canon_key in _HARDCODED_KEYS:
        if k in _HARDCODED_ALIAS_RULES[canon_key]:
            return canon_key
        # 前缀/后缀安全：canon_key 是 k 的子串且 k 仅多出常见后缀/前缀
        if canon_key in k and len(k) - len(canon_key) <= 4:
            # 额外安全检查：必须共享核心子串，而不是只是短串包含
            if k.startswith(canon_key) or k.endswith(canon_key):
                return canon_key
    return None


def _llm_alias_suggestions(names: list[str]) -> dict[str, str]:
    """LLM fallback：对未命中硬规则的实体名，请求模型给出等价分组建议。

    返回：原始名 -> canonical key（仅当 LLM 明确建议且置信度高时）。
    注意：LLM 返回的建议仍需经代码校验（不能是子串/包含歧义）。
    """
    if len(names) < 2:
        return {}
    # 只送需要判断的短名单：高频出现但未命中硬规则的
    system = (
        "你是极客公园 Mesh 的实体对齐助手。任务：判断以下列表中是否有「同一实体的不同写法」。"
        "只输出 JSON：{\"groups\":[[\"nameA\",\"nameB\"],...]}。"
        "规则：\n"
        "1. 必须是同一公司/人/项目，不能是子公司、产品、创始人与公司的关系。\n"
        "2. 允许空格、大小写、连字符差异（如 'FounderPark' 与 'Founder Park'）。\n"
        "3. 不允许把 bare 'AI'、'Tech'、'Lab' 等泛词与具体实体归为一组。\n"
        "4. 没有别名则返回空 groups。"
    )
    user = json.dumps(names, ensure_ascii=False)
    try:
        out = llm.call_json_compliant(system, user, max_tokens=2000)
    except Exception:
        return {}
    groups = out.get("groups") if isinstance(out, dict) else None
    if not isinstance(groups, list):
        return {}
    mapping: dict[str, str] = {}
    for g in groups:
        if not isinstance(g, list) or len(g) < 2:
            continue
        group_names = [str(x).strip() for x in g if str(x).strip()]
        if len(group_names) < 2:
            continue
        # canonical key 取组内最常见的 bucket key
        keys = [(canonical_key(n), n) for n in group_names]
        keys = [(k, n) for k, n in keys if k]
        if not keys:
            continue
        canon_key = max(set([k for k, _ in keys]), key=lambda k: sum(1 for kk, _ in keys if kk == k))
        # 代码二次校验：组内任意两名不能有危险的子串包含（如 'AI' 在 'FieldAI'）
        safe = True
        norm_set = {_norm_for_alias(n) for n in group_names}
        for a in norm_set:
            for b in norm_set:
                if a == b:
                    continue
                # 若一名是另一名的严格子串，且长度差 > 2，则不安全（bare AI vs FieldAI）
                if (a in b or b in a) and abs(len(a) - len(b)) > 2:
                    safe = False
                    break
            if not safe:
                break
        if not safe:
            continue
        for n in group_names:
            mapping[n] = canon_key
    return mapping


def build_canonical_map(
    names: list[str],
    *,
    use_llm_fallback: bool = True,
) -> tuple[dict[str, str], dict[str, Any]]:
    """为 names 构建 canonical 映射。

    返回：
        name_to_canonical: dict[str, str]
        audit: {"rules_matched": [...], "llm_groups": [...], "fallback_count": int}
    """
    name_to_canonical: dict[str, str] = {}
    audit_rules: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    for n in names:
        if not n or not str(n).strip():
            continue
        key = canonical_key(n)
        hard = _hardcoded_canonical(n)
        if hard:
            name_to_canonical[n] = hard
            seen_keys.add(hard)
            audit_rules.append({"name": n, "canonical": hard, "source": "rule"})
        else:
            # 未命中规则时先以自身 bucket key 作为 canonical
            name_to_canonical[n] = key
            seen_keys.add(key)

    llm_audit: list[list[str]] = []
    if use_llm_fallback:
        uncovered = [n for n in names if _hardcoded_canonical(n) is None and str(n).strip()]
        uncovered = list(dict.fromkeys(uncovered))[:60]  # 限制 LLM 输入规模
        if len(uncovered) >= 2:
            llm_map = _llm_alias_suggestions(uncovered)
            for n, canon_key in llm_map.items():
                if n in name_to_canonical:
                    old = name_to_canonical[n]
                    name_to_canonical[n] = canon_key
                    seen_keys.add(canon_key)
                    if old != canon_key:
                        llm_audit.append([old, canon_key, n])

    return name_to_canonical, {
        "rules_matched": audit_rules,
        "llm_groups": llm_audit,
        "fallback_count": len(llm_audit),
    }


def canonicalize_entities_in_items(
    items: list[dict],
    *,
    use_llm_fallback: bool = True,
) -> tuple[list[dict], dict[str, Any]]:
    """在 items 上统一实体名：返回新 items + audit。

    不修改 item 的原始 entities 展示字段；额外写入：
        - _canonical_entities: list[str]  用于候选生成/匹配
        - _entity_canonical_map: dict[orig -> canonical]  用于调试
    """
    # 收集全部实体名
    all_names: list[str] = []
    for it in items:
        ents = it.get("entities") or []
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except (json.JSONDecodeError, TypeError):
                ents = []
        for n in ents:
            n = str(n).strip()
            if n:
                all_names.append(n)
    all_names = list(dict.fromkeys(all_names))

    name_to_canonical, audit = build_canonical_map(
        all_names, use_llm_fallback=use_llm_fallback
    )

    # 为未命中任何规则的名称兜底：同名同 canonical_key
    for n in all_names:
        if n not in name_to_canonical:
            name_to_canonical[n] = canonical_key(n)

    out: list[dict] = []
    for it in items:
        row = dict(it)
        ents = row.get("entities") or []
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except (json.JSONDecodeError, TypeError):
                ents = []
        canon = []
        emap = {}
        for n in ents:
            n = str(n).strip()
            if not n:
                continue
            k = name_to_canonical.get(n)
            if not k:
                k = canonical_key(n)
                name_to_canonical[n] = k
            canon.append(k)
            emap[n] = k
        row["_canonical_entities"] = list(dict.fromkeys(canon))
        row["_entity_canonical_map"] = emap
        out.append(row)

    return out, audit


def display_name_for_canonical(
    canonical: str,
    items: list[dict],
) -> str:
    """从 items 中选出 canonical key 对应的最佳展示名：优先长名、含空格、首字母大写。"""
    candidates: list[str] = []
    for it in items:
        emap = it.get("_entity_canonical_map") or {}
        for orig, k in emap.items():
            if k == canonical:
                candidates.append(orig)
    if not candidates:
        return canonical

    def score(n: str) -> tuple[int, int, str]:
        s = n.strip()
        # 分数：长度优先，含空格加1，大小写保持（casefold 不同）加1
        has_space = 1 if " " in s else 0
        has_mixed_case = 1 if s != s.lower() and s != s.upper() else 0
        return (len(s), has_space, has_mixed_case, s)

    return max(candidates, key=score)
