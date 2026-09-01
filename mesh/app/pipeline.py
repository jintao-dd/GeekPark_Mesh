"""GeekPark Mesh · 管线定义与执行器

**这是 25 步的唯一事实源。** 后台界面显示的步骤、后端真正执行的动作，都读这一份，
不允许两边各写一份——那正是"UI 上写了、后端没做"的来源。

每步的 `by` 字段标明它由谁执行：
  - "code"  : 后端代码离散执行，跑完能给出真实数字（可验收）
  - "model" : 在抽取的提示词内部执行，随抽取阶段整体完成
UI 会把两者区分显示。**不许给 model 步伪造独立进度条。**
"""
from __future__ import annotations
import json
import traceback

from . import db, llm, merge, job_store, ask_concurrency

KIND = "pipeline"

# ---------------------------------------------------------------- 步骤表
PROC = [
    dict(sk="intake", k="接收 · 隔离入库", by="code",
         h="原样写入原始层，与网站物理隔离"),
    dict(sk="transcribe", k="转写与识别", by="code",
         h="录音转文字、录屏抽帧拼接、按说话人分段；说话人归属可能不准，转写稿只进原始层"),
    dict(sk="normalize", k="清洗 · 去重", by="code",
         h="去掉寒暄与重复，统一格式"),
]

DESIGN = [
    dict(sk="extract", k="按类型分别抽取", by="model",
         h="例会分「决定」与「想法」，沟通记录用真名，周报按白名单字段，文章取标题日期作者链接——每类材料一套规则，不混为一谈"),
    dict(sk="owner-attrib", k="归属判定 · 条目级", by="code",
         h="聚合器抓来的文档是混合体，一份里可能同时有多个团队的内容。归属按条判定，不按文件判定"),
    dict(sk="zone5-filter", k="⑤区过滤 · 人与钱", by="code",
         h="融资金额、条款、谈判立场、薪酬命中即整条拦截，代码硬执行。原文全量留在隔离区，只在对外这一层暂扣"),
    dict(sk="listed-check", k="上市 / pre-IPO 识别", by="model",
         h="涉及上市或临近上市的公司，给投资侧收件人版本单独加过滤，规避合规风险"),
    dict(sk="pool-match", k="跨期比对 · 系统的记忆", by="code",
         h="对照信息池近 12 期，识别谁首次进入记录、谁与往期同名。没有这一步，周报只是一次性摘要"),
    dict(sk="cross-channel-merge", k="跨通道合并 · 同一件事只算一次", by="code",
         h="同一场会可能同时出现在日历、录音和聚合文档里。按实体加事件合并，合并后保留全部来源行。不做这一步，同团队的数据到达两次会被误判成跨部门关系"),
    # 以下随「生成预览/草稿」执行，挖掘阶段只占位说明，避免 UI 假完成
    dict(sk="relation-link", k="关系匹配", by="defer",
         h="在生成周报草稿时由关系候选 + 模型完成，挖掘阶段不单独跑"),
    dict(sk="sync-pick", k="可同步性判断", by="defer",
         h="在生成周报草稿时执行（跨团队 provenance 校验）"),
    dict(sk="compose", k="五块合成", by="defer",
         h="在生成周报草稿时合成五块结构"),
    dict(sk="cite", k="来源行生成", by="defer",
         h="在生成周报草稿时写入来源行"),
    dict(sk="lint", k="术语与标签检查", by="code",
         h="禁用词、日程确定度、「不代表承诺」提示、只列本期提交团队"),
    dict(sk="name-verify", k="姓名与职级核对", by="defer",
         h="随抽取提示词约束；挖掘阶段不做二次模型核对"),
    dict(sk="zone-sort", k="六区分拣", by="defer",
         h="随抽取写入 zone；代码硬拦⑤区/L3"),
    dict(sk="decision-split", k="决定与想法分离", by="defer",
         h="随 T6 等抽取提示词执行"),
    dict(sk="speaker-doubt", k="说话人存疑标记", by="defer",
         h="随转写类抽取提示词执行"),
    dict(sk="value-extract", k="价值提取 · 有没有下一步", by="defer",
         h="随抽取提示词执行"),
    dict(sk="certainty-tag", k="确定度标注", by="defer",
         h="随抽取提示词执行"),
    dict(sk="first-seen", k="首次进入判定", by="code",
         h="对照信息池，确认哪些人和公司是本期第一次进入记录。本周导语从这里取角度"),
    dict(sk="invest-guard", k="投资侧合规二次过滤", by="defer",
         h="随 listed / zone 规则与草稿生成"),
    dict(sk="contrib-list", k="贡献团队清点", by="code",
         h="按归属团队计，不按采集通道计。只列本期有材料进来的团队，不写谁没交"),
    dict(sk="trace-keep", k="可追溯留痕", by="code",
         h="被暂扣和被脱敏的原文全量留在隔离区，附来源与时间。脱敏只发生在对外这一层，底库不动"),
    dict(sk="draft-save", k="落盘为草稿", by="code",
         h="挖掘结束后旧草稿失效，须重新「生成周报草稿」才能上线"),
]

ALL_STEPS = PROC + DESIGN


def steps_for_ui() -> list[dict]:
    out = []
    for i, s in enumerate(PROC):
        out.append({**s, "i": i, "tier": "proc"})
    for j, s in enumerate(DESIGN):
        out.append({**s, "i": len(PROC) + j, "tier": "design"})
    return out


def _defaults(slug: str) -> dict:
    return {
        "slug": slug, "running": False, "done": False, "error": None,
        "cur": -1, "results": {}, "log": [], "token": 0,
    }


def _new_state(slug: str) -> dict:
    st = _defaults(slug)
    st["running"] = True
    return st


def get_state(slug: str) -> dict:
    return job_store.get(KIND, slug, _defaults(slug))


def _set(slug, **kw):
    st = job_store.get(KIND, slug, _new_state(slug))
    st.update(kw)
    job_store.put(KIND, slug, st)


def _mark(slug, sk, result=""):
    st = job_store.get(KIND, slug, _new_state(slug))
    idx = next((i for i, s in enumerate(ALL_STEPS) if s["sk"] == sk), -1)
    st["cur"] = max(int(st.get("cur") or -1), idx)
    if result:
        results = dict(st.get("results") or {})
        results[sk] = result
        st["results"] = results
    job_store.put(KIND, slug, st)


def _is_current(slug: str, token: int) -> bool:
    return job_store.is_current(KIND, slug, token, _defaults(slug))


def start(slug: str, force: bool = False) -> None:
    """启动一次完整管线。force=True 时取消卡住/旧任务并重新开跑。"""
    from . import job_runtime

    claimed = job_store.try_claim(KIND, slug, _defaults(slug), _new_state(slug), force=force)
    if not claimed:
        return
    token = int(claimed.get("token") or 0)
    job_runtime.spawn_after_claim(KIND, slug, _defaults(slug), _run, (slug, token))


def _run(slug: str, token: int = 0) -> None:
    con = db.connect()
    try:
        if not _is_current(slug, token):
            return
        r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
        iid = r["id"]
        srcs = [dict(x) for x in con.execute(
            "SELECT id, stype, team, title, text, channel, meta FROM sources WHERE issue_id=? AND length(text)>0", (iid,))]

        _mark(slug, "intake", f"{len(srcs)} 文件")
        n_aud = sum(1 for s in srcs if (s["stype"] or "") in ("T6",) and "录音" in (s["title"] or ""))
        _mark(slug, "transcribe", f"{n_aud or '—'} 段")
        _mark(slug, "normalize", "已整理")

        # ---- 抽取（模型层：extract 及其后的 model 步随本阶段整体完成）----
        got = 0
        total = len(srcs)
        for i, s in enumerate(srcs, 1):
            if not _is_current(slug, token):
                return
            title = (s["title"] or f"来源 {s['id']}")[:36]
            _mark(slug, "extract", f"抽取中 {i}/{total} · {title}")
            skip_split = False
            try:
                meta_obj = json.loads(s["meta"] or "{}") if isinstance(s.get("meta"), str) else (s.get("meta") or {})
                skip_split = (meta_obj.get("split") or {}).get("mode") == "pre_split"
            except (json.JSONDecodeError, TypeError, AttributeError):
                skip_split = False
            items, split_meta = None, None
            try:
                with ask_concurrency.llm_slot(pool="job"):
                    items, split_meta = llm.extract_source(
                        s["stype"], s["team"], s["title"], s["text"] or "",
                        period_start=r["date_start"] or "",
                        period_end=r["date_end"] or "",
                        period_label=r["period_label"] or "",
                        channel=s.get("channel") or "manual",
                        skip_split=skip_split,
                        source_id=s["id"],
                    )
            except ask_concurrency.AskBusyError as e:
                raise RuntimeError(f"抽取排队超时：{e}") from e
            except ask_concurrency.JobBusyError as e:
                raise RuntimeError(f"抽取排队超时：{e}") from e
            if not _is_current(slug, token):
                return
            con.execute("DELETE FROM items WHERE source_id=?", (s["id"],))
            from .aggregator import merge_source_meta
            from .attribution import apply_attribution_to_item
            for it in items:
                item_stype = it.get("item_stype") or s["stype"]
                attr = apply_attribution_to_item(
                    it, source_team=s["team"] or "", segment_team=it.get("_segment_team"),
                )
                owner = attr.owner_team
                blocked = int(it.get("blocked") or 0)
                con.execute(
                    """INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,
                       source_label,pointer,blocked,owner_team,channel,source_labels,owner_provenance,llm_owner_team_hint)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (iid, s["id"], owner or it.get("team"), item_stype, it["zone"], it["level"], it["kind"], it["text"],
                     json.dumps(it["entities"], ensure_ascii=False), json.dumps(it["roles"], ensure_ascii=False),
                     json.dumps(it["signals"], ensure_ascii=False), it["source_label"], it["pointer"], blocked,
                     owner, s["channel"] or "manual",
                     json.dumps([it["source_label"]] if it.get("source_label") else [], ensure_ascii=False),
                     attr.provenance, attr.llm_owner_team_hint or it.get("llm_owner_team_hint")))
                for name in it["entities"]:
                    if name:
                        con.execute("INSERT INTO entities(name,kind,first_issue) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING",
                                    (name, "auto", slug))
                got += 1
            meta = merge_source_meta(s.get("meta"), split_meta)
            try:
                meta_obj = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
            except (json.JSONDecodeError, TypeError):
                meta_obj = {}
            if llm.split_needs_review(split_meta):
                sp = dict(meta_obj.get("split") or {})
                sp["needs_review"] = True
                meta_obj["split"] = sp
                meta = json.dumps(meta_obj, ensure_ascii=False)
            con.execute("UPDATE sources SET extracted=1, meta=? WHERE id=?", (meta, s["id"]))
            con.commit()
            _mark(slug, "extract", f"已完成 {i}/{total} · 候选 {got}")
        if not _is_current(slug, token):
            return
        _mark(slug, "extract", f"候选 {got}")

        # ---- 代码步：真跑，真数字 ----
        miss = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=? AND (owner_team IS NULL OR owner_team='')", (iid,)).fetchone()["c"]
        mixed = con.execute("SELECT COUNT(DISTINCT source_id) c FROM items WHERE issue_id=? AND channel='aggregator'", (iid,)).fetchone()["c"]
        _mark(slug, "owner-attrib", f"归属 {got - miss} 条 · 混合文档 {mixed}" + (f" · 待指定 {miss}" if miss else ""))

        blocked = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=? AND blocked=1", (iid,)).fetchone()["c"]
        _mark(slug, "zone5-filter", f"拦截 {blocked}")
        _mark(slug, "listed-check", "已标记")

        names = set()
        for x in con.execute("SELECT entities FROM items WHERE issue_id=? AND blocked=0", (iid,)):
            try:
                names.update(json.loads(x["entities"] or "[]"))
            except Exception:
                pass
        first = [n for n in names if (con.execute("SELECT first_issue FROM entities WHERE name=?", (n,)).fetchone() or {"first_issue": slug})["first_issue"] == slug]
        _mark(slug, "pool-match", f"首次 {len(first)}")

        st = merge.apply_merge(con, iid)
        _mark(slug, "cross-channel-merge", f"合并 {st['groups']} 组 · 来源 {st['source_lines']} 行")

        for sk in ("relation-link", "sync-pick", "compose", "cite"):
            _mark(slug, sk, "待预览/草稿阶段")

        hits = llm.forbidden_hits(" ".join(
            (x["text"] or "") for x in con.execute("SELECT text FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL", (iid,))))
        _mark(slug, "lint", f"问题 {len(hits)}" + ("：" + "、".join(hits[:4]) if hits else ""))

        for sk in ("name-verify", "zone-sort", "decision-split", "speaker-doubt",
                   "value-extract", "certainty-tag"):
            _mark(slug, sk, "随抽取提示词")

        _mark(slug, "first-seen", f"首次 {len(first)}")
        _mark(slug, "invest-guard", "随草稿/规则")

        teams = [x["owner_team"] for x in con.execute(
            "SELECT DISTINCT owner_team FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL AND owner_team IS NOT NULL AND owner_team<>''", (iid,))]
        _mark(slug, "contrib-list", f"团队 {len(teams)}")
        _mark(slug, "trace-keep", f"留痕 {blocked} 条")

        kept = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL", (iid,)).fetchone()["c"]
        _mark(slug, "draft-save", f"条目 {kept}")
        # 挖掘完成后旧草稿失效，必须重新「生成周报草稿」才能上线
        if not _is_current(slug, token):
            return
        db.mark_draft_stale(con, iid)
        con.commit()
        _set(slug, running=False, done=True)
    except Exception as e:
        if _is_current(slug, token):
            _set(slug, running=False, done=False, error=f"{e}", log=[traceback.format_exc()[-1200:]])
    finally:
        con.close()
