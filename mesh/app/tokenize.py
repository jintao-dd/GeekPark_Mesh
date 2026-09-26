"""中文友好的轻量分词（零依赖）：CJK 单字+二元组，英文按词。供 FTS5 MATCH 使用。"""
from __future__ import annotations
import re

_SEG = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9_+./-]*%?")
_STOP = {
    "的", "了", "吗", "呢", "啊", "吧", "是", "在", "有", "和", "与", "或", "及",
    "什么", "哪些", "哪个", "怎么", "如何", "多少", "一下", "一个", "这个", "那个",
    "我们", "你们", "他们", "自己", "可以", "需要", "关于", "对于", "如果", "还是",
    "没有", "还没", "尚未", "已经", "最近", "过去", "公司", "内部", "分别", "知道",
    "有没有", "有人", "谁", "哪些人", "接触过", "接触了", "跟进", "跟进过", "采访", "采访过",
    "怎么样", "如何了", "相关", "进展", "看看", "了解", "请问", "本周", "上周",
    "这周", "近期", "这段", "时候", "内容", "情况", "消息",
    "片子", "较好", "更好", "不错", "好看", "相关内容",
}
_VERB_RESIDUE = {"触过", "跟人", "人接", "跟进", "采访", "进展", "相关", "看看", "了解", "有人"}
_TOPIC_SPLIT = re.compile(r"[与和及]|以及|还有")
_SOFT_CJK_WITH_LATIN = {
    "下半年", "上半年", "规划", "计划", "安排", "总结", "复盘", "讨论", "问题",
}
_AGENT_CJK_TERMS = ("智能", "能体", "体安", "安全")


def tokenize(text: str) -> list[str]:
    text = text or ""
    out: list[str] = []
    for seg in _SEG.findall(text):
        if re.match(r"[\u4e00-\u9fff]", seg[0]):
            for i, ch in enumerate(seg):
                if ch.strip():
                    out.append(ch)
                if i + 1 < len(seg):
                    out.append(seg[i : i + 2])
        else:
            out.append(seg.lower())
    return out


def tokenize_for_index(text: str) -> str:
    return " ".join(tokenize(text))


def _cjk_pieces(seg: str) -> list[str]:
    pieces = [seg]
    for stop in sorted(_STOP, key=len, reverse=True):
        nxt = []
        for p in pieces:
            if stop in p and len(p) > len(stop):
                nxt.extend(x for x in p.split(stop) if x)
            else:
                nxt.append(p)
        pieces = nxt
    return [p for p in pieces if p and p not in _STOP and len(p) > 1]


def _or_group(terms: list[str]) -> str | None:
    terms = [t for t in dict.fromkeys(terms) if t]
    if not terms:
        return None
    return f"({' OR '.join(terms)})" if len(terms) > 1 else terms[0]


def _piece_bigrams(piece: str) -> list[str]:
    return [piece[i : i + 2] for i in range(len(piece) - 1)] if len(piece) >= 2 else []


def _grams_for_piece(piece: str) -> list[str]:
    if len(piece) >= 8:
        return _piece_bigrams(piece[:4]) + _piece_bigrams(piece[-4:])
    return _piece_bigrams(piece)


def _is_bare_number(w: str) -> bool:
    return bool(w) and w.isdigit()


def _is_soft_cjk_with_latin(piece: str) -> bool:
    if piece in _SOFT_CJK_WITH_LATIN:
        return True
    return any(soft in piece and len(piece) <= 8 for soft in _SOFT_CJK_WITH_LATIN)


def build_match_query(q: str) -> str | None:
    """
    FTS MATCH 配方：组间 AND、组内扁平 OR（兼容 fts_pg，禁止嵌套括号）。
    - 评价口语（片子/较好）停用
    - 与/和 并列主题 → 单一 OR 组
    - 长片 → 头4 AND 尾4（两个扁平组）
    - 英文实体：丢裸数字与规划脚手架；Agent↔智能体
    """
    q = (q or "").strip()
    if not q:
        return None
    q = re.sub(
        r"全部历史|所有期|历史以来|不限时间|有史以来|不限日期|全部时期|所有历史|历年|"
        r"本周|上周|这周|今年|去年|本月|上月|近\s*\d+\s*个?月|过去\s*\d+\s*个?月|"
        r"(?:近|过去)\s*\d+\s*天|(?:近|最近|过去)\s*\d+\s*期|最近|近期|这段时间",
        " ",
        q,
    )

    latin: list[str] = []
    groups: list[str] = []

    for seg in _SEG.findall(q):
        if re.match(r"[\u4e00-\u9fff]", seg[0]):
            pieces = _cjk_pieces(seg)
            if len(pieces) >= 2 and _TOPIC_SPLIT.search(seg):
                # 并列主题：每片取头/尾二元组（保留「端侧」「座舱」），仍扁平 OR
                grams: list[str] = []
                for piece in pieces:
                    if len(piece) >= 2:
                        grams.append(piece[:2])
                    if len(piece) >= 4:
                        grams.append(piece[-2:])
                g = _or_group(grams)
                if g:
                    groups.append(g)
            else:
                for piece in pieces:
                    grams = _grams_for_piece(piece)
                    if len(piece) >= 8:
                        # 头 / 尾 两个 AND 组
                        hg = _or_group(_piece_bigrams(piece[:4]))
                        tg = _or_group(_piece_bigrams(piece[-4:]))
                        if hg:
                            groups.append(hg)
                        if tg:
                            groups.append(tg)
                    else:
                        g = _or_group(grams)
                        if g:
                            groups.append(g)
        else:
            w = seg.lower().replace('"', "")
            if _is_bare_number(w):
                continue
            if len(w) >= 2:
                latin.append(f'"{w}"')
            elif w:
                latin.append(w)

    if latin:
        out: list[str] = []
        for lit in latin:
            if lit.strip('"') == "agent":
                out.append(_or_group([lit, *_AGENT_CJK_TERMS]) or lit)
            else:
                out.append(lit)
        # 重扫中文：软丢规划脚手架；并列 OR / 长片规则同上
        for seg in _SEG.findall(q):
            if not re.match(r"[\u4e00-\u9fff]", seg[0]):
                continue
            pieces = _cjk_pieces(seg)
            if len(pieces) >= 2 and _TOPIC_SPLIT.search(seg):
                keep = [p for p in pieces if not _is_soft_cjk_with_latin(p)]
                if not keep:
                    continue
                grams = []
                for piece in keep:
                    if len(piece) >= 2:
                        grams.append(piece[:2])
                    if len(piece) >= 4:
                        grams.append(piece[-2:])
                g = _or_group(grams)
                if g:
                    out.append(g)
            else:
                for piece in pieces:
                    if piece in _VERB_RESIDUE or _is_soft_cjk_with_latin(piece):
                        continue
                    if len(piece) >= 8:
                        hg = _or_group(_piece_bigrams(piece[:4]))
                        tg = _or_group(_piece_bigrams(piece[-4:]))
                        if hg:
                            out.append(hg)
                        if tg:
                            out.append(tg)
                    else:
                        g = _or_group(_grams_for_piece(piece))
                        if g:
                            out.append(g)
        groups = out

    if not groups:
        toks = [
            t
            for t in tokenize(q)
            if t not in _STOP and len(t) >= 2 and not _is_bare_number(t)
        ]
        if not toks:
            return None
        return " OR ".join(dict.fromkeys(toks[:12]))
    if len(groups) > 6:
        groups = groups[:6]
    return " AND ".join(groups)


def query_terms(q: str, limit: int = 6) -> list[str]:
    q = (q or "").strip()
    terms: list[str] = []
    for seg in _SEG.findall(q):
        if re.match(r"[\u4e00-\u9fff]", seg[0]):
            for piece in _cjk_pieces(seg):
                if piece in _VERB_RESIDUE or piece in _STOP:
                    continue
                terms.append(piece)
                if len(piece) >= 4:
                    terms.append(piece[:2])
                    terms.append(piece[-2:])
                if len(piece) >= 8:
                    terms.append(piece[:4])
                    terms.append(piece[-4:])
        else:
            w = seg.lower().replace('"', "")
            if len(w) >= 2 and not _is_bare_number(w):
                terms.append(w)
            if w == "agent":
                terms.append("智能体")
    return sorted(dict.fromkeys(terms), key=len, reverse=True)[:limit]
