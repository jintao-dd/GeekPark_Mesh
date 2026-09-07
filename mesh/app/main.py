"""GeekPark Mesh · 主应用（FastAPI）"""
import os, json, datetime, re, threading
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE.parent / ".env")
from . import db, ingest, llm, auth, edm, edm_job, embed_job, merge, pipeline, preview_job, qa_structured, search, tokenize
from . import ask_engine, ask_scope, conversation, presets, chunk_index, embeddings, retriever, ask_turn, ask_query, ask_concurrency, ask_rate, job_store, ask_analysis
import time

_ASK_CACHE: dict[str, tuple[float, dict]] = {}
_ASK_CACHE_LOCK = threading.Lock()
_ASK_CACHE_TTL = int(os.environ.get("MESH_ASK_CACHE_TTL", "120") or "120")
_ASK_CACHE_MAX = 256
_UVICORN_WORKERS = max(1, int(os.environ.get("MESH_UVICORN_WORKERS", "1") or "1"))
if _UVICORN_WORKERS > 1 and _ASK_CACHE_TTL > 0:
    print(
        f"[mesh] MESH_UVICORN_WORKERS={_UVICORN_WORKERS}: 进程内 Ask 缓存已关闭（多 worker 不一致）",
        flush=True,
    )
    _ASK_CACHE_TTL = 0


def _ask_rate_ok(key: str) -> bool:
    """跨 worker 的 per-user/IP 速率限制（落库）。"""
    return ask_rate.allow(key)

app = FastAPI(title="GeekPark Mesh")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
RAW_DIR = Path(os.environ.get("MESH_RAW_DIR", str(BASE.parent / "data" / "raw")))
BASE_URL = os.environ.get("MESH_BASE_URL", "http://localhost:8080")


def _cookie_secure(request: Request | None = None) -> bool:
    """HTTPS 或显式 MESH_COOKIE_SECURE=1 时给 cookie 加 Secure。"""
    if (os.environ.get("MESH_COOKIE_SECURE") or "").strip() in ("1", "true", "yes"):
        return True
    if BASE_URL.lower().startswith("https://"):
        return True
    if request is not None:
        try:
            return (request.url.scheme or "").lower() == "https"
        except Exception:
            pass
    return False

FLAG_COLORS = edm.FLAG_COLORS
TEAM_ORDER = [
    "编辑部", "商业化团队", "硅谷 BD 团队", "Global Partnership 团队", "英文站",
    "品牌创意团队", "社群", "投资团队", "音频播客团队", "视频号团队", "CEO / 总裁办",
]


def _wants_html(request: Request) -> bool:
    """浏览器打开页面要 HTML；/api/* 与显式 JSON Accept 仍返回 JSON。"""
    path = request.url.path or ""
    if path.startswith("/api/"):
        return False
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return False
    return True


async def _html_or_json_error(request: Request, status_code: int, detail):
    """未登录 / 权限不足 / 404 等：浏览器给页面，API 仍给 JSON。"""
    from urllib.parse import quote

    if not isinstance(detail, str):
        detail = str(detail) if detail is not None else ""
    path = request.url.path
    q = request.url.query
    next_path = path + (("?" + q) if q else "")

    if not _wants_html(request):
        return JSONResponse({"detail": detail or "error"}, status_code=status_code)

    if status_code == 401:
        return RedirectResponse(
            f"/login?next={quote(next_path, safe='/?=&')}",
            status_code=302,
        )

    title = {
        403: "没有权限",
        404: "找不到页面",
        400: "请求有误",
        405: "不支持这种方式访问",
        422: "提交有误",
        500: "服务异常",
        503: "服务暂不可用",
    }.get(status_code, "出了点问题")
    message = detail or title
    if status_code == 403:
        message = detail if detail and detail != "权限不足" else "当前账号没有访问这一页的权限。如需开通，请联系所有者。"
    if status_code == 404:
        # Starlette 默认英文 "Not Found"
        if not detail or detail in ("Not Found", "not found", "404"):
            message = "这个地址没有对应内容，可能是链接写错了，或页面已下线。"
        elif "没有" in detail:
            message = detail
        else:
            message = detail
    if status_code == 422 and (not detail or detail.startswith("[")):
        message = "提交的内容不完整或格式不对，请返回后重试。"

    return templates.TemplateResponse(
        "error.html",
        ctx(
            request,
            code=status_code,
            title=title,
            message=message,
            next=next_path,
        ),
        status_code=status_code,
    )


@app.exception_handler(StarletteHTTPException)
async def mesh_starlette_http_exception(request: Request, exc: StarletteHTTPException):
    # 含路由未匹配的 404（否则会落到框架默认 JSON）
    return await _html_or_json_error(request, exc.status_code, exc.detail)


@app.exception_handler(HTTPException)
async def mesh_http_exception(request: Request, exc: HTTPException):
    return await _html_or_json_error(request, exc.status_code, exc.detail)


@app.exception_handler(RequestValidationError)
async def mesh_validation_exception(request: Request, exc: RequestValidationError):
    # 缺字段 / 类型不对：浏览器不要看 Pydantic JSON
    errs = []
    for e in exc.errors()[:6]:
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body")
        msg = e.get("msg") or "无效"
        errs.append(f"{loc}: {msg}" if loc else msg)
    detail = "提交内容有误：" + "；".join(errs) if errs else "提交内容有误"
    return await _html_or_json_error(request, 422, detail)


@app.exception_handler(Exception)
async def mesh_unhandled_exception(request: Request, exc: Exception):
    # 未捕获异常：浏览器友好页；日志里仍打 traceback
    import traceback
    print(f"[mesh] unhandled {request.method} {request.url.path}: {exc}\n{traceback.format_exc()}", flush=True)
    return await _html_or_json_error(
        request,
        500,
        "服务暂时出了点问题，请稍后重试。若反复出现，请联系管理员。",
    )


def _flash_redirect(url: str, err: str, status: int = 302) -> RedirectResponse:
    """表单失败回跳，带可读错误，避免白屏纯文本。"""
    from urllib.parse import quote
    msg = (err or "操作失败").replace("\n", " · ")[:400]
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep}err={quote(msg)}", status_code=status)

@app.on_event("startup")
def _startup():
    """启动只做轻量初始化；重索引一律丢后台，避免容器起不来 → 网关 502。"""
    try:
        db.init_db(seed=True)
    except Exception as e:
        print(f"[mesh] init_db failed: {e}", flush=True)

    def _bg_maintenance():
        con = db.connect()
        lock_held = False
        try:
            lock_held = db.try_maintenance_lock(con)
            if not lock_held:
                print("[mesh] maintenance skipped (another worker holds lock)", flush=True)
                return

            def _cache_bust():
                with _ASK_CACHE_LOCK:
                    _ASK_CACHE.clear()

            n_pub = n_if = n_chunk = n_emb = 0
            with db.write_lock():
                n_facts = con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"]
                n_pub = con.execute("SELECT COUNT(*) c FROM issues WHERE status='published'").fetchone()["c"]
                fact_slugs = con.execute("SELECT COUNT(DISTINCT issue_slug) c FROM entity_team_facts").fetchone()["c"]
                if n_pub and (not n_facts or fact_slugs < n_pub):
                    print("[mesh] backfilling entity_team_facts…", flush=True)
                    db.reindex_all_entity_facts(con)
                    db.commit_retry(con)
                    _cache_bust()
                try:
                    n_if = con.execute("SELECT COUNT(*) c FROM item_facts").fetchone()["c"]
                except Exception:
                    n_if = 0
                if n_pub and not n_if:
                    print("[mesh] backfilling item_facts…", flush=True)
                    from . import item_facts
                    item_facts.reindex_all_item_facts(con)
                    db.commit_retry(con)
                    _cache_bust()
                try:
                    n_ie = con.execute("SELECT COUNT(*) c FROM item_entity_facts").fetchone()["c"]
                except Exception:
                    n_ie = 0
                if n_pub and n_if and not n_ie:
                    print("[mesh] backfilling item_entity_facts…", flush=True)
                    from . import item_facts
                    item_facts.reindex_all_item_facts(con)
                    db.commit_retry(con)
                    _cache_bust()
                try:
                    con.execute("UPDATE users SET role='editor' WHERE role='dept'")
                    con.execute(
                        "UPDATE cards SET status='approved', reviewer='mesh-migrate', "
                        "reviewed_at=datetime('now') WHERE status IN ('pending','rejected')"
                    )
                    db.commit_retry(con)
                except Exception:
                    pass
                n_fts = con.execute("SELECT COUNT(*) c FROM search_fts").fetchone()["c"]
                n_iss = con.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"]
                need = db.search_needs_reindex(con) or (n_iss and not n_fts)
                if need:
                    print("[mesh] rebuilding search_fts…", flush=True)
                    db.reindex_all_search(con)
                    db.clear_search_reindex_flag(con)
                    db.commit_retry(con)
                    _cache_bust()
                    print("[mesh] search rebuild done", flush=True)
                try:
                    n_chunk = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
                except Exception:
                    n_chunk = 0
                if n_pub and n_if and (not n_chunk or n_chunk < n_if):
                    print("[mesh] backfilling chunk_index…", flush=True)
                    chunk_index.rebuild_all(con)
                    db.commit_retry(con)
                    n_chunk = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
                    _cache_bust()
                try:
                    n_emb = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
                except Exception:
                    n_emb = 0

            # embedding 异步入队，不阻塞启动与 Ask 落库
            if n_pub and n_chunk and n_emb < n_chunk and embeddings.is_configured():
                try:
                    pending = db.issues_needing_embedding(con)
                    with db.write_lock():
                        db.commit_retry(con)
                    for slug in pending:
                        embed_job.ensure_for_slug(slug, by="startup")
                    print(
                        f"[mesh] queued embed jobs for {len(pending)} issue(s) ({n_emb}/{n_chunk})",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[mesh] embed queue failed: {e}", flush=True)
            elif n_pub and n_chunk and n_emb < n_chunk:
                print(
                    f"[mesh] warn: chunk_embeddings={n_emb}/{n_chunk} — configure MESH_EMBED_* for vector search",
                    flush=True,
                )

            with db.write_lock():
                try:
                    presets.run_due_pushes(con)
                    db.commit_retry(con)
                except Exception:
                    pass
                try:
                    pruned = db.prune_old_logs(con)
                    if any(pruned.values()):
                        db.commit_retry(con)
                except Exception:
                    pass
        except Exception as e:
            print(f"[mesh] background maintenance failed: {e}", flush=True)
        finally:
            if lock_held:
                try:
                    db.release_maintenance_lock(con)
                except Exception:
                    pass
            con.close()

    import threading
    threading.Thread(target=_bg_maintenance, daemon=True, name="mesh-maint").start()


@app.get("/healthz")
def healthz():
    """运维探活 + 关键模块是否齐（不碰业务数据）。"""
    info = {
        "ok": True,
        "schema_version": db.SCHEMA_VERSION,
        "vector_enabled": embeddings.enabled(),
        "embeddings_configured": embeddings.is_configured(),
        "db_team_alias_map": hasattr(db, "team_alias_map") or hasattr(db, "_team_alias_map"),
        "db_normalize_team": hasattr(db, "normalize_team"),
        "qa_structured": hasattr(qa_structured, "parse_intent"),
        "search_fts": hasattr(search, "fts_search"),
        "tokenize": hasattr(tokenize, "build_match_query"),
        "ask_stream_llm": hasattr(llm, "answer_question_stream"),
        "job_store": True,
        "zone_hard": True,
        "job_inline": os.environ.get("MESH_JOB_INLINE", "1"),
        "ask_analysis": ask_analysis.analysis_enabled(),
    }
    try:
        con = db.connect()
        con.execute("SELECT 1").fetchone()
        try:
            info["entity_team_facts"] = con.execute(
                "SELECT COUNT(*) c FROM entity_team_facts"
            ).fetchone()["c"]
        except Exception:
            info["entity_team_facts"] = -1
        try:
            info["chunk_index_rows"] = con.execute(
                "SELECT COUNT(*) c FROM chunk_index"
            ).fetchone()["c"]
        except Exception:
            info["chunk_index_rows"] = -1
        try:
            info["chunk_embeddings"] = con.execute(
                "SELECT COUNT(*) c FROM chunk_embeddings"
            ).fetchone()["c"]
        except Exception:
            info["chunk_embeddings"] = -1
        try:
            info["item_entity_facts"] = con.execute(
                "SELECT COUNT(*) c FROM item_entity_facts"
            ).fetchone()["c"]
        except Exception:
            info["item_entity_facts"] = -1
        try:
            info["schema_version_db"] = db.get_setting(con, "schema_version") or ""
        except Exception:
            info["schema_version_db"] = ""
        try:
            info["item_facts_rows"] = con.execute(
                "SELECT COUNT(*) c FROM item_facts"
            ).fetchone()["c"]
        except Exception:
            info["item_facts_rows"] = -1
        ci = info.get("chunk_index_rows") or 0
        ce = info.get("chunk_embeddings") or 0
        if ci > 0 and ce < ci:
            info["embeddings_degraded"] = True
            info["embeddings_ratio"] = round(ce / ci, 3) if ci else 0
            if embeddings.is_configured():
                info["embeddings_hint"] = f"partial backfill {ce}/{ci}"
                err = embeddings.last_error()
                if err:
                    info["embeddings_last_error"] = err[:200]
            else:
                info["embeddings_hint"] = "configure MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL"
        try:
            rows = con.execute(
                """SELECT slug, embedding_status, embedding_done, embedding_total, embedding_model
                   FROM issues WHERE status='published'
                   AND embedding_status IN ('pending', 'running', 'partial', 'failed')
                   ORDER BY date_end DESC LIMIT 20"""
            ).fetchall()
            partial = []
            for r in rows:
                total = int(r["embedding_total"] or 0)
                done = int(r["embedding_done"] or 0)
                if total <= 0 and r["embedding_status"] not in ("failed",):
                    continue
                partial.append({
                    "slug": r["slug"],
                    "status": r["embedding_status"],
                    "done": done,
                    "total": total,
                    "model": r["embedding_model"] or "",
                    "label": f"{done}/{total}",
                })
            if partial:
                info["embedding_issues_partial"] = partial
        except Exception:
            pass
        try:
            info["search_fts_rows"] = con.execute(
                "SELECT COUNT(*) c FROM search_fts"
            ).fetchone()["c"]
        except Exception:
            info["search_fts_rows"] = -1
        con.close()
    except Exception as e:
        info["ok"] = False
        info["error"] = str(e)
        return JSONResponse(info, status_code=500)
    if not all(info[k] for k in (
        "db_team_alias_map", "db_normalize_team", "qa_structured", "search_fts", "tokenize", "ask_stream_llm"
    )):
        info["ok"] = False
        return JSONResponse(info, status_code=503)
    return info

def ensure_draft_for_edit(
    con, issue_row, *, sync_from_published: bool = False, force: bool = False
) -> str:
    """编辑只写草稿。无可用草稿时用线上稿铺底；force 时用线上稿覆盖草稿。"""
    pub = issue_row["published_json"] or ""
    draft = issue_row["draft_json"] or ""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if sync_from_published and pub.strip() and (force or not db.issue_json_renderable(draft)):
        con.execute(
            "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
            (pub, now, issue_row["id"]),
        )
        return pub
    if not draft.strip() or draft.strip() in ("{}", "null"):
        seed = pub if pub.strip() else "{}"
        con.execute(
            "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
            (seed, now, issue_row["id"]),
        )
        return seed
    return draft


def edit_json_column(_issue_row=None, _target=None) -> str:
    """页面编辑 / 增删卡片一律写 draft_json；只有确认上线才覆盖 published_json。"""
    return "draft_json"


def ctx(request: Request, **kw):
    u = auth.current_user(request)
    base = {
        "request": request,
        "user": u,
        "perms": auth.perms(u),
        "feishu_enabled": auth.feishu_enabled(),
        "base_url": BASE_URL,
        "flag_colors": FLAG_COLORS,
        "team_order": TEAM_ORDER,
    }
    base.update(kw)
    return base

def load_issue(con, slug: str, published_only=True):
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r: return None, None
    if published_only and r["status"] != "published": return r, None
    data = json.loads((r["published_json"] if published_only else (r["draft_json"] or r["published_json"])) or "{}")
    # 已发布页：信任 published_json 原样（与已发 EDM 一致）。
    # strong 切片只在 Publish 时由 build_published_projection 写入，这里不再二次过滤，
    # 否则无 decision_tier 的旧稿会被全部抹掉。
    return r, data


def load_issue_for_edm(con, slug: str):
    """EDM 预览/发信用：已上线期一律读 published_json，与读者页和正式邮件一致。"""
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        return None, None, "missing"
    row = dict(r)
    if row["status"] == "published":
        raw = row.get("published_json") or "{}"
        source = "published"
    else:
        raw = row.get("draft_json") or row.get("published_json") or "{}"
        source = "draft" if row.get("draft_json") else ("published" if row.get("published_json") else "empty")
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
        source = "invalid"
    # 同上：已上线稿不做 is_reader_tier 二次过滤
    return row, data, source


def edm_draft_out_of_sync(row: dict) -> bool:
    """已上线期：草稿与上线稿不一致（预览/测试若读草稿会与正式邮件不同）。"""
    if not row or row.get("status") != "published":
        return False
    d, p = row.get("draft_json"), row.get("published_json")
    if not d or not p:
        return False
    return d != p

def _clear_preview_gate(data: dict) -> dict:
    from .preview_gate_state import clear_preview_gate
    return clear_preview_gate(data)


def _edit_invalidates_preview_gate(path: str) -> bool:
    from .preview_gate_state import edit_invalidates_preview_gate
    return edit_invalidates_preview_gate(path)


def publish_blockers(con, issue_id: int, draft_json: str) -> list[str]:
    """与后台第四步检查清单一致的服务端拦截项。"""
    errs = []
    if not con.execute("SELECT 1 FROM sources WHERE issue_id=? LIMIT 1", (issue_id,)).fetchone():
        errs.append("尚未放入材料")
    if not (con.execute("SELECT COUNT(*) AS n FROM items WHERE issue_id=?", (issue_id,)).fetchone()["n"] or 0):
        errs.append("尚未完成挖掘与脱敏（无条目）")
    cards = [dict(x) for x in con.execute("SELECT status FROM cards WHERE issue_id=?", (issue_id,))]
    if not cards:
        errs.append("尚未生成团队要点卡（请点「生成预览」或「生成各团队要点卡」）")
    n_noowner = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=? AND (owner_team IS NULL OR owner_team='')", (issue_id,)).fetchone()["c"]
    if n_noowner:
        errs.append(f"还有 {n_noowner} 条条目待指定归属")
    from .aggregator import INVALID_OWNER_TEAMS
    bad_owner = tuple(INVALID_OWNER_TEAMS)
    n_bad = con.execute(
        f"SELECT COUNT(*) c FROM items WHERE issue_id=? AND owner_team IN ({','.join('?' * len(bad_owner))})",
        (issue_id, *bad_owner),
    ).fetchone()["c"]
    if n_bad:
        errs.append(f"还有 {n_bad} 条条目归属无效（不能为「内容中心·数据聚合」或「其他」）")
    if not db.draft_is_ready(draft_json):
        errs.append("请先重新生成周报草稿（挖掘或卡片变更后旧草稿已失效）")
    # 仅内容聚合包：拆段低置信须人工确认（单团队来源选了谁就是谁，不拦）
    try:
        from .ingest import is_aggregation_source

        n_review = 0
        for row in con.execute(
            "SELECT stype, team, channel, meta FROM sources WHERE issue_id=?",
            (issue_id,),
        ):
            if not is_aggregation_source(
                stype=row["stype"] or "",
                team=row["team"] or "",
                channel=row["channel"] or "",
            ):
                continue
            try:
                m = json.loads(row["meta"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if (m.get("split") or {}).get("needs_review"):
                n_review += 1
        if n_review:
            errs.append(
                f"有 {n_review} 个内容聚合来源拆段置信度低，请到来源页点「确认拆段归属」"
                "或拆成单部门文件重传"
            )
    except Exception:
        pass
    try:
        # 关系论证不足：生成预览时会滤掉该卡，不作为整期硬拦
        from .attribution_verify import scan_issue

        for b in scan_issue(con, issue_id, draft_json).blockers:
            if isinstance(b, str) and b.startswith("关系「"):
                continue
            errs.append(b)
        draft_obj = {}
        try:
            draft_obj = json.loads(draft_json) if draft_json else {}
        except (json.JSONDecodeError, TypeError):
            draft_obj = {}
        review_titles = [
            (r.get("title") or "（无标题）")
            for r in (draft_obj.get("relations") or [])
            if isinstance(r, dict) and r.get("needs_review")
        ]
        if review_titles and not any("叙事待核对" in e for e in errs):
            # 有 evidence 的卡已人工看过稿面，不再因 needs_review 硬拦
            pass
    except Exception as e:
        errs.append(f"关系闸门检查失败：{e}")
    return errs

# ---------------- 公开页 ----------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    con = db.connect()
    r = con.execute("SELECT slug FROM issues WHERE status='published' ORDER BY date_end DESC LIMIT 1").fetchone()
    con.close()
    if not r: return RedirectResponse("/admin")
    return RedirectResponse(f"/{r['slug']}")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", debug: int = 0):
    target = auth.normalize_next(next)
    if auth.current_user(request):
        return RedirectResponse(target, status_code=302)
    preset = (request.cookies.get(auth.DEBUG_PRESET_COOKIE) or "").strip()
    if preset not in auth.ROLE_RANK:
        preset = ""
    return templates.TemplateResponse(
        "login.html",
        ctx(request, next=target, error=None, debug_panel=bool(debug), debug_preset=preset),
    )

@app.post("/login/debug-preset")
async def login_debug_preset(request: Request):
    """登录前预选调试视角（飞书 callback 时写入 session，仅对白名单用户生效）。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    role = (payload.get("role") or "").strip()
    if role and role not in auth.ROLE_RANK:
        raise HTTPException(400, "未知角色")
    resp = JSONResponse({"ok": True, "role": role})
    if role:
        resp.set_cookie(
            auth.DEBUG_PRESET_COOKIE, role, httponly=True, samesite="lax",
            secure=_cookie_secure(request), max_age=3600,
        )
    else:
        resp.delete_cookie(auth.DEBUG_PRESET_COOKIE, path="/", samesite="lax", secure=_cookie_secure(request))
    return resp

@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    """密码登录已关闭；保留接口供紧急运维（需 MESH_ALLOW_PASSWORD_LOGIN=1）。"""
    if os.environ.get("MESH_ALLOW_PASSWORD_LOGIN", "").strip() not in ("1", "true", "yes"):
        raise HTTPException(403, "请使用飞书登录")
    target = auth.normalize_next(next)
    if auth.current_user(request):
        return RedirectResponse(target, status_code=302)
    u = auth.local_login(username, password)
    if not u: return templates.TemplateResponse("login.html", ctx(request, next=target, error="账号或密码不对"))
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(auth.COOKIE, auth.make_session(u), httponly=True, samesite="lax",
                    secure=_cookie_secure(request), max_age=auth.SESSION_MAX_AGE)
    return resp

@app.get("/logout")
def logout(request: Request):
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(auth.COOKIE, path="/", samesite="lax", secure=_cookie_secure(request))
    resp.delete_cookie(auth.DEBUG_PRESET_COOKIE, path="/", samesite="lax", secure=_cookie_secure(request))
    return resp

@app.get("/auth/feishu")
def feishu_start(request: Request, next: str = "/"):
    if not auth.feishu_enabled(): raise HTTPException(400, "未配置飞书应用")
    target = auth.normalize_next(next)
    if auth.current_user(request):
        return RedirectResponse(target, status_code=302)
    return RedirectResponse(auth.feishu_authorize_url(target))

@app.get("/auth/feishu/callback")
def feishu_cb(request: Request, code: str = "", state: str = ""):
    target = "/"
    try:
        target = auth.read_feishu_state(state)
        info = auth.feishu_exchange(code); u = auth.upsert_feishu_user(info); u["avatar_url"] = info.get("avatar_url", "")
        preset = (request.cookies.get(auth.DEBUG_PRESET_COOKIE) or "").strip()
        debug_role = preset if auth.is_debug_user(u.get("display") or info.get("name") or "", u.get("username") or "") else ""
    except auth.FeishuLoginError as e:
        return templates.TemplateResponse("login.html", ctx(request, next=target, error=e.user_message))
    except Exception:
        return templates.TemplateResponse("login.html", ctx(request, next=target, error="飞书登录暂时不可用，请稍后重试。"))
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(auth.COOKIE, auth.make_session(u, debug_role=debug_role), httponly=True, samesite="lax",
                    secure=_cookie_secure(request), max_age=auth.SESSION_MAX_AGE)
    resp.delete_cookie(auth.DEBUG_PRESET_COOKIE, path="/", samesite="lax", secure=_cookie_secure(request))
    return resp

@app.post("/api/debug/role")
async def api_debug_role(request: Request):
    """调试：切换当前会话视角（仅白名单用户）。"""
    u = auth.current_user(request)
    if not u:
        raise HTTPException(401, "未登录")
    if not auth.is_debug_user(u.get("d") or "", u.get("u") or ""):
        raise HTTPException(403, "无调试权限")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    role = (payload.get("role") or "").strip()
    if role and role not in auth.ROLE_RANK:
        raise HTTPException(400, "未知角色")
    con = db.connect()
    row = con.execute(
        "SELECT username, role, team, display, avatar_url FROM users WHERE username=?",
        (u.get("u"),),
    ).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "用户不存在")
    real = dict(row)
    resp = JSONResponse({
        "ok": True,
        "role": role or real["role"],
        "real_role": real["role"],
        "debug": bool(role),
    })
    resp.set_cookie(
        auth.COOKIE,
        auth.make_session({
            "username": real["username"],
            "role": real["role"],
            "team": real["team"],
            "display": real["display"],
            "avatar_url": real.get("avatar_url") or u.get("a"),
        }, debug_role=role),
        httponly=True, samesite="lax",
        secure=_cookie_secure(request), max_age=auth.SESSION_MAX_AGE,
    )
    return resp

@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request):
    auth.require(request, "viewer")
    con = db.connect()
    rows = con.execute("SELECT slug, period_label, date_start, date_end, published_at FROM issues WHERE status='published' ORDER BY date_end DESC").fetchall()
    con.close()
    return templates.TemplateResponse("archive.html", ctx(request, issues=[_enrich_issue(r) for r in rows]))

@app.get("/api/search")
def api_search(request: Request, q: str = "", slug: str = ""):
    auth.require(request, "viewer")
    q = (q or "").strip()
    if not q:
        return {"hits": []}
    con = db.connect()
    try:
        cookie = auth.current_user(request)
        user = auth.ask_user(con, cookie) if cookie else None
        scope = ask_scope.resolve(con, user, {"slug": slug})
        search_q = ask_query.retrieval_query(q, [])
        _, rows, _ = retriever.retrieve(con, search_q, scope)
    finally:
        con.close()
    for h in rows:
        h.pop("body", None)
    return {"hits": rows}


def _cache_get(key: str):
    with _ASK_CACHE_LOCK:
        return _ASK_CACHE.get(key)


def _run_ask(request: Request, payload: dict):
    """统一问答：会话隔离 + 多路检索 + 多轮历史。"""
    cookie = auth.require(request, "viewer")
    rate_key = cookie.get("u") or (request.client.host if request.client else "anon")
    if not _ask_rate_ok(rate_key):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    q = (payload.get("q") or "").strip()
    if not q:
        return {"answer": "", "mode": "lexical", "n_context": 0, "session_id": ""}

    con = db.connect()
    try:
        user = auth.ask_user(con, cookie)
        if not user:
            raise HTTPException(401, "未登录")
        scope = ask_scope.resolve(con, user, payload)
        turn = ask_turn.begin_turn(
            con, user=user, scope=scope, q=q,
            cache_get=_cache_get, cache_put=_ask_cache_put,
        )
        if turn.get("cached"):
            return turn["resp"]
        sess = turn["sess"]
        history = turn["history"]
        cache_key = turn["cache_key"]
        use_cache = turn["use_cache"]
        context_route = turn.get("context_route") or {}
    finally:
        con.close()

    con = db.connect()
    try:
        prepared = ask_turn.prepare_turn(con, q, scope, history, context_route=context_route)
        prepared["context_route"] = context_route
    finally:
        con.close()

    try:
        if prepared.get("direct_answer") is not None:
            ans = prepared["direct_answer"]
        elif ask_analysis.analysis_enabled(payload):
            ans, steps, meta = ask_analysis.run_analysis_collect(
                q, prepared, history,
                persist={"session_id": sess["id"], "user_id": user.get("id")},
            )
            prepared = dict(prepared)
            prepared["analysis_steps"] = [
                {k: v for k, v in s.items() if k != "type"} for s in steps
            ]
            prepared["mode"] = (prepared.get("mode") or "lexical") + "+analysis"
            prepared["report_id"] = meta.get("report_id")
            prepared["analysis_id"] = meta.get("analysis_id") or meta.get("report_id")
            prepared["usage"] = meta.get("usage")
            prepared["verify"] = meta.get("verify")
            prepared["context_refs"] = meta.get("context_refs")
            prepared["sources"] = meta.get("sources")
            prepared["cross"] = meta.get("cross")
        else:
            with ask_concurrency.llm_slot():
                ans = ask_turn.generate_answer(q, prepared, history)
    except ask_concurrency.AskBusyError as e:
        return {k: v for k, v in prepared.items() if k not in ("contexts", "direct_answer", "preview")} | {
            "answer": str(e),
            "session_id": sess["id"],
        }
    except ask_concurrency.JobBusyError as e:
        return {k: v for k, v in prepared.items() if k not in ("contexts", "direct_answer", "preview")} | {
            "answer": str(e),
            "session_id": sess["id"],
        }
    except Exception:
        return {k: v for k, v in prepared.items() if k not in ("contexts", "direct_answer", "preview")} | {
            "answer": "AI 问答暂不可用，请稍后重试。",
            "session_id": sess["id"],
        }

    con = db.connect()
    try:
        return ask_turn.complete_turn(
            con, user=user, scope=scope, q=q,
            sess=sess, prepared=prepared, ans=ans,
            cache_key=cache_key, use_cache=use_cache,
            cache_put=_ask_cache_put,
        )
    finally:
        con.close()


@app.post("/api/ask")
def api_ask(request: Request, payload: dict):
    return _run_ask(request, payload)


@app.post("/api/ask/new_session")
def api_ask_new_session(request: Request):
    """开启新对话：客户端保存返回的 session_id 到 localStorage。"""
    auth.require(request, "viewer")
    import secrets
    return {"session_id": secrets.token_hex(8)}


@app.get("/api/ask/analysis/{analysis_id}")
def api_ask_analysis_get(request: Request, analysis_id: str):
    """按 analysis_id 读取已持久化的分析结果（SSE 断线后可回放）。"""
    auth.require(request, "viewer")
    from . import ask_store
    con = db.connect()
    try:
        row = ask_store.get(con, analysis_id)
    finally:
        con.close()
    if not row:
        raise HTTPException(404, "分析不存在")
    return row


@app.get("/api/ask/sessions")
def api_ask_sessions(request: Request):
    cookie = auth.require(request, "viewer")
    con = db.connect()
    user = auth.ask_user(con, cookie)
    if not user:
        con.close()
        raise HTTPException(401)
    rows = conversation.list_sessions(con, user["id"])
    con.close()
    return {"sessions": rows}


@app.get("/api/ask/presets")
def api_ask_presets_list(request: Request):
    cookie = auth.require(request, "viewer")
    con = db.connect()
    user = auth.ask_user(con, cookie)
    if not user:
        con.close()
        raise HTTPException(401)
    rows = presets.list_for_user(con, user["id"], user.get("team") or "")
    con.close()
    return {"presets": rows}


@app.post("/api/ask/presets")
def api_ask_presets_create(request: Request, payload: dict):
    cookie = auth.require(request, "viewer")
    con = db.connect()
    user = auth.ask_user(con, cookie)
    if not user:
        con.close()
        raise HTTPException(401)
    title = (payload.get("title") or "").strip()
    question = (payload.get("question") or payload.get("question_template") or "").strip()
    if not title or not question:
        con.close()
        raise HTTPException(400, "需要 title 与 question")
    scope = (payload.get("scope") or "user").strip()
    if scope == "company" and user.get("role") not in ("admin", "owner"):
        con.close()
        raise HTTPException(403, "全公司预设需管理员")
    if scope == "team" and user.get("role") not in ("admin", "owner", "editor"):
        con.close()
        raise HTTPException(403, "团队预设需编辑及以上")
    pid = presets.create_preset(
        con,
        title=title,
        question_template=question,
        scope=scope,
        user_id=user["id"] if scope == "user" else payload.get("user_id"),
        target_team=(payload.get("target_team") or user.get("team") or "").strip(),
        schedule=(payload.get("schedule") or "manual").strip(),
        schedule_time=(payload.get("schedule_time") or "09:00").strip(),
        schedule_dow=payload.get("schedule_dow"),
        team_scope=(payload.get("team_scope") or "").strip(),
        created_by=user["id"],
    )
    con.commit()
    con.close()
    return {"ok": True, "id": pid}


def _can_run_preset(user: dict, pd: dict) -> bool:
    if user.get("role") in ("admin", "owner"):
        return True
    sc = pd.get("scope") or "user"
    if sc == "user":
        return pd.get("user_id") == user.get("id")
    if sc == "team":
        return (user.get("team") or "") == (pd.get("target_team") or "")
    return sc != "company"


@app.post("/api/ask/presets/{preset_id}/run")
def api_ask_preset_run(request: Request, preset_id: int):
    cookie = auth.require(request, "viewer")
    rate_key = cookie.get("u") or (request.client.host if request.client else "anon")
    if not _ask_rate_ok(rate_key):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    con = db.connect()
    user = auth.ask_user(con, cookie)
    if not user:
        con.close()
        raise HTTPException(401)
    row = con.execute("SELECT * FROM user_ask_presets WHERE id=?", (preset_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404)
    pd = dict(row)
    if not _can_run_preset(user, pd):
        con.close()
        raise HTTPException(403)
    prepared = presets.run_preset(con, pd, user)
    q = presets.render_question(pd, user)
    scope = ask_scope.resolve(con, user, {
        "session_id": f"preset-{preset_id}-u{user['id']}",
        "team": pd.get("team_scope") or user.get("team") or "",
    })
    sess = conversation.ensure_session(con, scope, user)
    with db.write_lock():
        db.commit_retry(con)
    con.close()
    try:
        with ask_concurrency.llm_slot():
            # 预设问答：禁止会话 Answer 作为成文上下文
            ans = llm.answer_question(q, prepared["contexts"], mode=prepared["mode"], history=None)
    except ask_concurrency.AskBusyError as e:
        return {"answer": str(e), "question": q}
    except Exception:
        return {"answer": "AI 问答暂不可用", "question": q}
    con = db.connect()
    try:
        conversation.append_turn(
            con, sess["id"], user_text=q, assistant_text=ans, mode=prepared.get("mode", ""),
        )
        with db.write_lock():
            db.commit_retry(con)
    finally:
        con.close()
    return {"question": q, "answer": ans, "mode": prepared.get("mode"), "n_context": prepared.get("n_context", 0)}


@app.delete("/api/ask/presets/{preset_id}")
def api_ask_presets_delete(request: Request, preset_id: int):
    cookie = auth.require(request, "viewer")
    con = db.connect()
    user = auth.ask_user(con, cookie)
    if not user:
        con.close()
        raise HTTPException(401)
    row = con.execute("SELECT * FROM user_ask_presets WHERE id=?", (preset_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404)
    pd = dict(row)
    if pd.get("user_id") != user["id"] and user.get("role") not in ("admin", "owner"):
        con.close()
        raise HTTPException(403)
    presets.delete_preset(con, preset_id)
    con.commit()
    con.close()
    return {"ok": True}


def _ask_cache_put(key: str, resp: dict):
    if _ASK_CACHE_TTL <= 0:
        return
    with _ASK_CACHE_LOCK:
        if len(_ASK_CACHE) >= _ASK_CACHE_MAX:
            cutoff = time.time()
            stale = [k for k, (exp, _) in _ASK_CACHE.items() if exp <= cutoff]
            for k in stale[:64] or list(_ASK_CACHE.keys())[:64]:
                _ASK_CACHE.pop(k, None)
        _ASK_CACHE[key] = (time.time() + _ASK_CACHE_TTL, resp)


@app.post("/api/ask/stream")
def api_ask_stream(request: Request, payload: dict):
    """SSE 流式问答：先 meta，再 token，最后 done。"""
    cookie = auth.require(request, "viewer")
    rate_key = cookie.get("u") or (request.client.host if request.client else "anon")
    if not _ask_rate_ok(rate_key):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    q = (payload.get("q") or "").strip()

    def sse(obj: dict) -> str:
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

    def gen():
        yield sse({"type": "status", "message": "检索中"})
        if not q:
            yield sse({"type": "meta", "mode": "lexical", "n_context": 0})
            yield sse({"type": "done", "answer": ""})
            return

        cookie = auth.current_user(request)
        if not cookie:
            yield sse({"type": "error", "message": "未登录"})
            return

        con = db.connect()
        user = scope = prepared = sess = history = None
        cache_key = ""
        use_cache = False
        try:
            user = auth.ask_user(con, cookie)
            if not user:
                yield sse({"type": "error", "message": "未登录"})
                return
            scope = ask_scope.resolve(con, user, payload)
            turn = ask_turn.begin_turn(
                con, user=user, scope=scope, q=q,
                cache_get=_cache_get, cache_put=_ask_cache_put,
            )
            if turn.get("cached"):
                resp = turn["resp"]
                yield sse({"type": "meta", "mode": resp.get("mode", "lexical"),
                           "n_context": resp.get("n_context", 0),
                           "total": resp.get("total"), "query": resp.get("query"),
                           "date_from": resp.get("date_from"),
                           "date_to": resp.get("date_to"), "cached": True,
                           "session_id": resp.get("session_id")})
                ans = resp.get("answer") or ""
                if ans:
                    yield sse({"type": "token", "text": ans})
                yield sse({"type": "done", "answer": ans})
                return

            sess = turn["sess"]
            history = turn["history"]
            cache_key = turn["cache_key"]
            use_cache = turn["use_cache"]
            context_route = turn.get("context_route") or {}
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return
        finally:
            con.close()

        con = db.connect()
        try:
            prepared = ask_turn.prepare_turn(con, q, scope, history, context_route=context_route)
            prepared["context_route"] = context_route
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return
        finally:
            con.close()

        meta = {k: v for k, v in prepared.items() if k not in ("contexts", "direct_answer", "preview")}
        meta["type"] = "meta"
        meta["session_id"] = sess["id"]
        if context_route:
            meta["context_route"] = {
                "kind": context_route.get("kind"),
                "reason": context_route.get("reason"),
                "parent_analysis_id": context_route.get("parent_analysis_id"),
            }
        yield sse(meta)

        parts: list[str] = []
        ans = ""
        analysis_done = False
        try:
            if prepared.get("direct_answer") is not None:
                ans = prepared["direct_answer"]
                analysis_done = True
                if ans:
                    parts.append(ans)
                    yield sse({"type": "token", "text": ans})
            elif ask_analysis.analysis_enabled(payload):
                yield sse({"type": "status", "message": "多源交叉分析中"})
                ans, steps, meta = ask_analysis.run_analysis_collect(
                    q, prepared, history=history,
                    persist={"session_id": sess["id"], "user_id": user.get("id")},
                )
                analysis_done = True
                for ev in steps:
                    yield sse(ev)
                if ans:
                    chunk_n = 48
                    for i in range(0, len(ans), chunk_n):
                        piece = ans[i : i + chunk_n]
                        parts.append(piece)
                        yield sse({"type": "token", "text": piece})
                prepared = dict(prepared)
                prepared["_analysis_meta"] = {
                    "report_id": meta.get("report_id"),
                    "analysis_id": meta.get("analysis_id") or meta.get("report_id"),
                    "usage": meta.get("usage"),
                    "verify": meta.get("verify"),
                    "sources": meta.get("sources"),
                }
                prepared["analysis_id"] = meta.get("analysis_id") or meta.get("report_id")
                prepared["report_id"] = prepared["analysis_id"]
                prepared["context_refs"] = meta.get("context_refs")
                prepared["sources"] = meta.get("sources")
                prepared["cross"] = meta.get("cross")
                prepared["usage"] = meta.get("usage")
                prepared["verify"] = meta.get("verify")
            else:
                yield sse({"type": "status", "message": "生成并校验中"})
                with ask_concurrency.llm_slot():
                    ans = ask_turn.generate_answer(q, prepared, history)
                analysis_done = True
                if ans:
                    parts.append(ans)
                    yield sse({"type": "token", "text": ans})
        except ask_concurrency.AskBusyError as e:
            yield sse({"type": "error", "message": str(e)})
            return
        except ask_concurrency.JobBusyError as e:
            yield sse({"type": "error", "message": str(e)})
            return
        except Exception:
            yield sse({"type": "error", "message": "AI 问答暂不可用，请稍后重试。"})
            return
        finally:
            if analysis_done and sess and user and scope and prepared is not None:
                ans_fin = "".join(parts) if parts else (prepared.get("direct_answer") or ans or "")
                if ans_fin or prepared.get("analysis_id"):
                    con_fin = db.connect()
                    try:
                        ask_turn.complete_turn(
                            con_fin, user=user, scope=scope, q=q,
                            sess=sess, prepared=prepared, ans=ans_fin,
                            cache_key=cache_key, use_cache=use_cache,
                            cache_put=_ask_cache_put,
                        )
                    except Exception:
                        pass
                    finally:
                        con_fin.close()

        ans = "".join(parts) if parts else (prepared.get("direct_answer") or "")
        done_ev: dict = {"type": "done", "answer": ans}
        am = prepared.get("_analysis_meta") if isinstance(prepared, dict) else None
        if isinstance(am, dict):
            if am.get("report_id"):
                done_ev["report_id"] = am["report_id"]
            if am.get("analysis_id"):
                done_ev["analysis_id"] = am["analysis_id"]
            if am.get("usage") is not None:
                done_ev["usage"] = am["usage"]
            if am.get("verify") is not None:
                done_ev["verify"] = am["verify"]
        yield sse(done_ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/admin/reindex_facts")
def admin_reindex_facts(request: Request):
    """回填/重建全部已发布期的主体×团队事实表 + 搜索/chunk 索引。"""
    auth.require(request, "owner")
    con = db.connect()
    n = db.reindex_all_entity_facts(con)
    db.reindex_all_search(con)
    c = con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"]
    chunks = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
    if embeddings.is_configured():
        for r in con.execute("SELECT id FROM issues WHERE status='published'"):
            db.refresh_issue_embedding_status(con, r["id"], status="pending")
    con.commit()
    queued = 0
    if embeddings.is_configured():
        try:
            embed_job.start(force=False)
            queued = 1
        except Exception:
            pass
    try:
        embs = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
    except Exception:
        embs = -1
    con.close()
    _ASK_CACHE.clear()
    out = {"ok": True, "issues": n, "facts": c, "chunks": chunks, "embeddings": embs,
            "embed_configured": embeddings.is_configured(), "embed_queued": queued}
    return out


@app.post("/admin/reindex_search")
def admin_reindex_search(request: Request):
    """全量重建 FTS（中文 toks）+ 事实表。"""
    auth.require(request, "owner")
    con = db.connect()
    n = db.reindex_all_search(con)
    fts_n = con.execute("SELECT COUNT(*) c FROM search_fts").fetchone()["c"]
    fact_n = con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"]
    try:
        if_n = con.execute("SELECT COUNT(*) c FROM item_facts").fetchone()["c"]
    except Exception:
        if_n = -1
    try:
        ie_n = con.execute("SELECT COUNT(*) c FROM item_entity_facts").fetchone()["c"]
    except Exception:
        ie_n = -1
    con.commit(); con.close()
    _ASK_CACHE.clear()
    return {"ok": True, "issues": n, "fts_rows": fts_n, "facts": fact_n, "item_facts": if_n,
            "item_entities": ie_n, "schema_version": db.SCHEMA_VERSION}


@app.post("/admin/issue/{slug}/reindex_published")
def admin_reindex_published(request: Request, slug: str):
    """已上线期：重抽条目后，从当前 items + published_json 重建搜索/问答索引。"""
    auth.require(request, "editor")
    con = db.connect()
    r = con.execute("SELECT id, status FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    if r["status"] != "published":
        con.close()
        return _flash_redirect(f"/admin/issue/{slug}?step=4", "仅已上线期可重建搜索索引。")
    db.snapshot_published_items(con, r["id"])
    db.reindex_issue(con, r["id"])
    con.commit()
    con.close()
    _ASK_CACHE.clear()
    if embeddings.is_configured():
        embed_job.ensure_for_slug(slug, by="reindex")
    return _flash_redirect(f"/admin/issue/{slug}?step=4", "已重建搜索/问答索引（含条目与实体）；向量在后台补齐。")

@app.post("/admin/embed_backfill")
def admin_embed_backfill(request: Request):
    """异步回填 chunk 向量（状态见 /admin/embed_backfill/status）。"""
    auth.require(request, "owner")
    from . import embed_job

    if not embeddings.is_configured():
        raise HTTPException(400, "未配置 MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL")
    try:
        st = embed_job.start(force=False)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "async": True, **{k: st.get(k) for k in (
        "running", "done", "message", "added", "error",
    )}}


@app.get("/admin/embed_backfill/status")
def admin_embed_backfill_status(request: Request):
    auth.require(request, "owner")
    from . import embed_job

    st = embed_job.get_state()
    return {"ok": True, **{k: st.get(k) for k in (
        "running", "done", "error", "message", "added",
        "embeddings_before", "embeddings_after",
    )}}


@app.get("/admin/edm/{slug}/status")
def admin_edm_job_status(request: Request, slug: str):
    auth.require(request, "owner")
    return {"ok": True, **edm_job.get_state(slug)}


@app.post("/admin/source/{sid}/confirm_split")
def source_confirm_split(request: Request, sid: int):
    """核对拆段归属后清除 needs_review，允许上线。"""
    auth.require(request, "editor")
    con = db.connect()
    s = con.execute("SELECT id, meta FROM sources WHERE id=?", (sid,)).fetchone()
    if not s:
        con.close()
        raise HTTPException(404, "素材不存在")
    try:
        meta = json.loads(s["meta"] or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    sp = dict(meta.get("split") or {})
    sp["needs_review"] = False
    sp["reviewed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    meta["split"] = sp
    con.execute("UPDATE sources SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), sid))
    con.commit()
    con.close()
    wants_json = "application/json" in (request.headers.get("accept") or "")
    if wants_json:
        return {"ok": True, "id": sid}
    return RedirectResponse(f"/admin/source/{sid}", status_code=302)


@app.get("/admin/issue/{slug}/weak_relations")
def issue_weak_relations(request: Request, slug: str):
    auth.require(request, "editor")
    con = db.connect()
    r = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    try:
        draft = json.loads(r["draft_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        draft = {}
    weak = []
    for i, rel in enumerate(draft.get("relations") or []):
        if isinstance(rel, dict) and rel.get("weak"):
            weak.append({
                "index": i,
                "title": rel.get("title") or "",
                "label": rel.get("label") or "",
                "body": (rel.get("body") or "")[:200],
                "teams": rel.get("teams") or [],
            })
    con.close()
    return {"ok": True, "weak": weak}


@app.post("/admin/issue/{slug}/weak_relations/resolve")
def issue_weak_relations_resolve(request: Request, slug: str, payload: dict):
    """action=confirm 取消 weak；action=delete 删除该关系卡。indexes: [int]。"""
    u = auth.require(request, "editor")
    action = (payload.get("action") or "").strip()
    indexes = payload.get("indexes") or []
    if action not in ("confirm", "delete") or not isinstance(indexes, list):
        raise HTTPException(400, "需要 action=confirm|delete 与 indexes")
    idxs = sorted({int(x) for x in indexes}, reverse=True)
    con = db.connect()
    r = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    try:
        draft = json.loads(r["draft_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, "草稿 JSON 无效")
    rels = list(draft.get("relations") or [])
    changed = 0
    for i in idxs:
        if i < 0 or i >= len(rels):
            continue
        if action == "delete":
            rels.pop(i)
            changed += 1
        elif isinstance(rels[i], dict) and (rels[i].get("needs_review") or rels[i].get("weak")):
            rels[i] = dict(rels[i])
            rels[i]["needs_review"] = False
            if action == "confirm" and rels[i].get("weak"):
                pass  # 虚线卡保留 weak，仅清除核对标记
            rels[i]["status"] = "weak" if rels[i].get("weak") else "confirmed"
            changed += 1
    draft["relations"] = rels
    from .issue_verify import sync_kpis_from_data
    draft = sync_kpis_from_data(draft)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    from .relation_display import attach_reader_flags, build_published_projection, split_relations_for_publish
    draft["relations"] = attach_reader_flags(
        [r for r in (draft.get("relations") or []) if isinstance(r, dict)]
    )
    reader, backlog = split_relations_for_publish(draft["relations"])
    draft["_relations_reader"] = reader
    draft["_relations_backlog"] = backlog
    draft_payload = json.dumps(draft, ensure_ascii=False)
    # 已上线期：published 只写投影；草稿期可与 draft 同写（仍投影，避免误漏）
    pub_payload = json.dumps(build_published_projection(draft), ensure_ascii=False)
    con.execute(
        "UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?",
        (draft_payload, pub_payload, stamp, r["id"]),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (r["id"], u.get("u") or "", "weak_relations", action, f"{changed} cards"),
    )
    con.commit()
    con.close()
    return {"ok": True, "changed": changed, "action": action}

# ---------------- 后台 ----------------
# ---------------- 后台：期管理 ----------------

def _parse_iso_date(s: str | None) -> datetime.date | None:
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def format_display_date(d: datetime.date) -> str:
    """读者页单日期：2026.8.27"""
    return f"{d.year}.{d.month}.{d.day}"


def issue_display_date(issue: dict) -> str:
    """已上线用 published_at；草稿用 date_end（创建默认当天）。"""
    if issue.get("published_at"):
        d = _parse_iso_date(str(issue["published_at"])[:10])
        if d:
            return format_display_date(d)
    d = _parse_iso_date(issue.get("date_end"))
    if d:
        return format_display_date(d)
    return (issue.get("period_label") or issue.get("slug") or "").strip()


def _enrich_issue(row) -> dict:
    d = dict(row)
    d["display_date"] = issue_display_date(d)
    d["embedding_label"] = _embedding_progress_label(d)
    return d


def _embedding_progress_label(issue: dict) -> str:
    if issue.get("status") != "published":
        return ""
    st = (issue.get("embedding_status") or "").strip()
    total = int(issue.get("embedding_total") or 0)
    done = int(issue.get("embedding_done") or 0)
    if st == "skipped":
        return "未配置"
    if st == "completed" or (total > 0 and done >= total):
        return f"{done}/{total}" if total else "完成"
    if st == "running":
        return f"补齐中 {done}/{total}"
    if st == "pending":
        return f"待补齐 {done}/{total}" if total else "待补齐"
    if st == "partial":
        return f"部分 {done}/{total}"
    if st == "failed":
        return f"失败 {done}/{total}"
    if total:
        return f"{done}/{total}"
    return ""


def _period_label(start: datetime.date, end: datetime.date) -> str:
    """兼容旧调用：展示结束日单日期。"""
    return format_display_date(end)


def suggest_next_issue(con) -> dict:
    """新建下一期：默认发布日期为今天，无需手填区间。"""
    row = con.execute(
        "SELECT version FROM issues ORDER BY date_end DESC, id DESC LIMIT 1"
    ).fetchone()
    version = ((row["version"] if row else None) or "v1.4").strip() or "v1.4"
    today = datetime.date.today()
    ds = today.isoformat()
    return {
        "slug": ds,
        "date_start": ds,
        "date_end": ds,
        "period_label": format_display_date(today),
        "version": version,
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    """管理后台入口：先看各期列表，再选一期进入制作。"""
    auth.require(request, "editor")
    return RedirectResponse("/admin/issues", status_code=302)


@app.get("/admin/eval", response_class=HTMLResponse)
def admin_eval_dashboard(request: Request, current: str = "", baseline: str = "", role: str = ""):
    """Eval Dashboard V1 — 只读 eval/reports，不触发 Preview/Ask/Embed/Publish。"""
    auth.require(request, "editor")
    from .eval_dashboard import build_view

    view = build_view(
        current_name=current or None,
        baseline_name=baseline or None,
        role=role or None,
    )
    return templates.TemplateResponse(
        "eval_dashboard.html",
        ctx(request, nav="eval", view=view),
    )

@app.get("/admin/issues", response_class=HTMLResponse)
def admin_issue_list(request: Request, err: str = ""):
    u = auth.require(request, "editor")
    con = db.connect()
    issues = [_enrich_issue(r) for r in con.execute(
        """SELECT id, slug, period_label, status, updated_at, published_at, date_end, draft_json,
                  embedding_status, embedding_total, embedding_done, embedding_model
           FROM issues ORDER BY date_end DESC"""
    )]
    for i in issues:
        draft = i.pop("draft_json", None)
        i["has_preview"] = db.issue_json_renderable(draft)
        if i["status"] != "published":
            blockers = publish_blockers(con, i["id"], draft or "")
            i["can_go_live"] = len(blockers) == 0
            i["publish_hint"] = "；".join(blockers) if blockers else ""
        else:
            i["can_go_live"] = False
            i["publish_hint"] = ""
    suggest = suggest_next_issue(con)
    con.close()
    return templates.TemplateResponse(
        "admin.html",
        ctx(request, issues=issues, suggest=suggest, stypes=ingest.SOURCE_TYPES, teams=ingest.TEAMS, flash_err=err or None),
    )

@app.post("/admin/issue/new")
def issue_new(request: Request, slug: str = Form(...), date_start: str = Form(""), date_end: str = Form(""), period_label: str = Form(""), version: str = Form("v1.4")):
    auth.require(request, "editor")
    slug = (slug or "").strip()
    version = (version or "v1.4").strip() or "v1.4"
    today = datetime.date.today()
    if not date_end:
        date_end = today.isoformat()
    if not date_start:
        date_start = date_end
    if not period_label:
        period_label = format_display_date(_parse_iso_date(date_end) or today)
    if not slug:
        slug = date_end
    if date_start > date_end:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    con = db.connect()
    exists = con.execute("SELECT 1 FROM issues WHERE slug=?", (slug,)).fetchone()
    if exists:
        suggest = suggest_next_issue(con)
        issues = [_enrich_issue(r) for r in con.execute(
            """SELECT id, slug, period_label, status, updated_at, published_at, date_end, draft_json,
                      embedding_status, embedding_total, embedding_done, embedding_model
               FROM issues ORDER BY date_end DESC"""
        )]
        for i in issues:
            draft = i.pop("draft_json", None)
            i["has_preview"] = db.issue_json_renderable(draft)
            if i["status"] != "published":
                blockers = publish_blockers(con, i["id"], draft or "")
                i["can_go_live"] = len(blockers) == 0
                i["publish_hint"] = "；".join(blockers) if blockers else ""
            else:
                i["can_go_live"] = False
                i["publish_hint"] = ""
        con.close()
        return templates.TemplateResponse(
            "admin.html",
            ctx(request, issues=issues, suggest=suggest, stypes=ingest.SOURCE_TYPES, teams=ingest.TEAMS,
                flash_err=f"期号「{slug}」已存在，请换一个（例如今天日期）"),
            status_code=400,
        )
    con.execute("INSERT INTO issues(slug,date_start,date_end,period_label,version,status,updated_at) VALUES(?,?,?,?,?,'draft',?)",
                (slug, date_start, date_end, period_label, version, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}", status_code=302)


@app.get("/admin/issue/{slug}/delete")
def issue_delete_get(slug: str):
    """避免删除失败后地址栏停在 /delete，刷新变成 Method Not Allowed。"""
    return RedirectResponse("/admin/issues", status_code=302)


@app.post("/admin/issue/{slug}/delete")
def issue_delete(request: Request, slug: str, confirm_slug: str = Form("")):
    """删除整期。草稿：编辑即可；已上线：仅所有者。确认由前端对话框完成。"""
    from urllib.parse import quote
    u = auth.require(request, "editor")
    wants_json = "application/json" in (request.headers.get("accept") or "")
    con = db.connect()
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "没有这一期")
    if not auth.can_delete_issue(u, r["status"]):
        con.close()
        raise HTTPException(403, "权限不足：已上线期仅所有者可删" if r["status"] == "published" else "权限不足")
    if (confirm_slug or "").strip() != slug:
        con.close()
        if wants_json:
            return JSONResponse({"ok": False, "error": "请确认期号后再删除"}, status_code=400)
        raise HTTPException(400, "请确认期号后再删除")
    db.delete_issue(con, slug)
    con.commit()
    con.close()
    try:
        job_store.drop("pipeline", slug)
        job_store.drop("preview", slug)
    except Exception:
        pass
    raw = RAW_DIR / slug
    if raw.exists() and raw.is_dir():
        import shutil
        shutil.rmtree(raw, ignore_errors=True)
    if wants_json:
        return JSONResponse({"ok": True, "slug": slug})
    return RedirectResponse("/admin/issues", status_code=302)

@app.get("/admin/issue/{slug}", response_class=HTMLResponse)
def issue_admin(request: Request, slug: str, step: int | None = None, err: str = ""):
    u = auth.require(request, "editor")
    con = db.connect()
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r: raise HTTPException(404)
    sources = [dict(x) for x in con.execute(
        "SELECT id, stype, team, title, filename, fetched_at, extracted, length(text) AS n, channel, meta "
        "FROM sources WHERE issue_id=? ORDER BY id DESC", (r["id"],))]
    from .aggregator import split_hint
    for s in sources:
        s["split_hint"] = split_hint(s.get("meta"))
        s["team"] = ingest.source_pick_for(s.get("team") or "", s.get("stype") or "")
    counts = con.execute("SELECT COUNT(*) AS n, SUM(blocked) AS b FROM items WHERE issue_id=?", (r["id"],)).fetchone()
    cards = [dict(x) for x in con.execute("SELECT * FROM cards WHERE issue_id=? ORDER BY team", (r["id"],))]
    for c in cards: c["card"] = json.loads(c["card_json"] or "{}")
    edits = [dict(x) for x in con.execute("SELECT * FROM edits WHERE issue_id=? ORDER BY id DESC LIMIT 50", (r["id"],))]
    mails = [dict(x) for x in con.execute("SELECT * FROM mail_log WHERE issue_id=? ORDER BY id DESC LIMIT 20", (r["id"],))]
    versions = [dict(x) for x in con.execute("SELECT version, at, by_user, cards_out, edited_count, url FROM versions WHERE issue_id=? ORDER BY id DESC", (r["id"],))]
    n_merged = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=? AND merged_into IS NOT NULL", (r["id"],)).fetchone()["c"]
    n_noowner = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=? AND (owner_team IS NULL OR owner_team='')", (r["id"],)).fetchone()["c"]
    has_draft = db.draft_is_ready(r["draft_json"])
    draft_stale = bool(r["draft_json"]) and not has_draft
    unextracted = con.execute(
        "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND extracted=0 AND length(COALESCE(text,''))>0",
        (r["id"],),
    ).fetchone()["c"]
    blockers = publish_blockers(con, r["id"], r["draft_json"] or "")
    sources_need_review = []
    for s in sources:
        if not ingest.is_aggregation_source(
            stype=s.get("stype") or "",
            team=s.get("team") or "",
            channel=s.get("channel") or "",
        ):
            continue
        try:
            m = json.loads(s.get("meta") or "{}")
        except (json.JSONDecodeError, TypeError):
            m = {}
        if (m.get("split") or {}).get("needs_review"):
            sources_need_review.append({"id": s["id"], "title": s.get("title") or f"#{s['id']}"})
    weak_rels = []
    try:
        draft_obj = json.loads(r["draft_json"] or "{}") if r["draft_json"] else {}
        for i, rel in enumerate(draft_obj.get("relations") or []):
            if isinstance(rel, dict) and rel.get("needs_review"):
                weak_rels.append({
                    "index": i,
                    "title": rel.get("title") or "",
                    "label": rel.get("label") or "",
                    "body": (rel.get("body") or "")[:160],
                })
    except (json.JSONDecodeError, TypeError):
        pass
    con.close()
    p = auth.perms(u)
    # 0 放入素材 · 1 挖掘 · 2 审校 · 3 确认上线
    start_step = 0 if step is None else max(0, min(3, int(step)))
    return templates.TemplateResponse("issue_console.html", ctx(request, issue=_enrich_issue(r), sources=sources, n_items=counts["n"] or 0, n_blocked=counts["b"] or 0,
                                                              cards=cards, edits=edits, mails=mails, versions=versions, n_merged=n_merged,
                                                              n_noowner=n_noowner,
                                                              has_draft=has_draft, draft_stale=draft_stale,
                                                              sources_need_mine=bool(unextracted),
                                                              publish_blockers=blockers,
                                                              sources_need_review=sources_need_review,
                                                              weak_relations=weak_rels,
                                                              role_cn=p["role_cn"],
                                                              steps=pipeline.steps_for_ui(), start_step=start_step,
                                                              flash_err=err,
                                                              stypes=ingest.SOURCE_TYPES, teams=ingest.TEAMS,
                                                              source_picks=ingest.SOURCE_PICKS,
                                                              team_default_stype=ingest.TEAM_DEFAULT_STYPE,
                                                              embeddings_configured=embeddings.is_configured(),
                                                              draft=(r["draft_json"] or ""), external_feeds=os.environ.get("EXTERNAL_FEEDS", ",".join(ingest.EXTERNAL_FEEDS_DEFAULT))))


@app.post("/admin/issue/{slug}/pipeline/start")
def pipeline_start(request: Request, slug: str, force: int = 0, reextract: int = 0):
    auth.require(request, "editor")
    pipeline.start(slug, force=bool(force), reextract=bool(reextract))
    return {"ok": True}


@app.get("/admin/issue/{slug}/pipeline/status")
def pipeline_status(request: Request, slug: str):
    auth.require(request, "editor")
    return pipeline.get_state(slug)

def _safe_filename(name: str | None) -> str:
    raw = Path(name or "").name.strip()
    if not raw or raw in (".", ".."):
        return f"upload-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    return raw.replace("\x00", "")


@app.get("/admin/issue/{slug}/upload")
def upload_get(slug: str):
    return RedirectResponse(f"/admin/issue/{slug}", status_code=302)


@app.post("/admin/issue/{slug}/upload")
async def upload(
    request: Request,
    slug: str,
    files: list[UploadFile] = File(...),
    stype: str = Form(""),
    team: str = Form(""),
    title: str = Form(""),
    channel: str = Form(""),
):
    u = auth.require(request, "editor")
    wants_json = (
        request.headers.get("x-mesh-ajax") == "1"
        or "application/json" in (request.headers.get("accept") or "")
    )
    con = db.connect(); r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close(); raise HTTPException(404)
    d = RAW_DIR / slug; d.mkdir(parents=True, exist_ok=True)
    default_team = (u.get("t") or "").strip()
    saved = 0
    sources_out: list[dict] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        filename = _safe_filename(f.filename)
        path = d / filename
        if path.exists() and path.is_dir():
            filename = f"{path.stem}-{datetime.datetime.now().strftime('%H%M%S')}{path.suffix}"
            path = d / filename
        path.write_bytes(data)
        text, meta = ingest.read_upload(filename, data)
        guessed = ingest.infer_source_meta(filename, text, default_team=default_team, filename_only=True)
        use_stype = (stype or "").strip() or guessed["stype"]
        use_team = ingest.canonical_team((team or "").strip() or guessed["team"])
        use_channel = (channel or "").strip() or guessed["channel"]
        use_title = (title or "").strip() or guessed["title"] or filename
        meta["inferred"] = guessed
        from .aggregator import is_mixed_source, should_pre_explode, split_bundle_ex, sources_from_split
        rows = [{
            "stype": use_stype,
            "team": use_team,
            "title": use_title,
            "filename": filename,
            "raw_path": str(path),
            "text": text,
            "meta": meta,
            "channel": use_channel,
        }]
        if is_mixed_source(stype=use_stype, team=use_team, channel=use_channel, title=use_title, text=text):
            split = split_bundle_ex(text, source_title=use_title)
            if should_pre_explode(split):
                rows = sources_from_split(
                    split,
                    parent_title=use_title,
                    parent_filename=filename,
                    raw_path=str(path),
                    base_meta={"inferred": guessed},
                    upload_team=use_team,
                )
            else:
                # 置信不足：整包入库，抽取时再拆；低置信才 needs_review
                keep_meta = dict(meta)
                keep_meta["split"] = {**split.to_meta(), "pre_explode": False}
                rows[0]["meta"] = keep_meta
                rows[0]["stype"] = "T13"
                rows[0]["team"] = "内容中心·数据聚合"
                rows[0]["channel"] = "aggregator"
        for row in rows:
            sid = db.insert_id(
                con,
                "INSERT INTO sources(issue_id,stype,team,title,filename,raw_path,text,meta,channel) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    r["id"], row["stype"], row["team"], row["title"], row["filename"],
                    row.get("raw_path") or str(path), row["text"],
                    json.dumps(row["meta"], ensure_ascii=False), row.get("channel") or use_channel,
                ),
            )
            sources_out.append({
                "id": sid,
                "title": row["title"],
                "stype": row["stype"],
                "team": row["team"],
                "channel": row.get("channel") or use_channel,
                "n": len(row.get("text") or ""),
                "extracted": 0,
            })
            saved += 1
    con.commit(); con.close()
    if wants_json:
        if not saved:
            return JSONResponse({"ok": False, "error": "empty", "saved": 0, "sources": []}, status_code=400)
        return JSONResponse({"ok": True, "saved": saved, "sources": sources_out})
    if not saved:
        return RedirectResponse(f"/admin/issue/{slug}?upload_error=empty", status_code=302)
    return RedirectResponse(f"/admin/issue/{slug}?uploaded={saved}", status_code=302)

@app.post("/admin/issue/{slug}/paste")
def paste(request: Request, slug: str, title: str = Form(""), text: str = Form(...), stype: str = Form(""), team: str = Form(""), channel: str = Form("")):
    u = auth.require(request, "editor")
    con = db.connect(); r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    guessed = ingest.infer_source_meta(title or "粘贴文本.txt", text, default_team=(u.get("t") or "").strip())
    use_stype = (stype or "").strip() or guessed["stype"]
    use_team = ingest.canonical_team((team or "").strip() or guessed["team"])
    use_channel = (channel or "").strip() or guessed["channel"]
    use_title = (title or "").strip() or "粘贴文本"
    from .aggregator import is_mixed_source, should_pre_explode, split_bundle_ex, sources_from_split
    rows = [{
        "stype": use_stype,
        "team": use_team,
        "title": use_title,
        "filename": "",
        "raw_path": "",
        "text": text,
        "meta": {"inferred": guessed},
        "channel": use_channel,
    }]
    if is_mixed_source(stype=use_stype, team=use_team, channel=use_channel, title=use_title, text=text):
        split = split_bundle_ex(text, source_title=use_title)
        if should_pre_explode(split):
            rows = sources_from_split(
                split,
                parent_title=use_title,
                parent_filename=use_title,
                base_meta={"inferred": guessed},
                upload_team=use_team,
            )
        else:
            keep_meta = {"inferred": guessed, "split": {**split.to_meta(), "pre_explode": False}}
            rows[0]["meta"] = keep_meta
            rows[0]["stype"] = "T13"
            rows[0]["team"] = "内容中心·数据聚合"
            rows[0]["channel"] = "aggregator"
    for row in rows:
        con.execute(
            "INSERT INTO sources(issue_id,stype,team,title,filename,text,meta,channel) VALUES(?,?,?,?,?,?,?,?)",
            (
                r["id"], row["stype"], row["team"], row["title"], row.get("filename") or "",
                row["text"], json.dumps(row["meta"], ensure_ascii=False), row.get("channel") or use_channel,
            ),
        )
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}", status_code=302)

@app.post("/admin/issue/{slug}/fetch")
def fetch_feeds(request: Request, slug: str, which: str = Form("all"), allow_stale: int = Form(0)):
    auth.require(request, "editor")
    con = db.connect(); r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    feeds = []
    if which in ("all", "cn", "en"):
        for k, f in ingest.DEFAULT_FEEDS.items():
            if which == "all" or (which == "cn" and k == "geekpark_cn") or (which == "en" and k == "geekpark_en"): feeds.append((f["label"], f["url"], f["team"], f["stype"]))
    if which in ("all", "ext"):
        for u in [x.strip() for x in os.environ.get("EXTERNAL_FEEDS", ",".join(ingest.EXTERNAL_FEEDS_DEFAULT)).split(",") if x.strip()]:
            feeds.append((f"外部媒体 · {u}", u, "外部媒体", "T7"))
    for label, url, team, stype in feeds:
        try:
            entries = ingest.fetch_feed(url, allow_stale=bool(allow_stale))
            text = ingest.feed_to_text(entries)
            con.execute("INSERT INTO sources(issue_id,stype,team,title,filename,text,meta,channel) VALUES(?,?,?,?,?,?,?,'aggregator')",
                        (r["id"], stype, team, label, url, text, json.dumps({"n": len(entries), "url": url}, ensure_ascii=False)))
        except Exception as e:
            con.execute("INSERT INTO sources(issue_id,stype,team,title,filename,text,meta,channel) VALUES(?,?,?,?,?,?,?,'aggregator')",
                        (r["id"], stype, team, label + "（抓取失败）", url, "", json.dumps({"error": str(e)}, ensure_ascii=False)))
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}", status_code=302)

@app.post("/admin/source/{sid}/delete")
def source_delete(request: Request, sid: int):
    auth.require(request, "editor")
    wants_json = "application/json" in (request.headers.get("accept") or "")
    con = db.connect(); r = con.execute("SELECT issue_id FROM sources WHERE id=?", (sid,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    slug = con.execute("SELECT slug FROM issues WHERE id=?", (r["issue_id"],)).fetchone()["slug"]
    con.execute("DELETE FROM items WHERE source_id=?", (sid,))
    con.execute("DELETE FROM sources WHERE id=?", (sid,))
    db.mark_draft_stale(con, r["issue_id"])
    con.commit(); con.close()
    if wants_json:
        return JSONResponse({"ok": True, "id": sid, "slug": slug})
    return RedirectResponse(f"/admin/issue/{slug}", status_code=302)


@app.post("/api/source_meta")
def api_source_meta(request: Request, payload: dict):
    """行内修正来源的 stype / team / channel，并同步条目。"""
    auth.require(request, "editor")
    sid = int(payload.get("id") or 0)
    if not sid:
        raise HTTPException(400, "缺少来源 id")
    stype = (payload.get("stype") or "").strip()
    team = (payload.get("team") or "").strip()
    # 未传 channel 时保留库内原值，避免 UI 默认「部门提交」把 aggregator 盖掉
    channel_raw = payload.get("channel")
    channel = (channel_raw or "").strip() if channel_raw is not None else ""
    if channel and channel not in ("manual", "aggregator"):
        channel = "manual"
    if team:
        team = ingest.canonical_source_pick(team)
        if team not in ingest.TEAM_DEFAULT_STYPE:
            raise HTTPException(400, "未知团队")
        stype = ingest.default_stype_for_team(team)
    elif stype and stype not in ingest.SOURCE_TYPES:
        raise HTTPException(400, "未知数据类型")
    con = db.connect()
    s = con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not s:
        con.close()
        raise HTTPException(404, "来源不存在")
    new_stype = stype or s["stype"]
    new_team = ingest.source_pick_for(team or s["team"] or "", new_stype)
    owner = ingest.owner_team_for_pick(new_team)
    new_channel = channel or (s["channel"] or "manual")
    if new_channel not in ("manual", "aggregator"):
        new_channel = "manual"
    from .aggregator import AGG_TEAM, is_mixed_source
    team_changed = bool(team) and new_team != ingest.source_pick_for(s["team"] or "", s["stype"] or "")
    stype_changed = new_stype != (s["stype"] or "")
    was_mixed = is_mixed_source(
        stype=s["stype"] or "", team=s["team"] or "", channel=s["channel"] or "manual",
        title=s["title"] or "", text=s["text"] or "",
    )
    will_be_mixed = is_mixed_source(
        stype=new_stype, team=new_team, channel=new_channel,
        title=s["title"] or "", text=s["text"] or "",
    )
    need_reextract = False
    con.execute(
        "UPDATE sources SET stype=?, team=?, channel=? WHERE id=?",
        (new_stype, new_team, new_channel, sid),
    )
    if was_mixed or will_be_mixed:
        if stype_changed or team_changed:
            con.execute("UPDATE sources SET extracted=0 WHERE id=?", (sid,))
            need_reextract = True
    elif team_changed and new_team != AGG_TEAM:
        con.execute(
            "UPDATE items SET stype=?, team=?, channel=?, owner_team=? WHERE source_id=?",
            (new_stype, owner, new_channel, owner, sid),
        )
    else:
        fill_team = owner if new_team != AGG_TEAM else None
        if team_changed and fill_team:
            con.execute(
                "UPDATE items SET stype=?, team=?, channel=?, owner_team=? WHERE source_id=?",
                (new_stype, owner, new_channel, owner, sid),
            )
        else:
            con.execute(
                "UPDATE items SET stype=?, team=?, channel=?, owner_team=COALESCE(NULLIF(owner_team,''), ?) WHERE source_id=?",
                (new_stype, owner, new_channel, fill_team, sid),
            )
    db.mark_draft_stale(con, s["issue_id"])
    con.commit()
    con.close()
    return {"ok": True, "need_reextract": need_reextract, "stype": new_stype, "team": new_team}


@app.post("/api/item_owner")
def api_item_owner(request: Request, payload: dict):
    """逐条指定条目归属团队（T13 混合包等）。"""
    auth.require(request, "editor")
    iid = int(payload.get("id") or 0)
    if not iid:
        raise HTTPException(400, "缺少条目 id")
    from .aggregator import sanitize_owner_team
    owner = sanitize_owner_team(payload.get("owner_team"))
    if not owner:
        raise HTTPException(400, "请选择有效的归属团队（不能为内容中心·数据聚合或其他）")
    con = db.connect()
    row = con.execute("SELECT issue_id FROM items WHERE id=?", (iid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "条目不存在")
    con.execute("UPDATE items SET owner_team=? WHERE id=?", (owner, iid))
    db.mark_draft_stale(con, row["issue_id"])
    con.commit()
    con.close()
    return {"ok": True, "owner_team": owner}


@app.get("/admin/source/{sid}", response_class=HTMLResponse)
def source_view(request: Request, sid: int, err: str = ""):
    # 条目：编辑可见；原文全文：仅 admin/owner（与脱敏说明一致）
    u = auth.require(request, "editor")
    con = db.connect(); s = con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not s:
        con.close()
        raise HTTPException(404)
    items = [dict(x) for x in con.execute("SELECT * FROM items WHERE source_id=? ORDER BY blocked, zone, id", (sid,))]
    slug = con.execute("SELECT slug FROM issues WHERE id=?", (s["issue_id"],)).fetchone()["slug"]
    con.close()
    sdict = dict(s)
    if not auth.at_least(u, "admin"):
        # 不把全文塞进模板，避免编辑角色浏览器里直接看到隔离区原文
        n = len(sdict.get("text") or "")
        sdict["text"] = ""
        sdict["raw_redacted"] = True
        sdict["raw_chars"] = n
        for it in items:
            if it.get("blocked"):
                it["text"] = "【⑤区 / L3 硬拦 · 正文仅管理员可见】"
                it["entities"] = "[]"
    owner_teams = [t for t in ingest.TEAMS if t not in ("内容中心·数据聚合", "其他")]
    from .aggregator import split_hint
    split_info = {}
    try:
        split_info = (json.loads(sdict.get("meta") or "{}") or {}).get("split") or {}
    except (json.JSONDecodeError, TypeError):
        split_info = {}
    # 单团队来源：不展示「确认拆段」按钮（选了谁就是谁）
    if not ingest.is_aggregation_source(
        stype=sdict.get("stype") or "",
        team=sdict.get("team") or "",
        channel=sdict.get("channel") or "",
    ):
        split_info = dict(split_info or {})
        split_info["needs_review"] = False
    return templates.TemplateResponse(
        "source.html",
        ctx(
            request,
            s=sdict,
            items=items,
            slug=slug,
            owner_teams=owner_teams,
            flash_err=err or None,
            split_hint=split_hint(sdict.get("meta")),
            split_info=split_info,
        ),
    )

@app.post("/admin/source/{sid}/extract")
def source_extract(request: Request, sid: int):
    auth.require(request, "editor")
    con = db.connect()
    s = con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not s:
        con.close()
        raise HTTPException(404, "素材不存在")
    iss = con.execute("SELECT slug, date_start, date_end, period_label FROM issues WHERE id=?", (s["issue_id"],)).fetchone()
    if not iss:
        con.close()
        raise HTTPException(404, "所属期不存在")
    slug = iss["slug"]
    try:
        skip_split = False
        try:
            meta_obj = json.loads(s["meta"] or "{}") if isinstance(s["meta"], str) else (s["meta"] or {})
            skip_split = (meta_obj.get("split") or {}).get("mode") == "pre_split"
        except (json.JSONDecodeError, TypeError, AttributeError):
            skip_split = False
        items, split_meta = llm.extract_source(
            s["stype"], s["team"], s["title"], s["text"] or "",
            period_start=iss["date_start"] or "",
            period_end=iss["date_end"] or "",
            period_label=iss["period_label"] or "",
            channel=s["channel"] or "manual",
            skip_split=skip_split,
            source_id=sid,
        )
    except Exception as e:
        con.close()
        return _flash_redirect(f"/admin/source/{sid}", f"抽取失败：{e}")
    con.execute("DELETE FROM items WHERE source_id=?", (sid,))
    from .aggregator import merge_source_meta
    from .attribution import apply_attribution_to_item
    for it in items:
        item_stype = it.get("item_stype") or s["stype"]
        attr = apply_attribution_to_item(
            it, source_team=s["team"] or "", segment_team=it.get("_segment_team"),
        )
        owner = attr.owner_team
        blocked = int(it.get("blocked") or 0)
        con.execute("""INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,source_label,pointer,blocked,owner_team,channel,source_labels,owner_provenance,llm_owner_team_hint)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (s["issue_id"], sid, owner or it.get("team"), item_stype, it["zone"], it["level"], it["kind"], it["text"], json.dumps(it["entities"], ensure_ascii=False),
                     json.dumps(it["roles"], ensure_ascii=False), json.dumps(it["signals"], ensure_ascii=False), it["source_label"], it["pointer"], blocked,
                     owner, s["channel"] or "manual",
                     json.dumps([it["source_label"]] if it.get("source_label") else [], ensure_ascii=False),
                     attr.provenance, attr.llm_owner_team_hint or it.get("llm_owner_team_hint")))
        for name in it["entities"]:
            if name: con.execute("INSERT INTO entities(name,kind,first_issue) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING", (name, "auto", slug))
    meta = merge_source_meta(s["meta"], split_meta)
    try:
        meta_obj = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
    except (json.JSONDecodeError, TypeError):
        meta_obj = {}
    if llm.split_needs_review(
        split_meta,
        stype=s.get("stype") or "",
        team=s.get("team") or "",
        channel=s.get("channel") or "",
    ) or (
        ingest.is_aggregation_source(
            stype=s.get("stype") or "",
            team=s.get("team") or "",
            channel=s.get("channel") or "",
        )
        and (meta_obj.get("split") or {}).get("mode") == "fallback"
    ):
        sp = dict(meta_obj.get("split") or {})
        sp["needs_review"] = True
        meta_obj["split"] = sp
        meta = json.dumps(meta_obj, ensure_ascii=False)
    else:
        sp = dict(meta_obj.get("split") or {})
        if sp.get("needs_review"):
            sp["needs_review"] = False
            meta_obj["split"] = sp
            meta = json.dumps(meta_obj, ensure_ascii=False)
    con.execute("UPDATE sources SET extracted=1, meta=? WHERE id=?", (meta, sid))
    issue_id = s["issue_id"]
    st = con.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()
    if st and st["status"] == "published":
        # 已上线语料：Ask/读者只认发布快照。重抽只改 live items + 标草稿失效，不改公开索引。
        db.mark_draft_stale(con, issue_id)
    con.commit(); con.close()
    _ASK_CACHE.clear()
    if st and st["status"] == "published":
        return _flash_redirect(
            f"/admin/source/{sid}",
            "已重新抽取（读者页与 Ask 仍为上次上线版本；请生成草稿并由 Owner 重新上线后才会更新公开内容）。",
        )
    return RedirectResponse(f"/admin/source/{sid}", status_code=302)

@app.post("/admin/issue/{slug}/extract_all")
def extract_all(request: Request, slug: str):
    auth.require(request, "editor")
    con = db.connect()
    r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "没有这一期")
    ids = [x["id"] for x in con.execute("SELECT id FROM sources WHERE issue_id=? AND extracted=0 AND length(text)>0", (r["id"],))]
    con.close()
    errs = []
    for sid in ids:
        try: source_extract(request, sid)
        except Exception as e: errs.append(f"{sid}: {e}")
    if errs:
        return _flash_redirect(f"/admin/issue/{slug}", "部分抽取失败：" + "；".join(errs[:8]))
    con = db.connect()
    r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if r:
        merge.apply_merge(con, r["id"])
        con.commit()
    con.close()
    return RedirectResponse(f"/admin/issue/{slug}", status_code=302)

def _upsert_team_card(con, issue_id: int, team: str, card: dict) -> None:
    """写入团队要点卡（生成即 approved，无审批流）。"""
    payload = json.dumps(card, ensure_ascii=False)
    ex = con.execute("SELECT id FROM cards WHERE issue_id=? AND team=?", (issue_id, team)).fetchone()
    if ex:
        con.execute(
            "UPDATE cards SET card_json=?, status='approved', reviewer='mesh-auto', "
            "reviewed_at=datetime('now') WHERE id=?",
            (payload, ex["id"]),
        )
    else:
        con.execute(
            "INSERT INTO cards(issue_id,team,card_json,status,reviewer,reviewed_at) "
            "VALUES(?,?,?,'approved','mesh-auto',datetime('now'))",
            (issue_id, team, payload),
        )


def _build_cards_for_issue(con, r) -> None:
    """按归属团队生成要点卡。生成前先做跨通道合并，避免同团队重复被当成跨部门关系。"""
    merge.apply_merge(con, r["id"])
    teams = [x["owner_team"] for x in con.execute("SELECT DISTINCT owner_team FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL AND owner_team IS NOT NULL AND owner_team<>''", (r["id"],))]
    for team in teams:
        if team in ("外部媒体",):
            continue
        items = [dict(x) for x in con.execute("SELECT zone, level, kind, text, entities, roles, signals, source_label, source_labels, channel FROM items WHERE issue_id=? AND owner_team=? AND blocked=0 AND merged_into IS NULL", (r["id"], team))]
        card = llm.build_team_card(team, items, r["period_label"])
        _upsert_team_card(con, r["id"], team, card)
    db.mark_draft_stale(con, r["id"])


def _item_teams_for_draft(con, issue_id: int) -> list[str]:
    return db.draft_teams_for_issue(con, issue_id)


def _build_draft_for_issue(con, r, slug: str) -> dict:
    """根据团队要点卡 + 关系候选生成周报草稿 JSON。"""
    from .relation_candidates import merge_relations_from_candidates, prepare_draft_bundle

    merge.apply_merge(con, r["id"])
    # 无要点卡时先生成
    n_cards = con.execute(
        "SELECT COUNT(*) c FROM cards WHERE issue_id=? AND status='approved'", (r["id"],)
    ).fetchone()["c"]
    if not n_cards:
        _build_cards_for_issue(con, r)

    bundle = prepare_draft_bundle(con, r["id"], slug)
    prev = con.execute(
        "SELECT published_json FROM issues WHERE status='published' AND date_end<? "
        "ORDER BY date_end DESC LIMIT 1",
        (r["date_start"],),
    ).fetchone()
    prev_summary = ""
    if prev and prev["published_json"]:
        pj = json.loads(prev["published_json"])
        prev_summary = pj.get("lead", "") + " " + " / ".join(
            x.get("title", "") for x in pj.get("relations", [])
        )

    data = llm.build_issue_draft(
        dict(r),
        bundle["team_cards"],
        bundle["relation_candidates"],
        bundle["first_names"],
        bundle["external_items"],
        prev_summary,
    )
    data = merge_relations_from_candidates(
        data,
        bundle["relation_candidates"],
        bundle["item_rows"],
        team_cards=bundle["team_cards"],
    )
    data["slug"] = slug
    data["period_label"] = r["period_label"]
    data["version"] = r["version"]
    data.pop("_stale", None)
    return data


@app.post("/admin/issue/{slug}/cards")
def build_cards(request: Request, slug: str):
    u = auth.require(request, "editor")
    con = db.connect(); r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    try:
        _build_cards_for_issue(con, r)
    except Exception as e:
        con.close()
        return _flash_redirect(f"/admin/issue/{slug}?step=2", f"生成要点卡失败：{e}")
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}?step=2", status_code=302)

@app.post("/admin/card/{cid}/save")
def card_save(request: Request, cid: int, card_json: str = Form(...)):
    """编辑要点卡 JSON（无审批流，保存即生效）。"""
    u = auth.require(request, "editor")
    con = db.connect(); c = con.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    if not c:
        con.close()
        raise HTTPException(404)
    slug = con.execute("SELECT slug FROM issues WHERE id=?", (c["issue_id"],)).fetchone()["slug"]
    try:
        json.loads(card_json)
    except Exception:
        con.close()
        return _flash_redirect(f"/admin/issue/{slug}?step=2", "要点卡 JSON 格式有误")
    con.execute("INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
                (c["issue_id"], u["u"], f"card:{c['team']}", c["card_json"], card_json))
    con.execute(
        "UPDATE cards SET card_json=?, status='approved', reviewer=?, reviewed_at=datetime('now') WHERE id=?",
        (card_json, u["u"], cid),
    )
    db.mark_draft_stale(con, c["issue_id"])
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}?step=2", status_code=302)

@app.post("/admin/issue/{slug}/draft")
def build_draft(request: Request, slug: str):
    auth.require(request, "editor")
    con = db.connect()
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "没有这一期")
    try:
        data = _build_draft_for_issue(con, r, slug)
    except Exception as e:
        con.close()
        return _flash_redirect(f"/admin/issue/{slug}?step=2", f"生成草稿失败：{e}")
    con.execute("UPDATE issues SET draft_json=?, updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), r["id"]))
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}?step=2", status_code=302)


@app.post("/admin/issue/{slug}/preview/start")
def preview_start(request: Request, slug: str, force: int = 0):
    u = auth.require(request, "editor")
    st = preview_job.start(slug, u["u"], force=bool(force))
    return {"ok": True, **st}


@app.get("/admin/issue/{slug}/preview/status")
def preview_status(request: Request, slug: str):
    auth.require(request, "editor")
    return preview_job.get_state(slug)


@app.post("/admin/issue/{slug}/create_revision")
def create_revision(request: Request, slug: str):
    """已上线期：用线上稿重新铺底草稿。保持 published，不清 Ask。

    读者/Ask 仍看 published_json；改完 Preview 后由 Owner 确认上线才替换。
    """
    u = auth.require(request, "editor")
    wants_json = "application/json" in (request.headers.get("accept") or "")
    con = db.connect()
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "没有这一期")
    if (r["status"] or "") != "published":
        con.close()
        msg = "仅已上线期需要从线上稿铺底；当前已是草稿，可直接生成预览。"
        if wants_json:
            return JSONResponse({"ok": False, "error": msg, "status": r["status"]}, status_code=400)
        return _flash_redirect(f"/admin/issue/{slug}", msg)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ensure_draft_for_edit(con, r, sync_from_published=True, force=True)
    con.execute("UPDATE issues SET updated_at=? WHERE id=?", (now, r["id"]))
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (r["id"], u["u"], "create_revision", "published", "draft reset from published (still live)"),
    )
    con.commit()
    con.close()
    if wants_json:
        return JSONResponse({
            "ok": True,
            "slug": slug,
            "status": "published",
            "message": "已用线上稿铺底草稿。读者与 Ask 仍看线上版；改完后生成预览，再由 Owner 确认上线。",
        })
    return RedirectResponse(f"/admin/issue/{slug}?revision=1", status_code=302)


@app.post("/admin/issue/{slug}/prepare_preview")
def prepare_preview(request: Request, slug: str):
    """兼容旧表单：改为启动后台任务并回到控制台轮询（前端应走 /preview/start）。"""
    from urllib.parse import quote
    u = auth.require(request, "editor")
    st = preview_job.start(slug, u["u"])
    if st.get("error") and not st.get("running"):
        return RedirectResponse(
            f"/admin/issue/{slug}?err=" + quote(st["error"]),
            status_code=302,
        )
    return RedirectResponse(f"/admin/issue/{slug}?preview_job=1", status_code=302)

@app.post("/admin/issue/{slug}/save_draft")
def save_draft(request: Request, slug: str, draft: str = Form(...)):
    u = auth.require(request, "editor")
    try:
        data = json.loads(draft)
    except Exception as e:
        return _flash_redirect(f"/admin/issue/{slug}?step=2", f"JSON 格式有误：{e}")
    data.pop("_stale", None)
    data = _clear_preview_gate(data)
    con = db.connect()
    r = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "没有这一期")
    con.execute("INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)", (r["id"], u["u"], "draft_json", (r["draft_json"] or "")[:20000], draft[:20000]))
    con.execute("UPDATE issues SET draft_json=?, updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), r["id"]))
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}?step=2", status_code=302)

@app.post("/api/edit")
def api_edit(request: Request, payload: dict):
    """页面编辑模式写回：path 为 JSON 路径（如 relations.3.title），value 为新文本。一律写草稿，需确认上线后读者才看到。"""
    u = auth.require(request, "editor")
    slug, path, value = payload.get("slug"), payload.get("path", ""), payload.get("value", "")
    con = db.connect(); r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r: raise HTTPException(404)
    ensure_draft_for_edit(con, r)
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    col = edit_json_column()
    data = json.loads(r[col] or "{}")
    ref, keys = data, [k for k in path.split(".") if k]
    try:
        for k in keys[:-1]: ref = ref[int(k)] if isinstance(ref, list) else ref[k]
        last = keys[-1]; before = ref[int(last)] if isinstance(ref, list) else ref.get(last)
        if payload.get("op") == "delete":
            if isinstance(ref, list): ref.pop(int(last))
            else: ref.pop(last, None)
        else:
            if isinstance(ref, list): ref[int(last)] = value
            else: ref[last] = value
    except Exception as e:
        con.close(); raise HTTPException(400, f"路径无效：{e}")
    # 增删/改关系卡后重算 KPI，避免「可同步的关系」与读者卡脱节
    if keys and keys[0] == "relations":
        try:
            from .issue_verify import sync_kpis_from_data
            data = sync_kpis_from_data(data)
        except Exception:
            from .relation_display import count_reader_relations
            rels = [x for x in (data.get("relations") or []) if isinstance(x, dict)]
            n_reader = count_reader_relations(rels)
            kpis = list(data.get("kpis") or [])
            found = False
            for k in kpis:
                if k.get("label") == "可同步的关系":
                    k["n"] = str(n_reader)
                    found = True
            if not found:
                kpis.insert(0, {"n": str(n_reader), "label": "可同步的关系"})
            data["kpis"] = kpis
        from .relation_display import attach_reader_flags, split_relations_for_publish
        rels = attach_reader_flags([r for r in (data.get("relations") or []) if isinstance(r, dict)])
        reader, backlog = split_relations_for_publish(rels)
        data["relations"] = rels
        data["_relations_reader"] = reader
        data["_relations_backlog"] = backlog
    if _edit_invalidates_preview_gate(path):
        data = _clear_preview_gate(data)
    con.execute(f"UPDATE issues SET {col}=?, updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), r["id"]))
    con.execute("INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)", (r["id"], u["u"], f"{col}:{path}", json.dumps(before, ensure_ascii=False)[:5000] if before is not None else "", json.dumps(value, ensure_ascii=False)[:5000]))
    con.commit(); con.close()
    return {"ok": True, "preview_gate_stale": bool(data.get("_preview_gate_stale"))}

@app.post("/api/add_card")
def api_add_card(request: Request, payload: dict):
    u = auth.require(request, "editor")
    slug = payload.get("slug")
    target = payload.get("target", "published")
    section = (payload.get("section") or "relations").strip()
    con = db.connect()
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    ensure_draft_for_edit(con, r)
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    col = edit_json_column()
    data = json.loads(r[col] or "{}")
    if section == "contacts":
        card = {
            "label": "一手接触",
            "title": "新卡片（双击编辑）",
            "body": "补充这段接触的背景。",
            "groups": [
                {
                    "title": "新增分组",
                    "items": [
                        {
                            "name": "新对象",
                            "sub": "",
                            "rows": [{"k": "说明", "v": "双击修改"}],
                            "teams": [],
                            "weight": 1,
                        }
                    ],
                }
            ],
            "sources": ["双击填写来源"],
            "note": "",
        }
        data.setdefault("contacts", []).append(card)
        idx = len(data["contacts"]) - 1
    else:
        card = {
            "label": "已联动",
            "weak": False,
            "decision_tier": "strong",
            "title": "新卡片（双击编辑）",
            "body": "只写事实与来源，不写建议。",
            "details": [],
            "sources": ["双击填写来源"],
            "teams": ["团队"],
            "evidence": [{"source": "manual_edit"}],
            "reader_visible": True,
        }
        data.setdefault("relations", []).append(card)
        section = "relations"
        idx = len(data["relations"]) - 1
        try:
            from .issue_verify import sync_kpis_from_data
            data = sync_kpis_from_data(data)
        except Exception:
            pass
    data = _clear_preview_gate(data)
    con.execute(
        f"UPDATE issues SET {col}=?, updated_at=? WHERE id=?",
        (json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), r["id"]),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (r["id"], u["u"], f"{col}:{section}.append", "", json.dumps(card, ensure_ascii=False)),
    )
    con.commit()
    con.close()
    return {
        "ok": True,
        "index": idx,
        "section": section,
        "card": card,
        "preview_gate_stale": bool(data.get("_preview_gate_stale")),
    }

@app.post("/api/add_item")
def api_add_item(request: Request, payload: dict):
    """在 keywords / plans / contacts 分组下新增一条标签。path 指向 items 数组，如 keywords.groups.0.items"""
    u = auth.require(request, "editor")
    slug = payload.get("slug")
    path = payload.get("path") or ""
    target = payload.get("target", "published")
    kind = payload.get("kind") or "tag"
    con = db.connect()
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    ensure_draft_for_edit(con, r)
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    col = edit_json_column()
    data = json.loads(r[col] or "{}")
    keys = [k for k in path.split(".") if k]
    ref = data
    try:
        for k in keys:
            ref = ref[int(k)] if isinstance(ref, list) else ref[k]
        if not isinstance(ref, list):
            raise ValueError("path 必须指向数组")
    except Exception as e:
        con.close()
        raise HTTPException(400, f"路径无效：{e}")
    if kind == "group":
        item = {
            "title": "新分组",
            "items": [
                {
                    "name": "新对象",
                    "sub": "",
                    "rows": [{"k": "说明", "v": "双击修改"}],
                    "teams": [],
                    "weight": 1,
                }
            ],
        }
    else:
        item = {
            "name": "新标签",
            "sub": "",
            "rows": [{"k": "说明", "v": "双击修改"}],
            "teams": [],
            "weight": 1,
        }
        if kind == "plan":
            item["cert"] = "计划中"
    ref.append(item)
    if _edit_invalidates_preview_gate(path):
        data = _clear_preview_gate(data)
    con.execute(
        f"UPDATE issues SET {col}=?, updated_at=? WHERE id=?",
        (json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), r["id"]),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (r["id"], u["u"], f"{col}:{path}.append", "", json.dumps(item, ensure_ascii=False)),
    )
    con.commit()
    con.close()
    return {
        "ok": True,
        "index": len(ref) - 1,
        "path": path,
        "kind": kind,
        "item": item,
        "preview_gate_stale": bool(data.get("_preview_gate_stale")),
    }

@app.post("/admin/issue/{slug}/merge")
def merge_issue(request: Request, slug: str):
    """跨通道合并。必须在生成要点卡/草稿之前跑，否则同团队重复数据会被误判成跨部门关系。"""
    auth.require(request, "editor")
    con = db.connect(); r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r: con.close(); raise HTTPException(404, "没有这一期")
    stat = merge.apply_merge(con, r["id"]); con.close()
    return RedirectResponse(f"/admin/issue/{slug}?merged={stat['groups']}_{stat['merged']}", status_code=302)


@app.post("/admin/issue/{slug}/request_publish")
def request_publish(request: Request, slug: str):
    """编辑可提交上线请求；确认上线由管理员 / 所有者执行。"""
    u = auth.require(request, "editor")
    con = db.connect()
    r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "没有这一期")
    con.execute("INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
                (r["id"], u["u"], "request_publish", "", "已提交，等待所有者确认"))
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}?requested=1", status_code=302)


@app.post("/admin/issue/{slug}/publish")
def publish(request: Request, slug: str, confirm: str = Form("")):
    """上线：管理员 / 所有者；写入不可改版本记录。确认由前端对话框完成，不再要求手打文字。"""
    u = auth.require(request, "admin")
    wants_json = "application/json" in (request.headers.get("accept") or "")
    back = f"/{slug}?preview=1&edit=1"

    def fail(msg: str, code: int = 400):
        if wants_json:
            return JSONResponse({"ok": False, "error": msg}, status_code=code)
        from urllib.parse import quote
        flat = " · ".join(line.strip(" ·") for line in msg.replace("\r", "").split("\n") if line.strip())
        return RedirectResponse(f"{back}&puberr={quote(flat[:500])}", status_code=302)

    con = db.connect(); r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r or not r["draft_json"]:
        con.close()
        return fail("没有草稿可发布")
    # 进预览检查通过后可上线；若改过稿（gate 已作废）必须重新生成预览。
    draft_obj = {}
    try:
        draft_obj = json.loads(r["draft_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        draft_obj = {}
    if draft_obj.get("_preview_building"):
        con.close()
        return fail("预览仍在后台生成中（要点卡/草稿/关系未齐），请等待完成后再上线")
    if not draft_obj.get("_preview_gate_ok"):
        blockers = publish_blockers(con, r["id"], r["draft_json"] or "")
        hint = (
            "稿面已改动或尚未通过进预览检查，请重新点「生成预览」后再上线"
            if draft_obj.get("_preview_gate_stale")
            else "上线前检查未通过（请重新点「生成预览」通过检查后再上线）"
        )
        if blockers:
            msg = hint + "：\n" + "\n".join(f"· {x}" for x in blockers)
            con.close()
            return fail(msg)
        # 无结构性 blockers 但仍无 gate：也要求重跑预览，避免改稿绕过论证
        con.close()
        return fail(hint)
    data = draft_obj
    data.pop("_stale", None)
    data.pop("_preview_gate_ok", None)
    data.pop("_preview_gate_at", None)
    data.pop("_preview_gate_stale", None)
    from .relation_display import attach_reader_flags, build_published_projection, split_relations_for_publish
    data["relations"] = attach_reader_flags(
        [x for x in (data.get("relations") or []) if isinstance(x, dict)]
    )
    reader, backlog = split_relations_for_publish(data["relations"])
    data["_relations_reader"] = reader
    data["_relations_backlog"] = backlog
    published = build_published_projection(data)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    pub_day = datetime.date.today()
    pub_label = format_display_date(pub_day)
    pub_iso = pub_day.isoformat()
    draft_payload = json.dumps(data, ensure_ascii=False)
    pub_payload = json.dumps(published, ensure_ascii=False)
    con.execute(
        "UPDATE issues SET published_json=?, draft_json=?, status='published', published_at=?, "
        "updated_at=?, period_label=?, date_end=?, date_start=? WHERE id=?",
        (pub_payload, draft_payload, now, now, pub_label, pub_iso, pub_iso, r["id"]),
    )
    db.register_entities(con, published, slug)
    db.snapshot_published_items(con, r["id"])
    db.reindex_issue(con, r["id"])
    _ASK_CACHE.clear()
    seq = con.execute("SELECT COUNT(*) c FROM versions WHERE issue_id=?", (r["id"],)).fetchone()["c"] + 1
    cards_out = len(published.get("relations", [])) + sum(
        len(g.get("items", [])) for c in published.get("contacts", []) for g in c.get("groups", [])
    )
    edited = con.execute("SELECT COUNT(*) c FROM edits WHERE issue_id=? AND target LIKE '%%.%%'", (r["id"],)).fetchone()["c"]
    con.execute(
        "INSERT INTO versions(issue_id,version,at,by_user,cards_out,edited_count,url,snapshot_json) VALUES(?,?,?,?,?,?,?,?)",
        (r["id"], f"v{seq}", now, u.get("d") or u["u"], cards_out, edited,
         f"{os.environ.get('MESH_BASE_URL','').rstrip('/')}/{slug}", pub_payload),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (r["id"], u["u"], "publish", "", f"v{seq} reader_relations={len(published.get('relations') or [])}"),
    )
    con.commit()
    embed_info: dict = {"persisted": True, "queued": False, "skipped": True}
    if embeddings.is_configured():
        try:
            embed_info = embed_job.ensure_for_slug(slug, by="publish")
        except Exception as e:
            print(f"[mesh] embed ensure failed for {slug}: {e}", flush=True)
            embed_info = {
                "ok": False,
                "persisted": True,
                "queued": False,
                "error": str(e)[:200],
                "hint": "issues.embedding_status=pending 已落库，启动 maintenance 会重试",
            }
    con.close()
    _ASK_CACHE.clear()
    edm_job.enqueue_auto_send(slug, by=u.get("u") or "publish")
    if wants_json:
        return JSONResponse({
            "ok": True,
            "slug": slug,
            "status": "published",
            "published_at": now,
            "embed": embed_info,
        })
    return RedirectResponse(f"/{slug}?published=1", status_code=302)

@app.post("/admin/issue/{slug}/unpublish")
def unpublish(request: Request, slug: str):
    auth.require(request, "admin")
    con = db.connect()
    r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    con.execute("UPDATE issues SET status='draft' WHERE slug=?", (slug,))
    db.reindex_issue(con, r["id"])  # 清 FTS + 事实表
    con.commit(); con.close()
    _ASK_CACHE.clear()
    # 留在期管理列表；fetch 请求返回 JSON，避免整页跳走
    wants_json = "application/json" in (request.headers.get("accept") or "")
    if wants_json:
        return JSONResponse({"ok": True, "slug": slug, "status": "draft"})
    return RedirectResponse("/admin/issues", status_code=302)

def _edm_json_from_row(row: dict) -> dict:
    """从 issues 行解析 EDM 用 JSON，避免重复查库。"""
    if row.get("status") == "published":
        raw = row.get("published_json") or "{}"
    else:
        raw = row.get("draft_json") or row.get("published_json") or "{}"
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


@app.get("/admin/edm", response_class=HTMLResponse)
def edm_admin(request: Request, saved: int = 0, err: str = ""):
    auth.require(request, "owner")
    con = db.connect()
    issues = []
    for row in con.execute(
        "SELECT id, slug, period_label, status, published_at, updated_at, version, draft_json, published_json, date_end FROM issues ORDER BY date_end DESC"
    ):
        row_d = dict(row)
        data = _edm_json_from_row(row_d)
        i = _enrich_issue(row_d)
        i.pop("draft_json", None)
        i.pop("published_json", None)
        i["edm_subject"] = edm.build_edm_subject(row_d, data, use_llm=False)
        i["mail"] = edm.mail_status_label(con, i["id"])
        i["auto_send"] = edm.issue_auto_send_enabled(con, i["id"])
        i["draft_sync"] = not edm_draft_out_of_sync(row_d)
        issues.append(i)
    default_to = edm.default_to(con)
    auto_default = (db.get_setting(con, "edm_auto_send_default") or "1") == "1"
    con.close()
    return templates.TemplateResponse(
        "edm_admin.html",
        ctx(
            request,
            nav="edm",
            issues=issues,
            default_to=default_to,
            auto_default=auto_default,
            smtp_ok=edm.smtp_configured(),
            flash_ok="已保存" if saved else None,
            flash_err=err or None,
        ),
    )


@app.post("/admin/edm/settings")
def edm_settings_save(request: Request, default_to: str = Form(""), auto_default: str = Form("")):
    auth.require(request, "owner")
    con = db.connect()
    db.set_setting(con, "edm_default_to", default_to.strip())
    db.set_setting(con, "edm_auto_send_default", "1" if auto_default == "1" else "0")
    con.commit()
    con.close()
    return RedirectResponse("/admin/edm?saved=1", status_code=302)


@app.post("/admin/edm/issue/{slug}/auto")
def edm_issue_auto(request: Request, slug: str, enabled: str = Form("")):
    auth.require(request, "owner")
    con = db.connect()
    r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404)
    db.set_setting(con, f"edm_auto_send:{r['id']}", "1" if enabled == "1" else "0")
    con.commit()
    con.close()
    return RedirectResponse(f"/admin/edm#{slug}", status_code=302)


@app.post("/admin/edm/issue/{slug}/send")
def edm_admin_send(request: Request, slug: str, to: str = Form(""), test: int = Form(1)):
    auth.require(request, "owner")
    from urllib.parse import quote
    con = db.connect()
    r, data, source = load_issue_for_edm(con, slug)
    if not r:
        con.close()
        raise HTTPException(404)
    if not int(test) and r["status"] != "published":
        con.close()
        return _flash_redirect("/admin/edm", "正式发送仅限已上线期")
    if not int(test) and source != "published":
        con.close()
        return _flash_redirect("/admin/edm", "该期尚无上线稿，请先确认上线")
    addrs = edm.parse_addrs(to) or edm.parse_addrs(edm.default_to(con))
    if not addrs:
        con.close()
        return _flash_redirect("/admin/edm", "请填写收件人或配置默认名单")
    ok, err, _ = edm.send_for_issue(
        con,
        dict(r),
        data or {},
        to_addrs=addrs,
        test=bool(int(test)),
        base_url=BASE_URL,
        logo_url=os.environ.get("MESH_LOGO_URL", ""),
    )
    con.close()
    if not ok:
        return RedirectResponse("/admin/edm?err=" + quote(err[:200]), status_code=302)
    return RedirectResponse(f"/admin/edm?saved=1#{slug}", status_code=302)


@app.get("/admin/issue/{slug}/edm", response_class=HTMLResponse)
def edm_preview(request: Request, slug: str, legacy: int = 0):
    auth.require(request, "owner")
    import html as html_mod

    con = db.connect()
    r, data, source = load_issue_for_edm(con, slug)
    if not r:
        con.close()
        raise HTTPException(404)
    if legacy:
        data = data or {}
        data.setdefault("kpis", [])
        data.setdefault("relations", [])
        n_rel = len(data.get("relations", []))
        draft_sync = not edm_draft_out_of_sync(r)
        con.close()
        return templates.TemplateResponse(
            "issue_edm.html",
            ctx(
                request,
                issue=_enrich_issue(r),
                d=data,
                n_rel=n_rel,
                edm_source=source,
                draft_sync=draft_sync,
            ),
        )
    issue_row = _enrich_issue(r)
    subject = edm.build_edm_subject(issue_row, data or {}, use_llm=False)
    h, _ = edm.render_edm(issue_row, data or {}, BASE_URL, os.environ.get("MESH_LOGO_URL", ""))
    con.close()
    src_label = "已上线稿" if source == "published" else "草稿"
    slug_e = html_mod.escape(slug)
    period_e = html_mod.escape(issue_row.get("display_date") or slug)
    subject_e = html_mod.escape(subject)
    chrome = (
        '<div style="position:sticky;top:0;z-index:9999;background:#14382F;color:#fff;'
        'padding:10px 14px;font:13px/1.5 -apple-system,\'PingFang SC\',sans-serif;">'
        '<div style="max-width:480px;margin:0 auto;display:flex;flex-wrap:wrap;gap:8px 12px;'
        'align-items:center;justify-content:space-between;">'
        f'<span><b>EDM 预览</b> · {period_e} · {src_label}</span>'
        f'<a href="/admin/edm#{slug_e}" style="color:#C6FF3F;font-weight:700;text-decoration:none">← EDM 管理</a>'
        f'</div><div style="max-width:480px;margin:6px auto 0;font-size:12px;opacity:.92;word-break:break-word;">'
        f'发送标题（规则预览，正式发送时由模型生成）：{subject_e}</div></div>'
    )
    if re.search(r"<body[^>]*>", h, re.I):
        h = re.sub(r"(<body[^>]*>)", r"\1" + chrome, h, count=1, flags=re.I)
    else:
        h = chrome + h
    return HTMLResponse(h)

@app.post("/admin/issue/{slug}/edm/send")
def edm_send(request: Request, slug: str, to: str = Form(...), test: int = Form(1)):
    """兼容旧入口：转发到 EDM 管理页逻辑。"""
    auth.require(request, "owner")
    from urllib.parse import quote
    con = db.connect()
    r, data, source = load_issue_for_edm(con, slug)
    if not r:
        con.close()
        raise HTTPException(404)
    if not int(test) and r["status"] != "published":
        con.close()
        return _flash_redirect("/admin/edm", "正式发送仅限已上线期")
    if not int(test) and source != "published":
        con.close()
        return _flash_redirect("/admin/edm", "该期尚无上线稿，请先确认上线")
    addrs = edm.parse_addrs(to)
    if not addrs:
        con.close()
        return _flash_redirect("/admin/edm", "请填写收件人")
    ok, err, _ = edm.send_for_issue(
        con,
        dict(r),
        data or {},
        to_addrs=addrs,
        test=bool(int(test)),
        base_url=BASE_URL,
        logo_url=os.environ.get("MESH_LOGO_URL", ""),
    )
    con.close()
    if not ok:
        return RedirectResponse("/admin/edm?err=" + quote(err[:200]), status_code=302)
    return RedirectResponse(f"/admin/edm?saved=1#{slug}", status_code=302)

@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, page: int = 1, saved: int = 0):
    auth.require(request, "owner")
    page_size = 20
    page = max(1, int(page or 1))
    con = db.connect()
    total = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    pages = max(1, (total + page_size - 1) // page_size)
    if page > pages:
        page = pages
    offset = (page - 1) * page_size
    users = [dict(x) for x in con.execute(
        "SELECT id, username, display, role, team, feishu_open_id, created_at FROM users ORDER BY id LIMIT ? OFFSET ?",
        (page_size, offset),
    )]
    con.close()
    return templates.TemplateResponse(
        "users.html",
        ctx(
            request,
            users=users,
            teams=ingest.TEAMS,
            page=page,
            pages=pages,
            total=total,
            page_size=page_size,
            flash_ok="已保存" if saved else None,
        ),
    )

@app.post("/admin/users/save")
def users_save(request: Request, uid: int = Form(0), username: str = Form(""), display: str = Form(""), password: str = Form(""), role: str = Form("viewer"), team: str = Form("")):
    u = auth.require(request, "owner")
    role = (role or "viewer").strip()
    if role not in auth.ROLE_RANK:
        raise HTTPException(400, "未知角色")
    if role == "owner" and auth.role_of(u) != "owner":
        raise HTTPException(403, "只有所有者可以授予所有者角色")
    if role == "dept":
        raise HTTPException(400, "团队负责人已停用，请改为查看者或编辑")
    con = db.connect()
    refreshed = None
    if uid:
        target = con.execute("SELECT username, role FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            con.close()
            raise HTTPException(404, "用户不存在")
        # 非 owner 不得改动 / 降级 owner 账号
        if target["role"] == "owner" and auth.role_of(u) != "owner":
            con.close()
            raise HTTPException(403, "只有所有者可以修改所有者账号")
        if target["role"] == "owner" and role != "owner" and auth.role_of(u) != "owner":
            con.close()
            raise HTTPException(403, "只有所有者可以调整所有者角色")
        # 禁止把自己降到无管理员权限（防锁死）— 仍允许 owner 改他人
        con.execute("UPDATE users SET display=?, role=?, team=? WHERE id=?", (display, role, team or None, uid))
        if password:
            con.execute("UPDATE users SET pw_hash=? WHERE id=?", (db.hash_pw(password), uid))
        row = con.execute(
            "SELECT username, display, role, team FROM users WHERE id=?", (uid,)
        ).fetchone()
        if row and row["username"] == u.get("u"):
            refreshed = {
                "username": row["username"],
                "display": row["display"],
                "role": row["role"],
                "team": row["team"],
                "avatar_url": u.get("a") or "",
            }
    else:
        if not (password or "").strip():
            con.close()
            raise HTTPException(400, "新建用户必须设置密码")
        con.execute(
            "INSERT INTO users(username,display,pw_hash,role,team) VALUES(?,?,?,?,?)",
            (username, display or username, db.hash_pw(password), role, team or None),
        )
    con.commit()
    con.close()
    resp = RedirectResponse("/admin/users?saved=1", status_code=302)
    if refreshed:
        resp.set_cookie(
            auth.COOKIE,
            auth.make_session(refreshed),
            httponly=True,
            samesite="lax",
            secure=_cookie_secure(request),
            max_age=auth.SESSION_MAX_AGE,
        )
    return resp

@app.post("/admin/users/{uid}/delete")
def users_delete(request: Request, uid: int):
    u = auth.require(request, "owner")
    con = db.connect()
    row = con.execute("SELECT username, role FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404)
    if row["username"] in ("admin", "owner") or row["role"] == "owner":
        if auth.role_of(u) != "owner":
            con.close()
            raise HTTPException(403, "只有所有者可以删除所有者或系统账号")
        if row["username"] == "owner" or row["role"] == "owner":
            # 至少保留一个 owner
            n_owners = con.execute("SELECT COUNT(*) c FROM users WHERE role='owner'").fetchone()["c"]
            if n_owners <= 1:
                con.close()
                raise HTTPException(400, "不能删除唯一的所有者账号")
    con.execute("DELETE FROM users WHERE id=?", (uid,))
    con.commit(); con.close()
    return RedirectResponse("/admin/users", status_code=302)

def _prompt_path(name: str) -> Path:
    """仅允许 prompts 目录下的 *.md，防路径穿越。"""
    raw = (name or "").strip()
    if raw.endswith(".md"):
        raw = raw[:-3]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", raw or ""):
        raise HTTPException(400, detail="文件名仅限字母数字、下划线、短横线，且以字母或数字开头")
    base = (BASE / "prompts").resolve()
    p = (base / f"{raw}.md").resolve()
    if p.parent != base:
        raise HTTPException(400, detail="非法路径")
    return p

@app.get("/admin/prompts", response_class=HTMLResponse)
def prompts_page(request: Request, err: str = ""):
    auth.require(request, "owner")
    files = sorted(p.name for p in (BASE / "prompts").glob("*.md"))
    return templates.TemplateResponse(
        "prompts.html",
        ctx(request, files=files, content=None, current=None, flash_err=err or None),
    )

@app.post("/admin/prompts/new")
def prompt_new(request: Request, name: str = Form(...), content: str = Form("")):
    auth.require(request, "owner")
    from urllib.parse import quote
    try:
        p = _prompt_path(name)
    except HTTPException as e:
        return RedirectResponse("/admin/prompts?err=" + quote(str(e.detail)), status_code=302)
    if p.exists():
        return RedirectResponse("/admin/prompts?err=" + quote(f"「{p.name}」已存在，请换一个名字"), status_code=302)
    body = (content or "").strip() or f"# {p.stem}\n\n"
    p.write_text(body, encoding="utf-8")
    return RedirectResponse(f"/admin/prompts/{p.name}", status_code=302)

@app.post("/admin/prompts/upload")
async def prompt_upload(request: Request, overwrite: int = Form(0)):
    """拖拽 / 选择上传 .md 规则文件。"""
    auth.require(request, "owner")
    form = await request.form()
    uploads = form.getlist("files") if hasattr(form, "getlist") else []
    if not uploads:
        one = form.get("files") or form.get("file")
        uploads = [one] if one else []
    if not uploads:
        return JSONResponse({"ok": False, "error": "请选择或拖入 .md 文件"}, status_code=400)

    saved, skipped, errors = [], [], []
    last = None
    for item in uploads:
        if not item or not getattr(item, "filename", None):
            continue
        fname = Path(item.filename).name
        if not fname.lower().endswith(".md"):
            errors.append(f"「{fname}」不是 .md")
            continue
        try:
            p = _prompt_path(fname)
        except HTTPException as e:
            errors.append(f"「{fname}」：{e.detail}")
            continue
        raw = await item.read()
        if len(raw) > 512 * 1024:
            errors.append(f"「{p.name}」超过 512KB")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                errors.append(f"「{p.name}」不是 UTF-8 文本")
                continue
        if p.exists() and not int(overwrite or 0):
            skipped.append(p.name)
            continue
        p.write_text(text, encoding="utf-8")
        saved.append(p.name)
        last = p.name

    wants_json = "application/json" in (request.headers.get("accept") or "")
    if wants_json:
        return JSONResponse({
            "ok": True,
            "saved": saved,
            "skipped": skipped,
            "errors": errors,
            "open": last,
        })
    from urllib.parse import quote
    if errors and not saved:
        return RedirectResponse("/admin/prompts?err=" + quote("；".join(errors)[:400]), status_code=302)
    if last:
        return RedirectResponse(f"/admin/prompts/{last}", status_code=302)
    return RedirectResponse("/admin/prompts", status_code=302)

@app.get("/admin/prompts/{name}", response_class=HTMLResponse)
def prompt_edit(request: Request, name: str, err: str = ""):
    auth.require(request, "owner")
    try:
        p = _prompt_path(name)
    except HTTPException:
        raise HTTPException(404)
    if not p.exists():
        raise HTTPException(404)
    files = sorted(x.name for x in (BASE / "prompts").glob("*.md"))
    return templates.TemplateResponse(
        "prompts.html",
        ctx(request, files=files, content=p.read_text(encoding="utf-8"), current=p.name, flash_err=err or None),
    )

@app.post("/admin/prompts/{name}")
def prompt_save(request: Request, name: str, content: str = Form(...)):
    auth.require(request, "owner")
    p = _prompt_path(name)
    if not p.exists():
        raise HTTPException(404)
    p.write_text(content, encoding="utf-8")
    return RedirectResponse(f"/admin/prompts/{p.name}", status_code=302)

@app.post("/admin/prompts/{name}/delete")
def prompt_delete(request: Request, name: str):
    auth.require(request, "owner")
    from urllib.parse import quote
    # 核心管线依赖的文件不允许删，避免一键搞挂抽取
    protected = {
        "00_base_rules.md", "05_owner_attrib.md", "90_output_items.md", "91_output_issue.md",
        "card_team.md", "issue_draft.md", "qa.md",
        *(f"extract_T{i}.md" for i in range(1, 14)),
        "team_gp.md", "team_svbd.md", "team_podcast.md", "team_video.md",
    }
    p = _prompt_path(name)
    if p.name in protected:
        return RedirectResponse(
            f"/admin/prompts/{p.name}?err=" + quote("系统核心提示词不能删除，只能编辑"),
            status_code=302,
        )
    if p.exists():
        p.unlink()
    return RedirectResponse("/admin/prompts", status_code=302)


# ---------------- 期页（放最后，避免吞掉 /admin 等路径） ----------------
@app.get("/{slug}", response_class=HTMLResponse)
def issue_page(request: Request, slug: str, preview: int = 0, edit: int = 0, sync: int = 0):
    if slug in ("favicon.ico",): raise HTTPException(404)
    u = auth.current_user(request)
    # 从读者页点编辑：进入草稿预览，并用当前线上稿铺底，改完后需重新上线
    if edit and not preview and u and auth.ROLE_RANK.get(u["r"], 0) >= auth.ROLE_RANK["editor"]:
        return RedirectResponse(f"/{slug}?preview=1&edit=1&sync=1", status_code=302)
    con = db.connect()
    r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "没有这一期")
    # 仅编辑时铺底草稿；纯预览不写空 {}，避免「查看」把未生成稿写成可渲染假象
    if preview and (edit or sync) and u and auth.ROLE_RANK.get(u["r"], 0) >= auth.ROLE_RANK["editor"]:
        ensure_draft_for_edit(con, r, sync_from_published=bool(sync) and r["status"] == "published")
        con.commit()
        r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    r, data = load_issue(con, slug, published_only=not preview)
    con.close()
    if not r: raise HTTPException(404, "没有这一期")
    if not u: return templates.TemplateResponse("login.html", ctx(request, next=f"/{slug}", error=None, issue=dict(r), debug_panel=False, debug_preset=""))
    if preview and auth.ROLE_RANK.get(u["r"], 0) < auth.ROLE_RANK["editor"]: raise HTTPException(403)
    if data is None: raise HTTPException(404, "本期尚未发布")
    if preview and not db.issue_json_renderable(data):
        from urllib.parse import quote
        return RedirectResponse(
            f"/admin/issue/{slug}?err=" + quote("尚未生成预览，请先在素材页点「生成预览」"),
            status_code=302,
        )
    from .preview_progressive import allows_preview_entry, is_building

    gate_ok = bool(isinstance(data, dict) and data.get("_preview_gate_ok"))
    gate_stale = bool(isinstance(data, dict) and data.get("_preview_gate_stale"))
    preview_building = preview and is_building(data if isinstance(data, dict) else None)
    if preview and not allows_preview_entry(data if isinstance(data, dict) else None):
        from urllib.parse import quote
        return RedirectResponse(
            f"/admin/issue/{slug}?err="
            + quote("草稿尚未通过进预览检查，请重新点「生成预览」（检查通过后才会进入预览页）"),
            status_code=302,
        )
    preview_gate_stale = preview and gate_stale and not gate_ok and not preview_building
    dropped_ungrounded = []
    if isinstance(data, dict):
        dropped_ungrounded = list(data.get("_relations_dropped_ungrounded") or [])[:20]
    # 模板依赖 keywords/plans 等对象；缺省时给空结构，避免半成品草稿炸页
    data.setdefault("kpis", [])
    data.setdefault("relations", [])
    data.setdefault("contacts", [])
    data.setdefault("views", [])
    data.setdefault("gaps", [])
    data.setdefault("data_sources", [])
    data.setdefault("keywords", {})
    data["keywords"].setdefault("groups", [])
    data["keywords"].setdefault("sources", [])
    data.setdefault("plans", {})
    data["plans"].setdefault("groups", [])
    data["plans"].setdefault("sources", [])
    # 进草稿即读者可见：展示按强度排序（保留原下标）；KPI 对齐卡数
    relations_view = []
    try:
        from .relation_display import indexed_relations_for_display, _sync_relation_kpi
        from .issue_verify import sync_kpis_from_data
        relations_view = indexed_relations_for_display(data.get("relations") or [])
        if preview:
            data = sync_kpis_from_data(data)
        else:
            n_pub = sum(
                1
                for x in (data.get("relations") or [])
                if isinstance(x, dict) and (x.get("decision_tier") or "").strip().lower() != "skip"
            )
            _sync_relation_kpi(data, n_pub)
    except Exception:
        relations_view = [
            {"index": i, "rel": r}
            for i, r in enumerate(data.get("relations") or [])
            if isinstance(r, dict)
        ]
    con = db.connect()
    arch = [_enrich_issue(x) for x in con.execute("SELECT slug, period_label, date_end, published_at FROM issues WHERE status='published' ORDER BY date_end DESC LIMIT 12")]
    con.close()
    return templates.TemplateResponse(
        "issue.html",
        ctx(
            request,
            issue=_enrich_issue(r),
            d=data,
            preview=bool(preview),
            archive_list=arch,
            relations_view=relations_view,
            preview_gate_stale=bool(preview_gate_stale),
            preview_building=bool(preview_building),
            dropped_ungrounded=dropped_ungrounded,
        ),
    )

