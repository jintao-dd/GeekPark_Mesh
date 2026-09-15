"""Identity：飞书 open_id + 花名册即可认人，不强制 users OAuth 绑定。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import identity as idmod
from app.agent.models import AgentEnvelope, STATUS_BOUND, STATUS_UNLINKED


class _EmptyUsersCon:
    def execute(self, sql, params=()):
        class R:
            def fetchone(self_inner):
                return None

            def fetchall(self_inner):
                return []

        return R()


def test_roster_open_id_resolves_without_users_row():
    env = AgentEnvelope(
        text="你好",
        channel="feishu_dm",
        feishu_open_id="ou_98bd3b0520cfb6a112fce9f55d948606",  # 赵思琪
    )
    ident = idmod.resolve_identity(_EmptyUsersCon(), env)
    assert ident.status == STATUS_BOUND
    assert ident.display_hint == "赵思琪"
    assert ident.bind_state == "roster_known"
    assert ident.mesh_user_id is None
    assert ident.primary_team  # 海外拓展 → 品牌创意团队
    assert "品牌创意" in (ident.primary_team or "")


def test_unknown_open_id_still_unlinked():
    env = AgentEnvelope(
        text="你好",
        channel="feishu_dm",
        feishu_open_id="ou_totally_unknown_xyz",
    )
    with mock.patch(
        "app.agent.person_resolve.lookup_by_open_id",
        return_value=None,
    ), mock.patch(
        "app.agent.dept_team_map.mapped_teams_for_open_id",
        return_value=[],
    ):
        ident = idmod.resolve_identity(_EmptyUsersCon(), env)
    assert ident.status == STATUS_UNLINKED
    assert ident.bind_state == "unlinked"


def test_zhangpeng_roster_not_conflict_locked():
    env = AgentEnvelope(
        text="你好",
        channel="feishu_dm",
        feishu_open_id="ou_f50030f7ce0b67599a942d63ed824a7d",
    )
    ident = idmod.resolve_identity(_EmptyUsersCon(), env)
    assert ident.status == STATUS_BOUND
    assert ident.display_hint == "张鹏"
    assert ident.primary_team
