"""Hands 身份策略：bot / user / either。

对齐飞书 CLI 最佳实践：个人 context 用 UAT，组织/应用能力用 TAT。
"""
from __future__ import annotations

from enum import Enum


class IdentityNeed(str, Enum):
    BOT = "bot"  # 仅应用身份
    USER = "user"  # 必须个人授权
    EITHER = "either"  # 有 UAT 更好，无则 bot 降级
    USER_PREFERRED = "user_preferred"  # 强烈建议 UAT；无则引导授权（不静默空结果装成功）


# feishu.search 各 resource_type
SEARCH_IDENTITY: dict[str, IdentityNeed] = {
    "group": IdentityNeed.BOT,
    "member": IdentityNeed.BOT,
    "user": IdentityNeed.BOT,
    "directory": IdentityNeed.BOT,
    "folder": IdentityNeed.EITHER,
    "wiki": IdentityNeed.EITHER,
    "doc": IdentityNeed.EITHER,  # drive+search 可用 bot；个人私有盘需 UAT
    "message": IdentityNeed.EITHER,
    "calendar": IdentityNeed.USER_PREFERRED,  # 「我的日程」细节靠 UAT；bot 只能 freebusy 凑合
}

# 具名工具
TOOL_IDENTITY: dict[str, IdentityNeed] = {
    "feishu.search": IdentityNeed.EITHER,  # 再按 resource_type 细分
    "feishu.doc.get": IdentityNeed.EITHER,
    "feishu.doc.create": IdentityNeed.EITHER,
    "feishu.calendar.list": IdentityNeed.USER_PREFERRED,
    "feishu.calendar.create": IdentityNeed.USER,
    "feishu.calendar.propose": IdentityNeed.EITHER,  # 群成员 bot + 忙闲 bot；本人详细 agenda 仍要 UAT
    "feishu.im.send": IdentityNeed.BOT,
    "feishu.discuss.summary": IdentityNeed.EITHER,
}


def need_for_tool(tool: str, *, resource_type: str = "") -> IdentityNeed:
    t = (tool or "").strip()
    if t == "feishu.search":
        rt = (resource_type or "doc").strip().lower() or "doc"
        return SEARCH_IDENTITY.get(rt, IdentityNeed.EITHER)
    return TOOL_IDENTITY.get(t, IdentityNeed.EITHER)


def capability_label(tool: str, *, resource_type: str = "") -> str:
    need = need_for_tool(tool, resource_type=resource_type)
    t = (tool or "").strip()
    rt = (resource_type or "").strip().lower()
    if t == "feishu.search" and rt == "calendar":
        return "个人日历详情"
    if t == "feishu.calendar.list":
        return "个人日历详情"
    if t == "feishu.calendar.create":
        return "以你的名义创建日程"
    if t == "feishu.search" and rt == "doc":
        return "个人云文档"
    if t == "feishu.search" and rt == "message":
        return "个人消息检索"
    if need == IdentityNeed.USER:
        return "个人飞书数据"
    if need == IdentityNeed.USER_PREFERRED:
        return "个人飞书数据（更完整）"
    return "个人飞书数据"


# 给用户看的清单（产品文案）
PERSONAL_AUTH_REQUIRED = [
    "我的日历详情 / agenda（不是只看别人 busy）",
    "以我的名义创建/修改日程",
    "搜我的私有云文档、个人 Drive（应用搜不到的部分）",
    "通讯录关键词搜同事（CLI contact +search-user；应用侧可用部门名册兜底）",
    "邮箱读写、个人任务、妙记个人视角等（尚未全开，开了也要 UAT）",
]

BOT_OK_WITHOUT_PERSONAL_AUTH = [
    "公司通讯录名册（部门树 + 姓名/open_id，应用通讯录权限范围内）",
    "按 open_id 查人（contact +get-user）",
    "当前群成员列表",
    "机器人可见的群列表 / 群消息",
    "他人忙闲 freebusy（有 open_id 时）",
    "应用可见的云文档 drive 搜索",
    "机器人发消息 / 以应用身份创建文档并分享",
]
