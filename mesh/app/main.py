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
from . import db, ingest, llm, auth, edm, merge, pipeline, preview_job, qa_structured, search, tokenize
from . import ask_engine, ask_scope, conversation, presets, chunk_index, embeddings, retriever, ask_turn, ask_query
import time

_ASK_CACHE: dict[str, tuple[float, dict]] = {}
_ASK_CACHE_LOCK = threading.Lock()
_ASK_CACHE_TTL = int(os.environ.get("MESH_ASK_CACHE_TTL", "120") or "120")
_ASK_CACHE_MAX = 256
_ASK_RATE: dict[str, list[float]] = {}
_ASK_RATE_LOCK = threading.Lock()
_ASK_RATE_MAX = int(os.environ.get("MESH_ASK_RATE_PER_MIN", "40") or "40")


def _ask_rate_ok(key: str) -> bool:
    """简易 per-user/IP 速率限制。"""
    if _ASK_RATE_MAX <= 0:
        return True
    now = time.time()
    with _ASK_RATE_LOCK:
        hits = [t for t in _ASK_RATE.get(key, []) if now - t < 60]
        if len(hits) >= _ASK_RATE_MAX:
            _ASK_RATE[key] = hits
            return False
        hits.append(now)
        _ASK_RATE[key] = hits[-(_ASK_RATE_MAX * 2):]
    return True

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
        try:
            with db.write_lock():
                con = db.connect()
                try:
                    n_facts = con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"]
                    n_pub = con.execute("SELECT COUNT(*) c FROM issues WHERE status='published'").fetchone()["c"]
                    fact_slugs = con.execute("SELECT COUNT(DISTINCT issue_slug) c FROM entity_team_facts").fetchone()["c"]
                    if n_pub and (not n_facts or fact_slugs < n_pub):
                        print("[mesh] backfilling entity_team_facts…", flush=True)
                        db.reindex_all_entity_facts(con)
                        con.commit()
                        _ASK_CACHE.clear()
                    try:
                        n_if = con.execute("SELECT COUNT(*) c FROM item_facts").fetchone()["c"]
                    except Exception:
                        n_if = 0
                    if n_pub and not n_if:
                        print("[mesh] backfilling item_facts…", flush=True)
                        from . import item_facts
                        item_facts.reindex_all_item_facts(con)
                        con.commit()
                        _ASK_CACHE.clear()
                    try:
                        n_ie = con.execute("SELECT COUNT(*) c FROM item_entity_facts").fetchone()["c"]
                    except Exception:
                        n_ie = 0
                    if n_pub and n_if and not n_ie:
                        print("[mesh] backfilling item_entity_facts…", flush=True)
                        from . import item_facts
                        item_facts.reindex_all_item_facts(con)
                        con.commit()
                        _ASK_CACHE.clear()
                    try:
                        con.execute("UPDATE users SET role='editor' WHERE role='dept'")
                        con.execute(
                            "UPDATE cards SET status='approved', reviewer='mesh-migrate', "
                            "reviewed_at=datetime('now') WHERE status IN ('pending','rejected')"
                        )
                        con.commit()
                    except Exception:
                        pass
                    n_fts = con.execute("SELECT COUNT(*) c FROM search_fts").fetchone()["c"]
                    n_iss = con.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"]
                    need = db.search_needs_reindex(con) or (n_iss and not n_fts)
                    if need:
                        print("[mesh] rebuilding search_fts…", flush=True)
                        db.reindex_all_search(con)
                        db.clear_search_reindex_flag(con)
                        con.commit()
                        _ASK_CACHE.clear()
                        print("[mesh] search rebuild done", flush=True)
                    try:
                        n_chunk = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
                    except Exception:
                        n_chunk = 0
                    if n_pub and n_if and not n_chunk:
                        print("[mesh] backfilling chunk_index…", flush=True)
                        chunk_index.rebuild_all(con)
                        con.commit()
                        n_chunk = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
                        _ASK_CACHE.clear()
                    try:
                        n_emb = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
                    except Exception:
                        n_emb = 0
                    if n_pub and n_chunk and n_emb < n_chunk and embeddings.is_configured():
                        print("[mesh] backfilling chunk embeddings…", flush=True)
                        chunk_index.embed_all_missing(con)
                        con.commit()
                        _ASK_CACHE.clear()
                    elif n_pub and n_chunk and n_emb < n_chunk:
                        print(f"[mesh] warn: chunk_embeddings={n_emb}/{n_chunk} — configure MESH_EMBED_* for vector search", flush=True)
                    try:
                        presets.run_due_pushes(con)
                        con.commit()
                    except Exception:
                        pass
                    try:
                        pruned = db.prune_old_logs(con)
                        if any(pruned.values()):
                            con.commit()
                    except Exception:
                        pass
                finally:
                    con.close()
        except Exception as e:
            print(f"[mesh] background maintenance failed: {e}", flush=True)

    import threading
    threading.Thread(target=_bg_maintenance, daemon=True, name="mesh-maint").start()


@app.get("/healthz")
def healthz():
    """运维探活 + 关键模块是否齐（不碰业务数据）。"""
    info = {
        "ok": True,
        "schema_version": db.SCHEMA_VERSION,
        "vector_enabled": embeddings.is_configured(),
        "db_team_alias_map": hasattr(db, "team_alias_map") or hasattr(db, "_team_alias_map"),
        "db_normalize_team": hasattr(db, "normalize_team"),
        "qa_structured": hasattr(qa_structured, "parse_intent"),
        "search_fts": hasattr(search, "fts_search"),
        "tokenize": hasattr(tokenize, "build_match_query"),
        "ask_stream_llm": hasattr(llm, "answer_question_stream"),
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
        if ci > 0 and ce < ci and not embeddings.is_configured():
            info["embeddings_degraded"] = True
            info["embeddings_hint"] = "configure MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL"
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

def ensure_draft_for_edit(con, issue_row, *, sync_from_published: bool = False) -> str:
    """编辑只写草稿。无可用草稿时用线上稿铺底；已有可渲染草稿时不因 sync 覆盖，避免丢改。"""
    pub = issue_row["published_json"] or ""
    draft = issue_row["draft_json"] or ""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if sync_from_published and pub.strip() and not db.issue_json_renderable(draft):
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
    return r, data

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
def login_page(request: Request, next: str = "/"):
    target = auth.normalize_next(next)
    if auth.current_user(request):
        return RedirectResponse(target, status_code=302)
    return templates.TemplateResponse("login.html", ctx(request, next=target, error=None))

@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
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
    except auth.FeishuLoginError as e:
        return templates.TemplateResponse("login.html", ctx(request, next=target, error=e.user_message))
    except Exception:
        return templates.TemplateResponse("login.html", ctx(request, next=target, error="飞书登录暂时不可用，请稍后重试。"))
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(auth.COOKIE, auth.make_session(u), httponly=True, samesite="lax",
                    secure=_cookie_secure(request), max_age=auth.SESSION_MAX_AGE)
    return resp

@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request):
    auth.require(request, "viewer")
    con = db.connect()
    rows = con.execute("SELECT slug, period_label, date_start, date_end, published_at FROM issues WHERE status='published' ORDER BY date_end DESC").fetchall()
    con.close()
    return templates.TemplateResponse("archive.html", ctx(request, issues=[dict(r) for r in rows]))

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
        try:
            ans = ask_turn.generate_answer(q, turn["prepared"], turn["history"])
        except Exception:
            return {k: v for k, v in turn["prepared"].items() if k not in ("contexts", "direct_answer", "preview")} | {
                "answer": "AI 问答暂不可用，请稍后重试。",
                "session_id": turn["sess"]["id"],
            }
        return ask_turn.complete_turn(
            con, user=user, scope=scope, q=q,
            sess=turn["sess"], prepared=turn["prepared"], ans=ans,
            cache_key=turn["cache_key"], use_cache=turn["use_cache"],
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
    history = conversation.recent_messages(con, sess["id"], limit=4)
    try:
        ans = llm.answer_question(q, prepared["contexts"], mode=prepared["mode"], history=history)
    except Exception:
        con.close()
        return {"answer": "AI 问答暂不可用", "question": q}
    conversation.append_turn(con, sess["id"], user_text=q, assistant_text=ans, mode=prepared.get("mode", ""))
    con.commit()
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
        try:
            user = auth.ask_user(con, cookie)
            if not user:
                yield sse({"type": "error", "message": "未登录"})
                con.close()
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
                con.close()
                return

            prepared = turn["prepared"]
            sess = turn["sess"]
            history = turn["history"]
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            con.close()
            return

        meta = {k: v for k, v in prepared.items() if k not in ("contexts", "direct_answer", "preview")}
        meta["type"] = "meta"
        meta["session_id"] = sess["id"]
        yield sse(meta)

        parts: list[str] = []
        try:
            for chunk in ask_turn.generate_answer_stream(q, prepared, history):
                if not chunk:
                    continue
                parts.append(chunk)
                yield sse({"type": "token", "text": chunk})
        except Exception:
            con.close()
            yield sse({"type": "error", "message": "AI 问答暂不可用，请稍后重试。"})
            return

        ans = "".join(parts) if parts else (prepared.get("direct_answer") or "")
        ask_turn.complete_turn(
            con, user=user, scope=scope, q=q,
            sess=sess, prepared=prepared, ans=ans,
            cache_key=turn["cache_key"], use_cache=turn["use_cache"],
            cache_put=_ask_cache_put,
        )
        con.close()
        yield sse({"type": "done", "answer": ans})

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
    auth.require(request, "editor")
    con = db.connect()
    n = db.reindex_all_entity_facts(con)
    db.reindex_all_search(con)
    c = con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"]
    chunks = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
    try:
        embs = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
    except Exception:
        embs = -1
    con.commit(); con.close()
    _ASK_CACHE.clear()
    return {"ok": True, "issues": n, "facts": c, "chunks": chunks, "embeddings": embs,
            "embed_configured": embeddings.is_configured()}


@app.post("/admin/reindex_search")
def admin_reindex_search(request: Request):
    """全量重建 FTS（中文 toks）+ 事实表。"""
    auth.require(request, "editor")
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
    con.commit(); con.close()
    _ASK_CACHE.clear()
    return _flash_redirect(f"/admin/issue/{slug}?step=4", "已重建搜索/问答索引（含条目与实体）。")

@app.post("/admin/embed_backfill")
def admin_embed_backfill(request: Request):
    """回填 chunk 向量（需配置 MESH_EMBED_*）。"""
    auth.require(request, "editor")
    if not embeddings.is_configured():
        raise HTTPException(400, "未配置 MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL")
    con = db.connect()
    n_before = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
    chunk_index.embed_all_missing(con)
    n_after = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
    con.commit()
    con.close()
    _ASK_CACHE.clear()
    return {"ok": True, "embeddings_before": n_before, "embeddings_after": n_after}


# ---------------- 后台 ----------------
# ---------------- 后台：期管理 ----------------

def _parse_iso_date(s: str | None) -> datetime.date | None:
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _period_label(start: datetime.date, end: datetime.date) -> str:
    """读者页大日期：同年第月日省略年，如 2026.8.15 - 8.21"""
    if start.year == end.year:
        return f"{start.year}.{start.month}.{start.day} - {end.month}.{end.day}"
    return f"{start.year}.{start.month}.{start.day} - {end.year}.{end.month}.{end.day}"


def suggest_next_issue(con) -> dict:
    """按最近一期推下一期默认区间（保持相近跨度；无历史则按本周一～日）。"""
    row = con.execute(
        "SELECT date_start, date_end, version FROM issues ORDER BY date_end DESC, id DESC LIMIT 1"
    ).fetchone()
    if row:
        prev_end = _parse_iso_date(row["date_end"]) or datetime.date.today()
        prev_start = _parse_iso_date(row["date_start"]) or (prev_end - datetime.timedelta(days=6))
        span = max(1, (prev_end - prev_start).days)
        start = prev_end + datetime.timedelta(days=1)
        end = start + datetime.timedelta(days=span)
        version = (row["version"] or "v1.4").strip() or "v1.4"
    else:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=today.weekday())
        end = start + datetime.timedelta(days=6)
        version = "v1.4"
    return {
        "slug": end.isoformat(),
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "period_label": _period_label(start, end),
        "version": version,
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    """管理后台入口：先看各期列表，再选一期进入制作。"""
    auth.require(request, "editor")
    return RedirectResponse("/admin/issues", status_code=302)

@app.get("/admin/issues", response_class=HTMLResponse)
def admin_issue_list(request: Request, err: str = ""):
    u = auth.require(request, "editor")
    con = db.connect()
    issues = [dict(r) for r in con.execute(
        "SELECT id, slug, period_label, status, updated_at, published_at, draft_json FROM issues ORDER BY date_end DESC"
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
def issue_new(request: Request, slug: str = Form(...), date_start: str = Form(...), date_end: str = Form(...), period_label: str = Form(...), version: str = Form("v1.4")):
    auth.require(request, "editor")
    slug = (slug or "").strip()
    date_start = (date_start or "").strip()
    date_end = (date_end or "").strip()
    period_label = (period_label or "").strip()
    version = (version or "v1.4").strip() or "v1.4"
    if not slug or not date_start or not date_end or not period_label:
        raise HTTPException(400, "期号与日期不能为空")
    if date_start > date_end:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    con = db.connect()
    exists = con.execute("SELECT 1 FROM issues WHERE slug=?", (slug,)).fetchone()
    if exists:
        suggest = suggest_next_issue(con)
        issues = [dict(r) for r in con.execute(
            "SELECT id, slug, period_label, status, updated_at, published_at, draft_json FROM issues ORDER BY date_end DESC"
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
                flash_err=f"期号「{slug}」已存在，请换一个（常用结束日）"),
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
    db.delete_issue(con, slug)
    con.commit()
    con.close()
    try:
        from . import pipeline, preview_job
        with pipeline._LOCK:
            pipeline.JOBS.pop(slug, None)
        with preview_job._LOCK:
            preview_job.JOBS.pop(slug, None)
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
    con.close()
    p = auth.perms(u)
    # 0 放入素材 · 1 挖掘 · 2 审校 · 3 确认上线
    start_step = 0 if step is None else max(0, min(3, int(step)))
    return templates.TemplateResponse("issue_console.html", ctx(request, issue=dict(r), sources=sources, n_items=counts["n"] or 0, n_blocked=counts["b"] or 0,
                                                              cards=cards, edits=edits, mails=mails, versions=versions, n_merged=n_merged,
                                                              n_noowner=n_noowner,
                                                              has_draft=has_draft, draft_stale=draft_stale,
                                                              sources_need_mine=bool(unextracted),
                                                              role_cn=p["role_cn"],
                                                              steps=pipeline.steps_for_ui(), start_step=start_step,
                                                              flash_err=err,
                                                              stypes=ingest.SOURCE_TYPES, teams=ingest.TEAMS,
                                                              embeddings_configured=embeddings.is_configured(),
                                                              draft=(r["draft_json"] or ""), external_feeds=os.environ.get("EXTERNAL_FEEDS", ",".join(ingest.EXTERNAL_FEEDS_DEFAULT))))


@app.post("/admin/issue/{slug}/pipeline/start")
def pipeline_start(request: Request, slug: str, force: int = 0):
    auth.require(request, "editor")
    pipeline.start(slug, force=bool(force))
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
        guessed = ingest.infer_source_meta(filename, text, default_team=default_team)
        use_stype = (stype or "").strip() or guessed["stype"]
        use_team = ingest.canonical_team((team or "").strip() or guessed["team"])
        use_channel = (channel or "").strip() or guessed["channel"]
        use_title = (title or "").strip() or guessed["title"] or filename
        meta["inferred"] = guessed
        cur = con.execute(
            "INSERT INTO sources(issue_id,stype,team,title,filename,raw_path,text,meta,channel) VALUES(?,?,?,?,?,?,?,?,?)",
            (r["id"], use_stype, use_team, use_title, filename, str(path), text, json.dumps(meta, ensure_ascii=False), use_channel),
        )
        sources_out.append({
            "id": cur.lastrowid,
            "title": use_title,
            "stype": use_stype,
            "team": use_team,
            "channel": use_channel,
            "n": len(text or ""),
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
    con.execute(
        "INSERT INTO sources(issue_id,stype,team,title,filename,text,meta,channel) VALUES(?,?,?,?,?,?,?,?)",
        (r["id"], use_stype, use_team, use_title, "", text, json.dumps({"inferred": guessed}, ensure_ascii=False), use_channel),
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
    if stype and stype not in ingest.SOURCE_TYPES:
        raise HTTPException(400, "未知数据类型")
    if team:
        team = ingest.canonical_team(team)
        if team not in ingest.TEAMS:
            raise HTTPException(400, "未知团队")
    con = db.connect()
    s = con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not s:
        con.close()
        raise HTTPException(404, "来源不存在")
    new_stype = stype or s["stype"]
    new_team = ingest.canonical_team(team or s["team"] or "")
    new_channel = channel or (s["channel"] or "manual")
    if new_channel not in ("manual", "aggregator"):
        new_channel = "manual"
    from .aggregator import AGG_TEAM, is_mixed_source
    team_changed = bool(team) and team != (s["team"] or "")
    stype_changed = bool(stype) and stype != (s["stype"] or "")
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
            (new_stype, new_team, new_channel, new_team, sid),
        )
    else:
        fill_team = new_team if new_team != AGG_TEAM else None
        con.execute(
            "UPDATE items SET stype=?, team=?, channel=?, owner_team=COALESCE(NULLIF(owner_team,''), ?) WHERE source_id=?",
            (new_stype, new_team, new_channel, fill_team, sid),
        )
    db.mark_draft_stale(con, s["issue_id"])
    con.commit()
    con.close()
    return {"ok": True, "need_reextract": need_reextract}


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
    con = db.connect(); s = con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    iss = con.execute("SELECT slug, date_start, date_end, period_label FROM issues WHERE id=?", (s["issue_id"],)).fetchone()
    slug = iss["slug"]
    try:
        items, split_meta = llm.extract_source(
            s["stype"], s["team"], s["title"], s["text"] or "",
            period_start=iss["date_start"] or "",
            period_end=iss["date_end"] or "",
            period_label=iss["period_label"] or "",
            channel=s["channel"] or "manual",
        )
    except Exception as e:
        con.close()
        return _flash_redirect(f"/admin/source/{sid}", f"抽取失败：{e}")
    con.execute("DELETE FROM items WHERE source_id=?", (sid,))
    from .aggregator import merge_source_meta, resolve_item_owner
    for it in items:
        item_stype = it.get("item_stype") or s["stype"]
        owner = resolve_item_owner(it, source_team=s["team"] or "")
        con.execute("""INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,source_label,pointer,blocked,owner_team,channel,source_labels)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (s["issue_id"], sid, it["team"], item_stype, it["zone"], it["level"], it["kind"], it["text"], json.dumps(it["entities"], ensure_ascii=False),
                     json.dumps(it["roles"], ensure_ascii=False), json.dumps(it["signals"], ensure_ascii=False), it["source_label"], it["pointer"], it["blocked"],
                     owner, s["channel"] or "manual",
                     json.dumps([it["source_label"]] if it.get("source_label") else [], ensure_ascii=False)))
        for name in it["entities"]:
            if name: con.execute("INSERT INTO entities(name,kind,first_issue) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING", (name, "auto", slug))
    meta = merge_source_meta(s["meta"], split_meta)
    con.execute("UPDATE sources SET extracted=1, meta=? WHERE id=?", (meta, sid))
    issue_id = s["issue_id"]
    st = con.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()
    if st and st["status"] == "published":
        db.mark_draft_stale(con, issue_id)
        db.snapshot_published_items(con, issue_id)
        db.reindex_issue(con, issue_id)
    con.commit(); con.close()
    _ASK_CACHE.clear()
    if st and st["status"] == "published":
        return _flash_redirect(
            f"/admin/source/{sid}",
            "已重新抽取并更新搜索/问答索引（读者页正文不变，需重新上线才更新页面）。",
        )
    return RedirectResponse(f"/admin/source/{sid}", status_code=302)

@app.post("/admin/issue/{slug}/extract_all")
def extract_all(request: Request, slug: str):
    auth.require(request, "editor")
    con = db.connect(); r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
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
    """根据全部有效条目（按 owner_team）生成周报草稿 JSON。"""
    merge.apply_merge(con, r["id"])
    teams = _item_teams_for_draft(con, r["id"])
    q = (
        "SELECT owner_team AS team, stype, zone, level, kind, text, entities, roles, signals, "
        "source_label, source_labels, channel FROM items WHERE issue_id=? AND blocked=0 "
        "AND merged_into IS NULL AND (owner_team IN (%s) OR stype IN ('T5','T7'))"
        % (",".join("?" * len(teams)) or "''")
    )
    rows = [dict(x) for x in con.execute(q, (r["id"], *teams))]
    internal = [x for x in rows if x["stype"] != "T7"]
    external = [x for x in rows if x["stype"] == "T7"]
    names = []
    for x in internal:
        for n in json.loads(x["entities"] or "[]"):
            e = con.execute("SELECT first_issue FROM entities WHERE name=?", (n,)).fetchone()
            if not e or e["first_issue"] == slug:
                names.append(n)
    names = list(dict.fromkeys(names))[:40]
    prev = con.execute("SELECT published_json FROM issues WHERE status='published' AND date_end<? ORDER BY date_end DESC LIMIT 1", (r["date_start"],)).fetchone()
    prev_summary = ""
    if prev and prev["published_json"]:
        pj = json.loads(prev["published_json"])
        prev_summary = pj.get("lead", "") + " " + " / ".join(x.get("title", "") for x in pj.get("relations", []))
    data = llm.build_issue_draft(dict(r), internal, names, external, prev_summary)
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
    con = db.connect(); r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
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
    con = db.connect(); r = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
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
    con.execute(f"UPDATE issues SET {col}=?, updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), r["id"]))
    con.execute("INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)", (r["id"], u["u"], f"{col}:{path}", json.dumps(before, ensure_ascii=False)[:5000] if before is not None else "", json.dumps(value, ensure_ascii=False)[:5000]))
    con.commit(); con.close()
    return {"ok": True}

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
            "title": "新卡片（双击编辑）",
            "body": "只写事实与来源，不写建议。",
            "details": [],
            "sources": ["双击填写来源"],
            "teams": ["团队"],
        }
        data.setdefault("relations", []).append(card)
        section = "relations"
        idx = len(data["relations"]) - 1
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
    return {"ok": True, "index": idx, "section": section, "card": card}

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
    return {"ok": True, "index": len(ref) - 1, "path": path, "kind": kind, "item": item}

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
    """管理员可以准备到这一步，但只能提交；确认上线是所有者的权限。"""
    u = auth.require(request, "editor")
    con = db.connect(); r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    con.execute("INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
                (r["id"], u["u"], "request_publish", "", "已提交，等待所有者确认"))
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}?requested=1", status_code=302)


@app.post("/admin/issue/{slug}/publish")
def publish(request: Request, slug: str, confirm: str = Form("")):
    """上线：仅 owner；写入不可改版本记录。确认由前端对话框完成，不再要求手打文字。"""
    u = auth.require(request, "owner")
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
    blockers = publish_blockers(con, r["id"], r["draft_json"] or "")
    if blockers:
        msg = "上线前检查未通过：\n" + "\n".join(f"· {x}" for x in blockers)
        con.close()
        return fail(msg)
    data = json.loads(r["draft_json"])
    data.pop("_stale", None)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = json.dumps(data, ensure_ascii=False)
    con.execute(
        "UPDATE issues SET published_json=?, draft_json=?, status='published', published_at=?, updated_at=? WHERE id=?",
        (payload, payload, now, now, r["id"]),
    )
    db.register_entities(con, data, slug)
    db.snapshot_published_items(con, r["id"])
    db.reindex_issue(con, r["id"])
    _ASK_CACHE.clear()
    seq = con.execute("SELECT COUNT(*) c FROM versions WHERE issue_id=?", (r["id"],)).fetchone()["c"] + 1
    cards_out = len(data.get("relations", [])) + sum(len(g.get("items", [])) for c in data.get("contacts", []) for g in c.get("groups", []))
    edited = con.execute("SELECT COUNT(*) c FROM edits WHERE issue_id=? AND target LIKE '%.%'", (r["id"],)).fetchone()["c"]
    con.execute("INSERT INTO versions(issue_id,version,at,by_user,cards_out,edited_count,url,snapshot_json) VALUES(?,?,?,?,?,?,?,?)",
                (r["id"], f"v{seq}", now, u.get("d") or u["u"], cards_out, edited,
                 f"{os.environ.get('MESH_BASE_URL','').rstrip('/')}/{slug}", json.dumps(data, ensure_ascii=False)))
    con.execute("INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)", (r["id"], u["u"], "publish", "", f"v{seq}"))
    con.commit(); con.close()
    if wants_json:
        return JSONResponse({"ok": True, "slug": slug, "status": "published", "published_at": now})
    return RedirectResponse(f"/{slug}?published=1", status_code=302)

@app.post("/admin/issue/{slug}/unpublish")
def unpublish(request: Request, slug: str):
    auth.require(request, "owner")
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

@app.get("/admin/issue/{slug}/edm", response_class=HTMLResponse)
def edm_preview(request: Request, slug: str):
    auth.require(request, "editor")
    con = db.connect(); r, data = load_issue(con, slug, published_only=False); con.close()
    h, _ = edm.render_edm(dict(r), data or {}, BASE_URL, os.environ.get("MESH_LOGO_URL", ""))
    return HTMLResponse(h)

@app.post("/admin/issue/{slug}/edm/send")
def edm_send(request: Request, slug: str, to: str = Form(...), test: int = Form(1)):
    auth.require(request, "editor")
    con = db.connect(); r, data = load_issue(con, slug, published_only=False)
    if not r:
        con.close()
        raise HTTPException(404)
    if not int(test) and r["status"] != "published":
        con.close()
        return _flash_redirect(f"/admin/issue/{slug}", "正式发送仅限已上线的期；未上线请用「测试」或先确认上线。")
    h, t = edm.render_edm(dict(r), data or {}, BASE_URL, os.environ.get("MESH_LOGO_URL", ""))
    addrs = [a.strip() for a in re.split(r"[,\s;]+", to) if a.strip()]
    subject = ("【测试】" if test else "") + f"GeekPark Mesh · 周报 · {r['period_label']}（内部 · {r['version']}）"
    ok, err = edm.send_mail(addrs, subject, h, t)
    con.execute("INSERT INTO mail_log(issue_id,to_addr,subject,ok,error) VALUES(?,?,?,?,?)", (r["id"], ",".join(addrs), subject, 1 if ok else 0, err))
    con.commit(); con.close()
    return RedirectResponse(f"/admin/issue/{slug}", status_code=302)

@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, page: int = 1, saved: int = 0):
    auth.require(request, "admin")
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
    u = auth.require(request, "admin")
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
    u = auth.require(request, "admin")
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
    auth.require(request, "admin")
    files = sorted(p.name for p in (BASE / "prompts").glob("*.md"))
    return templates.TemplateResponse(
        "prompts.html",
        ctx(request, files=files, content=None, current=None, flash_err=err or None),
    )

@app.post("/admin/prompts/new")
def prompt_new(request: Request, name: str = Form(...), content: str = Form("")):
    auth.require(request, "admin")
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
    auth.require(request, "admin")
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
    auth.require(request, "admin")
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
    auth.require(request, "admin")
    p = _prompt_path(name)
    if not p.exists():
        raise HTTPException(404)
    p.write_text(content, encoding="utf-8")
    return RedirectResponse(f"/admin/prompts/{p.name}", status_code=302)

@app.post("/admin/prompts/{name}/delete")
def prompt_delete(request: Request, name: str):
    auth.require(request, "admin")
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
    if not u: return templates.TemplateResponse("login.html", ctx(request, next=f"/{slug}", error=None, issue=dict(r)))
    if preview and auth.ROLE_RANK.get(u["r"], 0) < auth.ROLE_RANK["editor"]: raise HTTPException(403)
    if data is None: raise HTTPException(404, "本期尚未发布")
    if preview and not db.issue_json_renderable(data):
        from urllib.parse import quote
        return RedirectResponse(
            f"/admin/issue/{slug}?err=" + quote("尚未生成预览，请先在素材页点「生成预览」"),
            status_code=302,
        )
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
    con = db.connect()
    arch = [dict(x) for x in con.execute("SELECT slug, period_label, date_end FROM issues WHERE status='published' ORDER BY date_end DESC LIMIT 12")]
    con.close()
    return templates.TemplateResponse("issue.html", ctx(request, issue=dict(r), d=data, preview=bool(preview), archive_list=arch))

