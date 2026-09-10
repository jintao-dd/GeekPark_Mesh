"""角色权限矩阵。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth


def _u(role: str):
    return {"u": role, "r": role, "d": role}


def test_admin_can_publish_not_edm():
    p = auth.perms(_u("admin"))
    assert p["publish"] is True
    assert p["edm"] is False
    assert p["sysadmin"] is False
    assert p["write"] is True


def test_owner_has_all_ops_nav():
    p = auth.perms(_u("owner"))
    assert p["publish"] is True
    assert p["edm"] is True
    assert p["sysadmin"] is True


def test_debug_user_default():
    assert auth.is_debug_user("杜锦涛", "fs_abc") is True
    assert auth.is_debug_user("张三", "fs_abc") is False


def test_password_login_enabled_respects_flag_and_prod(monkeypatch):
    monkeypatch.delenv("MESH_ALLOW_PASSWORD_LOGIN", raising=False)
    monkeypatch.setenv("MESH_ENV", "staging")
    assert auth.password_login_enabled() is False

    monkeypatch.setenv("MESH_ALLOW_PASSWORD_LOGIN", "1")
    monkeypatch.setenv("MESH_ENV", "staging")
    assert auth.password_login_enabled() is True

    monkeypatch.setenv("MESH_ENV", "production")
    assert auth.password_login_enabled() is False

    monkeypatch.setenv("MESH_ENV", "prod")
    assert auth.password_login_enabled() is False


def test_debug_perms_with_role_override():
    u = {"u": "u1", "r": "admin", "real_r": "viewer", "d": "杜锦涛", "debug_r": "admin"}
    p = auth.perms(u)
    assert p["publish"] is True
    assert p["debug"] is True
    assert p["real_role"] == "viewer"


def test_editor_cannot_publish_or_edm():
    p = auth.perms(_u("editor"))
    assert p["publish"] is False
    assert p["edm"] is False
    assert p["write"] is True
