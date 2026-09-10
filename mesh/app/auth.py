"""GeekPark Mesh · 认证与角色（本地账号 + 可选飞书 OAuth）"""
import os, time, logging
from itsdangerous import URLSafeSerializer, BadSignature
from fastapi import Request, HTTPException
from . import db

SECRET = os.environ.get("MESH_SECRET", "").strip()
_WEAK_SECRETS = {"", "change-me-please", "local-dev-secret-change-me-1234567890", "secret", "mesh"}
_BASE = (os.environ.get("MESH_BASE_URL") or "").strip().lower()
_IS_PROD = (
    (os.environ.get("MESH_ENV") or "").strip().lower() in ("prod", "production")
    or _BASE.startswith("https://")
)
if SECRET in _WEAK_SECRETS or len(SECRET) < 16:
    msg = "MESH_SECRET 未设置或过弱：会话可被伪造。生产环境必须设置强随机串。"
    if _IS_PROD:
        raise RuntimeError(msg + "（已拒绝启动）")
    import warnings
    warnings.warn(msg, RuntimeWarning, stacklevel=1)
    if not SECRET:
        SECRET = "change-me-please"
_ser = URLSafeSerializer(SECRET, salt="mesh-session")
_state_ser = URLSafeSerializer(SECRET, salt="mesh-feishu-state")
COOKIE = "mesh_session"
DEBUG_PRESET_COOKIE = "mesh_debug_preset"
SESSION_MAX_AGE = 60 * 60 * 24 * 14
FEISHU_STATE_TTL = 60 * 10
_log = logging.getLogger("mesh.auth")
# owner 最高；admin 可确认上线/撤回；EDM/账号/提示词仅 owner
ROLE_RANK = {"viewer": 1, "dept": 2, "editor": 3, "admin": 4, "owner": 5}
ROLE_CN = {"owner": "所有者", "admin": "管理员", "editor": "编辑", "dept": "编辑（已迁移）", "viewer": "查看者"}


def _csv_set(env_key: str) -> set[str]:
    return {x.strip() for x in os.environ.get(env_key, "").split(",") if x.strip()}

def role_of(user) -> str:
    if not user:
        return ""
    return user.get("r") or user.get("role") or ""


def rank_of(user) -> int:
    return ROLE_RANK.get(role_of(user), 0)


def at_least(user, min_role: str) -> bool:
    return rank_of(user) >= ROLE_RANK.get(min_role, 99)


def perms(user) -> dict:
    """前端/模板能力开关；后端仍须 require() 再验一次。"""
    r = role_of(user)
    write = at_least(user, "editor")
    is_owner = r == "owner"
    can_publish = r in ("admin", "owner")
    debug = bool(user and is_debug_user(user.get("d") or "", user.get("u") or ""))
    return {
        "role": r,
        "role_cn": ROLE_CN.get(r, r),
        "real_role": (user or {}).get("real_r") or r,
        "debug": debug,
        "read": at_least(user, "viewer"),
        "console": at_least(user, "editor"),
        "write": write,
        "publish": can_publish,
        "edm": is_owner,
        "sysadmin": is_owner,
        "raw_view": at_least(user, "admin"),
        "delete_draft": write,
        "delete_published": is_owner,
    }


def can_delete_issue(user, status: str) -> bool:
    if (status or "") == "published":
        return role_of(user) == "owner"
    return at_least(user, "editor")


def is_debug_user(display: str = "", username: str = "") -> bool:
    """调试模式白名单（默认杜锦涛；可用 MESH_DEBUG_USERS 追加）。"""
    names = _csv_set("MESH_DEBUG_USERS") or {"杜锦涛"}
    blob = f"{display or ''}{username or ''}"
    return any(n and n in blob for n in names)


def make_session(user: dict, debug_role: str = "") -> str:
    payload = {
        "u": user["username"],
        "r": user["role"],
        "t": user.get("team"),
        "d": user.get("display"),
        "a": user.get("avatar_url"),
        "ts": int(time.time()),
    }
    dr = (debug_role or "").strip()
    if dr and dr in ROLE_RANK:
        payload["dr"] = dr
    return _ser.dumps(payload)

def read_session(request: Request):
    tok = request.cookies.get(COOKIE)
    if not tok: return None
    try:
        s = _ser.loads(tok)
        if time.time() - s.get("ts", 0) > SESSION_MAX_AGE: return None  # 14 天
        return s
    except BadSignature:
        return None

def current_user(request: Request):
    """读 cookie 后与库中角色对齐，避免降权后旧 cookie 仍越权。"""
    s = read_session(request)
    if not s:
        return None
    try:
        con = db.connect()
        try:
            row = con.execute(
                "SELECT username, role, team, display, avatar_url FROM users WHERE username=?",
                (s.get("u"),),
            ).fetchone()
        except Exception:
            # 旧库缺 avatar_url 时兜底查询；仍以库为准，不退回 cookie 角色
            row = con.execute(
                "SELECT username, role, team, display FROM users WHERE username=?",
                (s.get("u"),),
            ).fetchone()
        finally:
            con.close()
    except Exception:
        # DB 不可用时 fail-closed：宁可不认登录，也不信过期 cookie 角色
        return None
    if not row:
        return None
    display = row["display"] or s.get("d") or ""
    real_role = row["role"]
    effective = real_role
    dr = (s.get("dr") or "").strip()
    if dr and dr in ROLE_RANK and is_debug_user(display, row["username"]):
        effective = dr
    return {
        "u": row["username"],
        "r": effective,
        "real_r": real_role,
        "debug_r": dr or None,
        "t": row["team"],
        "d": display,
        "a": (row["avatar_url"] if "avatar_url" in row.keys() else None) or s.get("a"),
        "ts": s.get("ts"),
    }

def require(request: Request, min_role: str = "viewer"):
    s = current_user(request)
    if not s: raise HTTPException(status_code=401, detail="未登录")
    if ROLE_RANK.get(s["r"], 0) < ROLE_RANK[min_role]: raise HTTPException(status_code=403, detail="权限不足")
    return s


def ask_user(con, session: dict | None) -> dict | None:
    """问答/预设 API 用完整用户（含 id）；session 为 require() 返回的 cookie 形态。"""
    if not session:
        return None
    username = session.get("u")
    if not username:
        return None
    row = con.execute(
        "SELECT id, username, display, role, team, feishu_open_id FROM users WHERE username=?",
        (username,),
    ).fetchone()
    if not row:
        return None
    display = row["display"] or row["username"]
    team = row["team"] or ""
    role = (session.get("r") if session else None) or row["role"]
    return {
        "id": row["id"],
        "username": row["username"],
        "display": display,
        "role": role,
        "team": team,
        "feishu_open_id": row["feishu_open_id"] or "",
        "u": row["username"],
        "r": role,
        "t": team,
        "d": display,
    }

def local_login(username: str, password: str):
    con = db.connect()
    r = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    con.close()
    if r and r["pw_hash"] and db.check_pw(password, r["pw_hash"]):
        return dict(r)
    return None

# ---------- 飞书 OAuth（配置 FEISHU_APP_ID / FEISHU_APP_SECRET / MESH_BASE_URL 后启用） ----------
def feishu_enabled() -> bool:
    return bool(os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"))


def password_login_enabled() -> bool:
    """账号密码登录：仅当显式打开，且非生产环境。"""
    flag = (os.environ.get("MESH_ALLOW_PASSWORD_LOGIN") or "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return False
    env = (os.environ.get("MESH_ENV") or "").strip().lower()
    if env in ("prod", "production"):
        return False
    return True

class FeishuLoginError(RuntimeError):
    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail or user_message

def normalize_next(next_path: str | None) -> str:
    target = (next_path or "/").strip()
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target

def make_feishu_state(next_path: str = "/") -> str:
    return _state_ser.dumps({"next": normalize_next(next_path), "ts": int(time.time())})

def read_feishu_state(state: str | None) -> str:
    if not state:
        raise FeishuLoginError("登录状态已失效，请重新点击飞书登录。", "missing state")
    try:
        data = _state_ser.loads(state)
    except BadSignature as e:
        raise FeishuLoginError("登录状态校验失败，请重新点击飞书登录。", f"bad state: {e}") from e
    if time.time() - data.get("ts", 0) > FEISHU_STATE_TTL:
        raise FeishuLoginError("登录已超时，请重新点击飞书登录。", "expired state")
    return normalize_next(data.get("next"))

def feishu_authorize_url(next_path: str = "/") -> str:
    from urllib.parse import urlencode
    base = os.environ.get("MESH_BASE_URL", "http://localhost:8080").rstrip("/")
    redirect = f"{base}/auth/feishu/callback"
    state = make_feishu_state(next_path)
    q = urlencode({
        "app_id": os.environ["FEISHU_APP_ID"],
        "redirect_uri": redirect,
        "scope": "contact:user.base:readonly",
        "state": state,
    })
    return f"https://open.feishu.cn/open-apis/authen/v1/authorize?{q}"

def _request_json(method: str, url: str, *, user_message: str, retries: int = 2, timeout: int = 10, **kwargs) -> dict:
    import requests
    last_detail = ""
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} {resp.reason}", response=resp)
            return resp.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            last_detail = f"{type(e).__name__}: {e}"
            _log.warning("Feishu request failed on attempt %s: %s %s -> %s", attempt + 1, method, url, last_detail)
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise FeishuLoginError(user_message, last_detail) from e
        except requests.HTTPError as e:
            detail = f"HTTP {getattr(e.response, 'status_code', '?')}: {getattr(e.response, 'text', '')[:300]}"
            _log.warning("Feishu request returned HTTP error: %s %s -> %s", method, url, detail)
            raise FeishuLoginError(user_message, detail) from e
        except ValueError as e:
            detail = f"invalid json: {e}"
            _log.warning("Feishu request returned invalid JSON: %s %s -> %s", method, url, detail)
            raise FeishuLoginError(user_message, detail) from e
    raise FeishuLoginError(user_message, last_detail or "unknown error")

def feishu_exchange(code: str) -> dict:
    """code → 用户信息（open_id, name）。失败抛异常。"""
    if not code:
        raise FeishuLoginError("登录信息已失效，请重新点击飞书登录。", "missing code")
    app_id, app_secret = os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"]
    r = _request_json("POST", "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                      json={"app_id": app_id, "app_secret": app_secret},
                      user_message="飞书登录暂时不可用，请稍后重试。")
    app_token = r.get("app_access_token")
    if not app_token:
        raise FeishuLoginError("飞书登录配置校验失败，请稍后重试。", f"missing app_access_token: {r}")
    r2 = _request_json("POST", "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
                       headers={"Authorization": f"Bearer {app_token}", "Content-Type": "application/json"},
                       json={"grant_type": "authorization_code", "code": code},
                       user_message="飞书登录暂时不可用，请稍后重试。")
    utoken = (r2.get("data") or {}).get("access_token")
    if not utoken:
        msg = str(r2.get("msg") or "")
        if "code" in msg.lower() or "grant" in msg.lower():
            raise FeishuLoginError("本次飞书登录已失效，请重新点击登录。", f"invalid user token exchange: {r2}")
        raise FeishuLoginError("飞书登录校验失败，请稍后重试。", f"missing user access_token: {r2}")
    r3 = _request_json("GET", "https://open.feishu.cn/open-apis/authen/v1/user_info",
                       headers={"Authorization": f"Bearer {utoken}"},
                       user_message="飞书用户信息获取失败，请稍后重试。")
    d = r3.get("data") or {}
    if not d.get("open_id"):
        raise FeishuLoginError("飞书用户信息获取失败，请稍后重试。", f"missing open_id: {r3}")
    return {
        "open_id": d["open_id"],
        "name": d.get("name") or d.get("en_name") or "飞书用户",
        "email": d.get("enterprise_email") or d.get("email") or "",
        "avatar_url": d.get("avatar_url") or d.get("avatar_thumb") or d.get("avatar_middle") or d.get("avatar_big") or ""
    }

def feishu_auto_role(info: dict) -> str | None:
    """仅按 open_id 自动授予角色（禁止靠显示名升权）。未命中返回 None。"""
    owner_ids = _csv_set("FEISHU_OWNER_OPEN_IDS")
    admin_ids = _csv_set("FEISHU_ADMIN_OPEN_IDS")
    oid = info.get("open_id") or ""
    if oid and oid in owner_ids:
        return "owner"
    if oid and oid in admin_ids:
        return "admin"
    return None

def upsert_feishu_user(info: dict) -> dict:
    """飞书用户首次登录默认 viewer；命中 open_id 白名单时自动提权。
    已有显示名不会被飞书昵称覆盖；已有更高/自定义角色不会被 open_id 白名单降权，
    仅允许提权到 admin/owner。"""
    auto_role = feishu_auto_role(info)
    default_role = auto_role or "viewer"
    con = db.connect()
    r = con.execute("SELECT * FROM users WHERE feishu_open_id=?", (info["open_id"],)).fetchone()
    if not r:
        uname = "fs_" + info["open_id"][-10:]
        con.execute("INSERT INTO users(username,display,role,feishu_open_id) VALUES(?,?,?,?)",
                    (uname, info["name"], default_role, info["open_id"]))
        con.commit()
        r = con.execute("SELECT * FROM users WHERE feishu_open_id=?", (info["open_id"],)).fetchone()
    else:
        if auto_role and ROLE_RANK.get(auto_role, 0) > ROLE_RANK.get(r["role"] or "", 0):
            con.execute("UPDATE users SET role=? WHERE id=?", (auto_role, r["id"]))
            con.commit()
            r = con.execute("SELECT * FROM users WHERE feishu_open_id=?", (info["open_id"],)).fetchone()
        elif not (r["display"] or "").strip():
            con.execute("UPDATE users SET display=? WHERE id=?", (info["name"], r["id"]))
            con.commit()
            r = con.execute("SELECT * FROM users WHERE feishu_open_id=?", (info["open_id"],)).fetchone()
    con.close()
    return dict(r)
