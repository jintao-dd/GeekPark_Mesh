"""中文友好的轻量分词（零依赖）：CJK 单字+二元组，英文按词。供 FTS5 MATCH 使用。"""
from __future__ import annotations
import re

_SEG = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9_+./-]*%?")
_STOP = {
    "的", "了", "吗", "呢", "啊", "吧", "是", "在", "有", "和", "与", "或", "及",
    "什么", "哪些", "哪个", "怎么", "如何", "多少", "一下", "一个", "这个", "那个",
    "我们", "你们", "他们", "自己", "可以", "需要", "关于", "对于", "如果", "还是",
    "没有", "还没", "尚未", "已经", "最近", "过去", "公司", "内部", "分别", "知道",
    # 问句脚手架：进 MATCH 的 AND 会误伤实体召回（section 未进 toks）
    "有没有", "有人", "谁", "哪些人", "接触过", "接触了", "跟进", "跟进过", "采访", "采访过",
    "怎么样", "如何了", "相关", "进展", "看看", "了解", "请问", "本周", "上周",
    "这周", "近期", "这段", "时候", "内容", "情况", "消息",
}
# 停用后仍可能残留的短动词片，有英文实体时丢弃
_VERB_RESIDUE = {"触过", "跟人", "人接", "跟进", "采访", "进展", "相关", "看看", "了解", "有人"}


def tokenize(text: str) -> list[str]:
    """返回用于索引的 token 列表（含单字与二元组）。"""
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
    """空格分隔，写入 FTS toks 列。"""
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
    out = []
    for piece in pieces:
        if not piece or piece in _STOP or len(piece) == 1:
            continue
        out.append(piece)
    return out


def _piece_to_group(piece: str) -> str | None:
    bigrams = [piece[i : i + 2] for i in range(len(piece) - 1)]
    if not bigrams:
        return None
    return f"({' OR '.join(bigrams)})" if len(bigrams) > 1 else bigrams[0]


def build_match_query(q: str) -> str | None:
    """
    把用户查询编成 FTS5 MATCH 表达式。
    各语义片段之间 AND；片段内二元组 OR（提高召回）。
    有明确英文实体时，丢掉问句动词脚手架，避免
    (接触 OR …) AND "openai" 因正文不含「接触」而漏召回。
    """
    q = (q or "").strip()
    if not q:
        return None
    # 时间/范围短语不参与 MATCH（已由时间窗处理）
    q = re.sub(
        r"全部历史|所有期|历史以来|不限时间|有史以来|不限日期|全部时期|所有历史|历年|"
        r"本周|上周|这周|今年|去年|本月|上月|近\s*\d+\s*个?月|过去\s*\d+\s*个?月|"
        r"(?:近|过去)\s*\d+\s*天|(?:近|最近|过去)\s*\d+\s*期|最近|近期|这段时间",
        " ",
        q,
    )
    latin: list[str] = []
    cjk: list[str] = []
    for seg in _SEG.findall(q):
        if re.match(r"[\u4e00-\u9fff]", seg[0]):
            for piece in _cjk_pieces(seg):
                g = _piece_to_group(piece)
                if g:
                    cjk.append((piece, g))
        else:
            w = seg.lower().replace('"', "")
            if len(w) >= 2:
                latin.append(f'"{w}"')
            elif w:
                latin.append(w)

    if latin:
        # 只保留较长主题中文（≥3 字），丢掉动词残片
        cjk_kept = []
        for piece, g in cjk:
            if piece in _STOP or piece in _VERB_RESIDUE:
                continue
            if len(piece) < 3:
                continue
            cjk_kept.append(g)
        groups = latin + cjk_kept
    else:
        groups = [g for _, g in cjk]

    if not groups:
        toks = [t for t in tokenize(q) if t not in _STOP and len(t) >= 2]
        if not toks:
            return None
        return " OR ".join(dict.fromkeys(toks[:12]))
    if len(groups) > 6:
        groups = groups[:6]
    return " AND ".join(groups)


def query_terms(q: str, limit: int = 6) -> list[str]:
    """抽取适合 LIKE 兜底的实体词（去脚手架）。"""
    q = (q or "").strip()
    terms: list[str] = []
    for seg in _SEG.findall(q):
        if re.match(r"[\u4e00-\u9fff]", seg[0]):
            for piece in _cjk_pieces(seg):
                if piece in _VERB_RESIDUE:
                    continue
                terms.append(piece)
        else:
            w = seg.lower().replace('"', "")
            if len(w) >= 2:
                terms.append(w)
    return sorted(dict.fromkeys(terms), key=len, reverse=True)[:limit]
