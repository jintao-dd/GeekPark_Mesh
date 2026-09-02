"""GeekPark Mesh · EDM（预览邮件）生成与发送

浏览器预览：issue_edm.html（legacy）· 正式邮件：edm_email_inline.html
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

APP_DIR = Path(__file__).resolve().parent
_env = Environment(
    loader=FileSystemLoader(str(APP_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)

FLAG_COLORS = {
    "两处记录待核对": "#EA580C",
    "一方有需求，另一方尚未接触": "#0891B2",
    "一方接触了，另一方正在接触": "#16A34A",
    "已公开报道，内部也在用": "#2563EB",
    "同一件事，两个部门各知一半": "#9333EA",
    "采访对象也是客户": "#DB2777",
    "两个部门各有判断": "#64748B",
    "已联动": "#166534",
    "一方接触，另一方用得上": "#854D0E",
    "外部在热聊，我们还没碰": "#4338CA",
    "海外接触，国内可能承接": "#0D9488",
    "海外新发现，国内尚未接触": "#0369A1",
    "中英文站同周各自成稿": "#0E7490",
    "一方报道了，另一方在接触": "#7C3AED",
    "同一赛道，各自在做": "#57534E",
    "同一条赛道，各自在做": "#57534E",
    "同一公司，不同触点": "#57534E",
    "已排期，内容侧待安排": "#9D174D",
}

_SUG_MARK = re.compile(r"^[→\-–>]+\s*")

SUBJECT_EMOJIS = ["🐊", "🐉", "🐢", "🦕", "🦗", "🌻", "🌵", "🌴", "🧃", "🍵", "🏜️", "🗽", "🚛", "🎋", "🔫", "🔋"]
SUBJECT_FORBIDDEN = (
    "情报",
    "建议",
    "最值得看",
    "共振",
    "同现",
    "主体",
    "冲突",
    "分歧",
    "缺口",
    "不一致",
    "通知",
    "周报",
    "极客公园",
    "系统",
)
_SUBJECT_HIGHLIGHT_MAX = 28


def _trunc(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _preview_lines(text: str, *, lines: int = 3, width: int = 30) -> list[str]:
    """拆成若干短行预览（邮件单列换行）。"""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    out: list[str] = []
    rest = text
    while rest and len(out) < lines:
        if len(rest) <= width:
            out.append(rest)
            break
        chunk = rest[:width]
        for sep in ("。", "；", "，", "、", " "):
            pos = chunk.rfind(sep)
            if pos > width // 3:
                chunk = rest[: pos + 1].strip()
                rest = rest[pos + 1 :].lstrip()
                out.append(chunk)
                break
        else:
            out.append(chunk.rstrip() + "…")
            rest = rest[width:].lstrip()
    return out


def _strip_listy_lead(lead: str) -> str:
    """去掉 lead 里罗列人名/公司/数字的片段。"""
    s = re.sub(r"\d+[+]?|\d+", "", lead or "")
    s = re.sub(r"[：:][^。；]+(?=[。；]|$)", "。", s)
    s = re.sub(r"(公司与人|首次进入记录|首次出现的话题)[：:][^。；]+", "", s)
    s = re.sub(r"\s+", " ", s).strip(" 。；，、")
    return s


def _clean_summary(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r"\s*等\s*等\s*", "等", text)
    text = re.sub(r"([，。；])\s+", r"\1", text)
    return text


def _editorial_summary(data: dict) -> str:
    """整体概括本期，不罗列名称、不重复 KPI 数字。"""
    themes: list[str] = []
    for g in (data.get("keywords") or {}).get("groups") or []:
        for it in g.get("items") or []:
            n = (it.get("name") or "").strip()
            if n and n not in themes:
                themes.append(n)
            if len(themes) >= 4:
                break
        if len(themes) >= 4:
            break

    multi_team = sum(1 for r in (data.get("relations") or []) if len(_relation_teams(r)) >= 2)

    bits: list[str] = []
    if themes:
        head = "、".join(themes[:3])
        tail = "等" if len(themes) > 3 else ""
        bits.append(f"本期在 {head}{tail}方向有新进展")
    if multi_team:
        bits.append(f"含 {multi_team} 组跨团队可对齐事项")
    elif data.get("relations"):
        bits.append("跨团队关系与接触记录已汇总")
    if bits:
        return _clean_summary(_trunc("；".join(bits) + "。", 88))

    cleaned = _strip_listy_lead(data.get("lead") or "")
    if len(cleaned) >= 12:
        return _clean_summary(_trunc(cleaned, 88))
    return "本期各团队记录已整理入 Mesh，可在下方浏览各板块完整内容。"


def _relation_teams(r: dict) -> list[str]:
    out: list[str] = []
    for t in r.get("teams") or []:
        t = _SUG_MARK.sub("", (t or "").strip())
        if t and t not in out:
            out.append(t)
    return out


def _items_from_groups(section: dict | None) -> list[dict]:
    if not section:
        return []
    out: list[dict] = []
    for g in section.get("groups") or []:
        for it in g.get("items") or []:
            if (it.get("name") or "").strip():
                out.append(it)
    return out


def _contact_items(contacts: list) -> list[str]:
    names: list[str] = []
    for c in contacts or []:
        for g in c.get("groups") or []:
            for it in g.get("items") or []:
                n = (it.get("name") or "").strip()
                if n and n not in names:
                    names.append(n)
    return names


def _pick_relation_hooks(relations: list, *, featured: int = 8, extra: int = 0) -> tuple[list[dict], list[dict]]:
    """精选跨团队关系钩子（邮件内展示完整卡片）。"""
    pool = [(i, r) for i, r in enumerate(relations or [])]
    if not pool:
        return [], []

    def score(item: tuple[int, dict]) -> tuple:
        r = item[1]
        teams = _relation_teams(r)
        return (
            0 if r.get("weak") else 1,
            len(teams),
            len(r.get("title") or ""),
        )

    def row(r: dict) -> dict:
        teams = _relation_teams(r)
        return {
            "title": r.get("title") or "",
            "label": r.get("label") or "",
            "hook_para": _to_paragraph(r.get("body") or "", 130),
            "teams": teams[:3],
            "weak": bool(r.get("weak")),
            "color": FLAG_COLORS.get(r.get("label") or "", "#344054"),
        }

    ranked = sorted(pool, key=score, reverse=True)
    featured_rows: list[dict] = []
    seen_teams: set[str] = set()
    used: set[int] = set()

    for idx, r in ranked:
        if len(featured_rows) >= featured:
            break
        teams = _relation_teams(r)
        novel = [t for t in teams if t not in seen_teams]
        if featured_rows and not novel and r.get("weak"):
            continue
        used.add(idx)
        for t in teams:
            seen_teams.add(t)
        featured_rows.append(row(r))

    if len(featured_rows) < featured:
        for idx, r in ranked:
            if len(featured_rows) >= featured:
                break
            if idx in used:
                continue
            used.add(idx)
            featured_rows.append(row(r))

    return featured_rows, []


def _to_paragraph(text: str, max_chars: int = 140) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    if text[-1] not in "。；！？":
        text += "。"
    return text


def _mesh_more_hint(count: int, unit: str, shown: int | None = None) -> str:
    if shown is not None and shown < count:
        return f"摘要 {shown}/{count}，全文在 Mesh"
    return f"非全文，完整 {count} {unit} 在 Mesh"


def _contact_word_cloud(names: list[str]) -> str:
    if not names:
        return ""
    return " · ".join(names)


def _keyword_groups(keywords: dict, *, max_items: int = 6) -> list[dict]:
    out: list[dict] = []
    for g in (keywords or {}).get("groups") or []:
        title = (g.get("title") or "").strip()
        items = [(it.get("name") or "").strip() for it in g.get("items") or [] if (it.get("name") or "").strip()]
        if not title and not items:
            continue
        out.append(
            {
                "title": title or "其他",
                "tags": items[:max_items],
                "more": max(0, len(items) - max_items),
            }
        )
    return out


def _plan_paragraph(plans: dict, *, max_items: int = 4) -> str:
    names: list[str] = []
    for g in (plans or {}).get("groups") or []:
        for it in g.get("items") or []:
            n = (it.get("name") or "").strip()
            if not n:
                continue
            cert = (it.get("cert") or "").strip()
            names.append(n + (f"（{cert}）" if cert else ""))
    if not names:
        return ""
    head = "、".join(names[:max_items])
    if len(names) > max_items:
        return f"各团队日程与会议里出现过的待办包括 {head} 等；确定度标在每一项后，完整列表见 Mesh。"
    return f"各团队日程与会议里出现过的待办包括 {head}；确定度标在每一项后，完整列表见 Mesh。"


def _view_items(views: list, *, limit: int = 5) -> list[dict]:
    out: list[dict] = []
    for v in views[:limit]:
        topic = (v.get("topic") or "").strip() or "看法"
        text = (v.get("text") or "").strip()
        text = _trunc(text, 56)
        if text and text[-1] not in "。；！？":
            text += "。"
        out.append({"topic": topic, "text": text})
    return out


def _relation_search_prompt(r: dict) -> dict | None:
    """从单条关系生成「团队之间」导向的启发式搜索问句。"""
    teams = _relation_teams(r)
    if len(teams) < 2:
        return None
    t1, t2 = teams[0], teams[1]
    title = _trunc((r.get("title") or "").strip(), 14)
    label = (r.get("label") or "").strip()
    if not title:
        return None

    if "各知一半" in label or "两个部门" in label:
        text = f"「{title}」{t1} 与 {t2} 各知道多少？"
    elif "一方接触" in label and "另一方" in label:
        text = f"「{title}」{t1} 和 {t2} 谁接触了、谁该对齐？"
    elif "已联动" in label:
        text = f"「{title}」{t1} 与 {t2} 怎么联动的？"
    elif "海外" in label and "国内" in label:
        text = f"「{title}」海外与国内团队怎么衔接？"
    elif "采访了" in label or "客户" in label:
        text = f"「{title}」内容侧与商务侧怎么交叉？"
    else:
        text = f"{t1} × {t2}：「{title}」值得对齐吗？"

    q = " ".join(x for x in [title, t1, t2, label[:8] if label else ""] if x)
    return {"label": text, "q": quote(q)}


def _build_search_links(data: dict) -> list[dict]:
    """本期相关的搜问入口：优先具体关系卡，避免空泛模板句。"""
    relations = [r for r in (data.get("relations") or []) if isinstance(r, dict)]
    out: list[dict] = []
    seen: set[str] = set()

    def add(label: str, q: str) -> None:
        label = _clean_summary(label)
        if not label or label in seen:
            return
        seen.add(label)
        out.append({"label": label, "q": q})

    # 优先：有实质叙事的关系（strong/parallel 先，再 watch）
    def _rank(r: dict) -> tuple:
        tier = (r.get("decision_tier") or "").strip().lower()
        tier_score = {"strong": 3, "parallel": 2, "watch": 1}.get(tier, 0)
        if not tier_score:
            # 无 tier 时：双实线团队优先
            solid = [t for t in (r.get("teams") or []) if not str(t).strip().startswith(("→", "->"))]
            tier_score = 2 if len(solid) >= 2 else 1
        return (tier_score, 0 if r.get("weak") else 1, len(r.get("evidence") or []))

    for r in sorted(relations, key=_rank, reverse=True):
        if len(out) >= 5:
            break
        item = _relation_search_prompt(r)
        if item:
            add(item["label"], item["q"])

    # 补一条：本期具体主体（从关系 title 抽）
    if len(out) < 6:
        for r in sorted(relations, key=_rank, reverse=True):
            title = _trunc((r.get("title") or "").strip(), 18)
            if not title or "：" in title and len(title) < 4:
                continue
            # 取冒号前主体
            subject = title.split("：", 1)[0].strip() if "：" in title else title
            if len(subject) < 2:
                continue
            q = quote(f"{subject} 跨团队")
            add(f"本期「{subject}」各团队分别记了什么？", q)
            break

    # 最后才用极少数标签型问题，且必须能对应到本期真实 label
    if len(out) < 6:
        present = {(r.get("label") or "") for r in relations}
        extras = [
            ("同一件事，两个部门各知一半", "本期还有哪些事两边各只知道一半？", "各知一半 本期"),
            ("同一公司，不同触点", "本期同一公司还有哪些不同触点？", "同一公司 不同触点"),
            ("一方接触，另一方用得上", "本期还有哪些一队碰到、另一队该知道的？", "一方接触 用得上 本期"),
        ]
        for pattern, question, q in extras:
            if len(out) >= 6:
                break
            if any(pattern in lbl for lbl in present):
                add(question, quote(q))

    return out[:6]


def _rel_see_all_cta(n_total: int, n_shown: int) -> str:
    remaining = max(0, n_total - n_shown)
    if remaining > 0:
        return f"还有 {remaining} 组未展开 · 去 Mesh 一次看完 →"
    return "在 Mesh 查看全部关系 →"


def _build_hooks(data: dict) -> dict:
    data = data or {}
    relations = data.get("relations") or []
    contacts = data.get("contacts") or []
    keywords = data.get("keywords") or {}
    plans = data.get("plans") or {}
    views = data.get("views") or []

    contact_names = _contact_items(contacts)
    kw_items = _items_from_groups(keywords)
    kw_names = [(it.get("name") or "").strip() for it in kw_items if (it.get("name") or "").strip()]
    plan_items = _items_from_groups(plans)
    plan_names = [(it.get("name") or "").strip() for it in plan_items if (it.get("name") or "").strip()]

    featured_rels, extra_rels = _pick_relation_hooks(relations)
    rel_shown = len(featured_rels)
    search_links = _build_search_links(data)

    kpis = list(data.get("kpis") or [])
    if not kpis:
        kpis = [
            {"n": str(len(relations)), "label": "可同步的关系"},
            {"n": str(len(contact_names)), "label": "接触过的人"},
            {"n": str(len(kw_names)), "label": "关注的事"},
            {"n": str(len(plan_names)), "label": "条日程"},
        ]

    sections: list[dict] = []
    if contact_names:
        sections.append(
            {
                "title": "接触过的人和公司",
                "anchor": "#who",
                "count": len(contact_names),
                "count_label": "条",
                "kind": "word_cloud",
                "word_cloud": _contact_word_cloud(contact_names),
                "more_hint": "",
            }
        )
    if kw_names:
        sections.append(
            {
                "title": "关注了什么",
                "anchor": "#what",
                "count": len(kw_names),
                "count_label": "项",
                "kind": "keywords",
                "keyword_groups": _keyword_groups(keywords),
                "more_hint": _mesh_more_hint(len(kw_names), "项"),
            }
        )
    if plan_names:
        sections.append(
            {
                "title": "日程与计划",
                "anchor": "#next",
                "count": len(plan_names),
                "count_label": "条",
                "kind": "paragraph",
                "paragraph": _plan_paragraph(plans),
                "more_hint": _mesh_more_hint(len(plan_names), "条"),
            }
        )
    if views:
        view_items = _view_items(views, limit=5)
        sections.append(
            {
                "title": "沟通中提到的看法",
                "anchor": "#views",
                "count": len(views),
                "count_label": "条",
                "kind": "views",
                "view_items": view_items,
                "more_hint": _mesh_more_hint(len(views), "条", len(view_items)),
            }
        )

    return {
        "featured_rels": featured_rels,
        "extra_rels": extra_rels,
        "rel_more_hint": _mesh_more_hint(len(relations), "组", rel_shown),
        "rel_see_all_label": _rel_see_all_cta(len(relations), rel_shown),
        "sections": sections,
        "search_links": search_links,
        "summary": _editorial_summary(data),
        "kpis": kpis[:4],
    }


def _edm_ctx(issue: dict, data: dict, base_url: str) -> dict:
    data = data or {}
    data.setdefault("kpis", [])
    data.setdefault("relations", [])
    hooks = _build_hooks(data)
    return {
        "issue": issue,
        "d": data,
        "hooks": hooks,
        "n_rel": len(data.get("relations", [])),
        "base_url": base_url.rstrip("/"),
        "flag_colors": FLAG_COLORS,
    }


def _is_video_team(name: str) -> bool:
    return "视频" in (name or "")


def _subject_date(issue: dict) -> str:
    disp = (issue.get("display_date") or "").strip()
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", disp)
    if m:
        return f"{int(m.group(2)):02d}.{int(m.group(3)):02d}"
    for key in ("published_at", "date_end", "slug"):
        raw = str(issue.get(key) or "")[:10]
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
        if m:
            return f"{int(m.group(2)):02d}.{int(m.group(3)):02d}"
    pl = issue.get("period_label") or ""
    m = re.search(r"(\d+)\.(\d+)\s*$", pl)
    if m:
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}"
    return "??.??"


def _subject_emoji(seed: str) -> str:
    idx = hashlib.md5((seed or "mesh").encode()).digest()[0] % len(SUBJECT_EMOJIS)
    return SUBJECT_EMOJIS[idx]


def _entity_from_title(title: str) -> str:
    title = (title or "").strip()
    if " · " in title:
        return title.split(" · ", 1)[0].strip() or title
    return title


_SUBJECT_TEAM_SHORT = {
    "硅谷 BD 团队": "硅谷",
    "Global Partnership 团队": "Global 团队",
    "Global Partnership": "Global 团队",
    "商业化团队": "商业团队",
}


def _team_subject_name(team: str) -> str:
    team = _SUG_MARK.sub("", (team or "").strip())
    return _SUBJECT_TEAM_SHORT.get(team, team)


def _sanitize_subject_highlight(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.replace("！", "").replace("!", "").replace("？", "").replace("?", "")
    text = text.replace("…", "").replace("...", "")
    for em in SUBJECT_EMOJIS:
        text = text.replace(em, "")
    text = re.sub(r"^Mesh\s*·\s*\d{2}\.\d{2}\s*[｜|]\s*", "", text)
    for w in SUBJECT_FORBIDDEN:
        text = text.replace(w, "")
    return text.strip(" ｜|·")


def _subject_valid_highlight(text: str) -> bool:
    if not text or len(text) > _SUBJECT_HIGHLIGHT_MAX:
        return False
    if any(ch in text for ch in "！!？?…"):
        return False
    return not any(w in text for w in SUBJECT_FORBIDDEN)


def _keyword_subject_candidates(data: dict) -> list[str]:
    names: list[str] = []
    for g in (data.get("keywords") or {}).get("groups") or []:
        for it in g.get("items") or []:
            n = (it.get("name") or "").strip()
            if n and n not in names:
                names.append(n)
    for n in _contact_items(data.get("contacts") or []):
        if n not in names:
            names.append(n)
    if not names:
        names.append("本期记录")
    names.sort(key=len)
    prefix = "本周关键词："
    out = [prefix + n for n in names if len(prefix + n) <= _SUBJECT_HIGHLIGHT_MAX]
    return out or [prefix + names[0]]


def _relation_subject_candidates(r: dict) -> list[str]:
    teams_all = _relation_teams(r)
    teams = [_team_subject_name(t) for t in teams_all if not _is_video_team(t)]
    entity = _entity_from_title(r.get("title") or "")
    label = (r.get("label") or "").strip()
    if not entity:
        return []

    out: list[str] = []
    if len(teams_all) >= 3:
        out.append(f"{entity}本周出现在三个部门的记录里")
    out.append(f"{entity}本周在两处记录里出现")

    if len(teams) >= 2:
        t1, t2 = teams[0], teams[1]
        if "已联动" in label:
            out.extend(
                [
                    f"{t1}和{t2}已经在对{entity}的事",
                    f"{entity}，{t1}和{t2}已在联动",
                ]
            )
        elif "一方接触" in label or "另一方用得上" in label or "另一方正在接触" in label:
            out.extend(
                [
                    f"{t1}接触了{entity}，{t2}也在看",
                    f"{entity}，{t1}和{t2}都有接触",
                ]
            )
        elif "采访对象" in label or "客户" in label:
            out.append(f"{t1}和{t2}都在接触{entity}")
        elif "海外" in label:
            out.append(f"{entity}，海外和国内都有记录")
        else:
            out.append(f"{t1}和{t2}都在接触{entity}")

    out.append(f"本周关键词：{entity}")

    seen: set[str] = set()
    deduped: list[str] = []
    for line in out:
        line = _sanitize_subject_highlight(line)
        if line and line not in seen:
            seen.add(line)
            deduped.append(line)
    return deduped


def _fit_subject_highlight(text: str, *, data: dict | None = None) -> str:
    """在字数上限内返回语义完整的看点，禁止省略号截断。"""
    clean = _sanitize_subject_highlight(text)
    if clean and _subject_valid_highlight(clean):
        return clean

    relation_lines: list[str] = []
    keyword_lines: list[str] = []
    if data:
        relations = data.get("relations") or []
        if relations:
            relation_lines = _relation_subject_candidates(relations[0])
        keyword_lines = _keyword_subject_candidates(data)

    for line in relation_lines + keyword_lines:
        if line and _subject_valid_highlight(line):
            return line

    if data and keyword_lines:
        return keyword_lines[0]
    return clean if clean else "本期有更新"


def _pick_keyword_fallback(data: dict) -> str:
    keywords = data.get("keywords") or {}
    for g in keywords.get("groups") or []:
        title = g.get("title") or ""
        if "公司" in title or "人" in title:
            for it in g.get("items") or []:
                n = (it.get("name") or "").strip()
                if n:
                    return n
    for g in keywords.get("groups") or []:
        for it in g.get("items") or []:
            n = (it.get("name") or "").strip()
            if n:
                return n
    contacts = _contact_items(data.get("contacts") or [])
    if contacts:
        return contacts[0]
    return "本期记录"


def _highlight_from_relation(r: dict) -> str | None:
    candidates = _relation_subject_candidates(r)
    for line in candidates:
        if _subject_valid_highlight(line):
            return line
    return candidates[0] if candidates else None


def _fallback_subject_highlight(data: dict) -> str:
    relations = data.get("relations") or []
    if relations:
        line = _highlight_from_relation(relations[0])
        if line:
            return _fit_subject_highlight(line, data=data)
    return _fit_subject_highlight(_keyword_subject_candidates(data)[0], data=data)


def _subject_payload(data: dict) -> dict:
    relations = []
    for r in (data.get("relations") or [])[:6]:
        relations.append(
            {
                "title": r.get("title") or "",
                "label": r.get("label") or "",
                "teams": _relation_teams(r),
                "body": _trunc(r.get("body") or "", 90),
            }
        )
    keywords: list[str] = []
    for g in (data.get("keywords") or {}).get("groups") or []:
        for it in g.get("items") or []:
            n = (it.get("name") or "").strip()
            if n and n not in keywords:
                keywords.append(n)
            if len(keywords) >= 12:
                break
        if len(keywords) >= 12:
            break
    return {
        "relations": relations,
        "keywords": keywords,
        "contacts": _contact_items(data.get("contacts") or [])[:12],
    }


def _highlight_from_llm(data: dict) -> str | None:
    try:
        from . import llm
        from .providers import get_provider

        if not get_provider().is_configured():
            return None
        system = llm.load_prompt("edm_subject")
        if not system:
            return None
        user = json.dumps(_subject_payload(data), ensure_ascii=False, indent=2)
        text = llm.call(system, user, max_tokens=120, json_mode=False)
        line = _sanitize_subject_highlight((text or "").strip().splitlines()[0])
        if line and len(line) <= _SUBJECT_HIGHLIGHT_MAX and _subject_valid_highlight(line):
            return line
        if line and len(line) > _SUBJECT_HIGHLIGHT_MAX:
            retry_user = (
                user
                + f"\n\n上一版「{line}」超过28字。请整句重写，不超过28字，语义必须完整，禁止省略号。"
            )
            text2 = llm.call(system, retry_user, max_tokens=120, json_mode=False)
            line2 = _sanitize_subject_highlight((text2 or "").strip().splitlines()[0])
            if line2 and len(line2) <= _SUBJECT_HIGHLIGHT_MAX and _subject_valid_highlight(line2):
                return line2
        fitted = _fit_subject_highlight(line or "", data=data)
        if _subject_valid_highlight(fitted):
            return fitted
    except Exception:
        return None
    return None


def build_edm_subject(issue: dict, data: dict, *, use_llm: bool = True) -> str:
    """邮件标题：{emoji}Mesh · MM.DD｜{看点}。后台列表请 use_llm=False 避免逐期调模型卡页。"""
    date = _subject_date(issue)
    emoji = _subject_emoji(str(issue.get("slug") or issue.get("id") or ""))
    if use_llm:
        highlight = _highlight_from_llm(data or {}) or _fallback_subject_highlight(data or {})
    else:
        highlight = _fallback_subject_highlight(data or {})
    highlight = _fit_subject_highlight(highlight, data=data)
    return f"{emoji}Mesh · {date}｜{highlight}"


def _plain_text(issue: dict, data: dict, base_url: str) -> str:
    slug = issue["slug"]
    url = f"{base_url.rstrip('/')}/{slug}"
    hooks = _build_hooks(data or {})
    n_rel = len((data or {}).get("relations", []))
    t = [
        build_edm_subject(issue, data or {}),
        "",
        (data or {}).get("question", ""),
        hooks["summary"],
        "",
        " ｜ ".join(f"{k.get('n')} {k.get('label')}" for k in hooks["kpis"]),
        "",
        f"可同步的关系（共 {n_rel} 组，邮件摘 {len(hooks['featured_rels'])} 条）",
    ]
    for r in hooks["featured_rels"]:
        teams = " · ".join(r["teams"])
        line = f"· {r['title']} —— {r['label']}"
        if teams:
            line += f"（{teams}）"
        if r.get("hook_para"):
            line += f"\n  {r['hook_para']}"
        t.append(line)
    t += [hooks["rel_see_all_label"], f"{url}#rel", ""]
    for sec in hooks["sections"]:
        t.append(f"{sec['title']} · 共 {sec['count']} {sec['count_label']}")
        t.append(f"  {sec['more_hint']}")
        if sec.get("word_cloud"):
            t.append(f"  {sec['word_cloud']}")
        elif sec.get("paragraph"):
            t.append(f"  {sec['paragraph']}")
        elif sec.get("keyword_groups"):
            for g in sec["keyword_groups"]:
                t.append(f"  {g['title']}：{' · '.join(g['tags'])}")
        elif sec.get("view_items"):
            for v in sec["view_items"]:
                t.append(f"  · {v['topic']}：{v['text']}")
        t.append(f"  {url}{sec['anchor']}")
    t += [
        "",
        "在 Mesh 里搜问查：",
    ]
    for s in hooks["search_links"]:
        t.append(f"  · {s['label']}")
    t += [
        "",
        f"访问 Mesh：{url}",
        f"往期：{base_url.rstrip('/')}/archive",
        "",
        "仅限极客公园内部使用。请勿转发、截图或对外引用。",
        f"此邮件由 GeekPark Mesh 在管理员确认后发出 · 数据最近更新：{issue.get('updated_at', '')}",
        "产品/开发：ZSS & DDD",
        "© GEEKPARK 内容中心",
    ]
    return "\n".join(t)


def render_edm(issue: dict, data: dict, base_url: str, logo_url: str = "") -> tuple[str, str]:
    """返回 (html, text)。邮件 HTML 为全内联样式，与邮箱客户端实际渲染一致。"""
    _ = logo_url
    ctx = _edm_ctx(issue, data, base_url)
    html_out = _env.get_template("edm_email_inline.html").render(**ctx)
    return html_out, _plain_text(issue, data, base_url)


def send_mail(to_addrs: list[str], subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user)
    if not (host and user and pw):
        return False, "未配置 SMTP（SMTP_HOST/SMTP_USER/SMTP_PASSWORD）"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        if os.environ.get("SMTP_SSL", "1") == "1":
            s = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            s = smtplib.SMTP(host, port, timeout=30)
            s.starttls()
        s.login(user, pw)
        s.sendmail(sender, to_addrs, msg.as_string())
        s.quit()
        return True, ""
    except Exception as e:
        return False, str(e)


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def parse_addrs(raw: str) -> list[str]:
    return [a.strip() for a in re.split(r"[,\s;]+", (raw or "")) if a.strip()]


def default_to(con) -> str:
    from . import db

    return db.get_setting(con, "edm_default_to") or os.environ.get("EDM_DEFAULT_TO", "") or ""


def issue_auto_send_enabled(con, issue_id: int) -> bool:
    from . import db

    v = db.get_setting(con, f"edm_auto_send:{issue_id}")
    if v is not None:
        return v == "1"
    return (db.get_setting(con, "edm_auto_send_default") or "1") == "1"


def mail_status_label(con, issue_id: int) -> dict:
    rows = [
        dict(x)
        for x in con.execute(
            "SELECT subject, ok, error, at, to_addr FROM mail_log WHERE issue_id=? ORDER BY id DESC LIMIT 8",
            (issue_id,),
        )
    ]
    if not rows:
        return {"label": "未发", "kind": "none", "recent": rows}
    formal = [r for r in rows if not (r.get("subject") or "").startswith("【测试】")]
    if any(r.get("ok") for r in formal):
        last = next(r for r in formal if r.get("ok"))
        return {"label": f"已正式发 · {last.get('at', '')}", "kind": "formal", "recent": rows}
    if rows[0].get("ok"):
        subj = rows[0].get("subject") or ""
        if subj.startswith("【测试】"):
            return {"label": f"已测试 · {rows[0].get('at', '')}", "kind": "test", "recent": rows}
    return {"label": f"发送失败 · {rows[0].get('at', '')}", "kind": "fail", "recent": rows}


def send_for_issue(
    con,
    issue: dict,
    data: dict,
    *,
    to_addrs: list[str],
    test: bool,
    base_url: str,
    logo_url: str = "",
) -> tuple[bool, str, str]:
    """渲染并发送；写入 mail_log 并 commit。返回 (ok, error, subject)。"""
    if not test and issue.get("status") != "published":
        return False, "正式发送仅限已上线期", ""
    if not to_addrs:
        return False, "未填写收件人", ""
    h, t = render_edm(issue, data or {}, base_url, logo_url)
    subject = ("【测试】" if test else "") + build_edm_subject(issue, data or {})
    ok, err = send_mail(to_addrs, subject, h, t)
    con.execute(
        "INSERT INTO mail_log(issue_id,to_addr,subject,ok,error) VALUES(?,?,?,?,?)",
        (issue["id"], ",".join(to_addrs), subject, 1 if ok else 0, err),
    )
    from . import db as _db
    with _db.write_lock():
        _db.commit_retry(con)
    return ok, err or "", subject
