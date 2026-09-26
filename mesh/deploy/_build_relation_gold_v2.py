"""Build eval/relation_gold_v2.jsonl from migrated v1 + real/adversarial claim cases."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "eval" / "relation_gold_v2.jsonl"


def case(**kwargs):
    # unify keys
    kwargs.setdefault("rel", None)
    kwargs.setdefault("items", [])
    kwargs.setdefault("claim", None)
    return kwargs


CASES = [
    case(
        id="g01",
        title="双团队各有 evidence",
        notes="应通过 relation_team_supported + grounding；迁移自 v1",
        expect="keep",
        source={"env": "synth", "slug": None},
        rel={
            "title": "面壁智能 · 合作推进",
            "decision_tier": "strong",
            "teams": ["编辑部", "硅谷 BD 团队"],
            "body": "编辑部记录面壁智能沟通；硅谷 BD 团队跟进面壁。",
            "details": ["编辑部记录面壁智能沟通", "硅谷 BD 团队跟进面壁"],
            "evidence": [
                {"item_id": 1, "team": "编辑部", "snippet": "编辑部记录面壁智能沟通"},
                {"item_id": 2, "team": "硅谷 BD 团队", "snippet": "硅谷 BD 团队跟进面壁"},
            ],
        },
        items=[
            {
                "id": 1,
                "source_id": 101,
                "owner_team": "编辑部",
                "pointer": "面壁-编辑",
                "entities": '["面壁智能"]',
                "blocked": 0,
                "text": "编辑部记录面壁智能沟通",
            },
            {
                "id": 2,
                "source_id": 202,
                "owner_team": "硅谷 BD 团队",
                "pointer": "面壁-BD",
                "entities": '["面壁智能"]',
                "blocked": 0,
                "text": "硅谷 BD 团队跟进面壁",
            },
        ],
        claim={"valid": True, "reason": "两侧证据分别支撑各自触点陈述，未越界到已签约等"},
    ),
    case(
        id="g02",
        title="无 evidence 应丢弃",
        notes="filter_ungrounded 应 drop；迁移自 v1",
        expect="drop",
        source={"env": "synth", "slug": None},
        rel={
            "title": "空壳跨团队",
            "decision_tier": "strong",
            "teams": ["编辑部", "商业化团队"],
            "body": "两边都在做",
            "evidence": [],
        },
        items=[
            {
                "id": 1,
                "owner_team": "编辑部",
                "entities": '["甲"]',
                "blocked": 0,
                "text": "甲",
            }
        ],
        claim={"valid": False, "reason": "无 evidence，论断无法成立"},
    ),
    case(
        id="g03",
        title="仅虚线观察可保留",
        notes="watch+evidence 不过滤；迁移自 v1",
        expect="keep",
        source={"env": "synth", "slug": None},
        rel={
            "title": "单侧观察",
            "decision_tier": "watch",
            "teams": ["编辑部", "→ 商业化团队"],
            "body": "编辑部在跟乙相关",
            "weak": True,
            "evidence": [{"item_id": 1, "team": "编辑部", "snippet": "编辑部在跟乙相关"}],
        },
        items=[
            {
                "id": 1,
                "owner_team": "编辑部",
                "entities": '["乙"]',
                "blocked": 0,
                "text": "编辑部在跟乙相关",
            }
        ],
        claim={"valid": True, "reason": "单侧观察表述与证据一致"},
    ),
    case(
        id="g04",
        title="标题词面不替代 evidence owner",
        notes="relation_team_supported 靠 item_id；迁移自 v1",
        expect="team_via_evidence",
        source={"env": "synth", "slug": None},
        rel={
            "title": "跨主题协作",
            "candidate_title": "跨主题协作",
            "teams": ["编辑部", "商业化团队"],
            "body": "两侧各有条目",
            "evidence": [{"item_id": 10}, {"item_id": 11}],
        },
        items=[
            {
                "id": 10,
                "owner_team": "编辑部",
                "entities": '["无关"]',
                "blocked": 0,
                "text": "x",
            },
            {
                "id": 11,
                "owner_team": "商业化团队",
                "entities": '["另一"]',
                "blocked": 0,
                "text": "y",
            },
        ],
        claim=None,
    ),
    case(
        id="g05",
        title="跨期指纹稳定",
        notes="标点/团队顺序不影响 fingerprint；迁移自 v1",
        expect="fingerprint_stable",
        source={"env": "synth", "slug": None},
        rel=None,
        items=[],
        claim=None,
        title_a="面壁智能 · 詹杨帆",
        teams_a=["编辑部", "硅谷 BD 团队"],
        title_b="面壁智能·詹杨帆",
        teams_b=["硅谷 BD 团队", "编辑部"],
    ),
    # --- real / adversarial claim set (tmesh 2026-8-17) ---
    case(
        id="g06",
        title="Founder Park：传播复盘与下半年规划各知一半",
        notes="tmesh 2026-8-17 真稿；两侧同主题信息互补，claim 成立",
        expect="claim_valid",
        source={"env": "tmesh", "slug": "2026-8-17"},
        expect_type="info_complement",
        expect_tier="strong",
        rel={
            "title": "Founder Park 账号运营：传播涨粉与下半年规划两侧各知一半",
            "decision_tier": "strong",
            "relation_type": "info_complement",
            "teams": ["品牌创意团队", "社群"],
            "body": "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉。Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施。两侧各知一半。",
            "details": [
                "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉",
                "Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "品牌创意团队",
                    "snippet": "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉",
                },
                {
                    "item_id": 2,
                    "team": "社群",
                    "snippet": "Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "品牌创意团队",
                "source_id": 43,
                "pointer": "妙记 01:01",
                "blocked": 0,
                "entities": '["Founder Park"]',
                "text": "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉主要来自嘉宾线上主动分享",
            },
            {
                "id": 2,
                "owner_team": "社群",
                "source_id": 41,
                "pointer": "Part1 开篇",
                "blocked": 0,
                "entities": '["Founder Park"]',
                "text": "Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施",
            },
        ],
        claim={
            "valid": True,
            "reason": "证据分别支撑传播复盘与规划两侧信息，未宣称已统一执行",
        },
    ),
    case(
        id="g07",
        title="Insta360 刘靖康：接触与出镜两侧联动",
        notes="tmesh 2026-8-17 真稿；同一人物两侧触点，claim 成立",
        expect="claim_valid",
        source={"env": "tmesh", "slug": "2026-8-17"},
        expect_type="event_chain",
        expect_tier="strong",
        rel={
            "title": "刘靖康（影石 Insta360）：编辑部已接触，视频号用其出镜内容",
            "decision_tier": "strong",
            "relation_type": "event_chain",
            "teams": ["编辑部", "视频号团队"],
            "body": "编辑部已接触 Insta360 CEO 刘靖康，视频号用其在 AGI Playground 的出镜内容做多条视频。",
            "details": [
                "编辑部：Insta360（刘靖康）已接触",
                "视频号：刘靖康 AGI Playground 出镜多条视频",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "编辑部",
                    "snippet": "Insta360（沟通对象刘靖康，CEO）已接触，行业为智能硬件",
                },
                {
                    "item_id": 2,
                    "team": "视频号团队",
                    "snippet": "影石Insta360创始人刘靖康在AGI Playground出镜多条视频",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "编辑部",
                "source_id": 42,
                "pointer": "记录 139",
                "blocked": 0,
                "entities": '["Insta360", "刘靖康"]',
                "text": "Insta360（沟通对象刘靖康，CEO）已接触，行业为智能硬件",
            },
            {
                "id": 2,
                "owner_team": "视频号团队",
                "source_id": 46,
                "pointer": "选题117",
                "blocked": 0,
                "entities": '["影石Insta360", "刘靖康"]',
                "text": "影石Insta360创始人刘靖康在AGI Playground出镜多条视频，讲相机公司产品使命与AI剪辑。",
            },
        ],
        claim={
            "valid": True,
            "reason": "证据支撑接触与出镜两个触点；未宣称商务合作已签约",
        },
    ),
    case(
        id="g08",
        title="Helloboss 单侧接触观察",
        notes="tmesh 2026-8-17 watch；单侧 claim 成立",
        expect="claim_valid",
        source={"env": "tmesh", "slug": "2026-8-17"},
        expect_type="one_sided",
        expect_tier="watch",
        rel={
            "title": "Helloboss CEO 王骁：编辑部接触",
            "decision_tier": "watch",
            "relation_type": "one_sided",
            "weak": True,
            "teams": ["编辑部", "→ Global Partnership 团队"],
            "body": "编辑部接触 Helloboss CEO 王骁；Helloboss 为日本 AI 求职招聘匹配应用。",
            "details": ["编辑部接触 Helloboss CEO 王骁"],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "编辑部",
                    "snippet": "Alex Wang（王骁，Helloboss CEO）已接触；Helloboss 为日本 AI 求职招聘匹配应用",
                }
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "编辑部",
                "source_id": 42,
                "pointer": "人脉",
                "blocked": 0,
                "entities": '["Helloboss", "王骁"]',
                "text": "Alex Wang（王骁，Helloboss CEO）已接触；Helloboss 为日本 AI 求职招聘匹配应用，Global Partnership 团队看日本动态时可对照",
            }
        ],
        claim={"valid": True, "reason": "单侧接触陈述与证据一致，虚线不要求对方证据"},
    ),
    case(
        id="g09",
        title="有 evidence 但宣称已统一执行 Founder Park",
        notes="基于 tmesh 真稿对抗：证据只支持各知一半，不可推已联合执行",
        expect="claim_invalid",
        source={"env": "tmesh", "slug": "2026-8-17", "adversarial": True},
        rel={
            "title": "Founder Park：品牌创意与社群已统一账号运营方案",
            "decision_tier": "strong",
            "relation_type": "info_complement",
            "teams": ["品牌创意团队", "社群"],
            # 词面大量复述 evidence，lexical 易过；但「已形成合作推进」超出证据
            "body": "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉。Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施。品牌创意与社群已形成 Founder Park 合作推进。",
            "details": [
                "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉",
                "Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "品牌创意团队",
                    "snippet": "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉",
                },
                {
                    "item_id": 2,
                    "team": "社群",
                    "snippet": "Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "品牌创意团队",
                "source_id": 4301,
                "pointer": "传播复盘",
                "blocked": 0,
                "entities": '["Founder Park"]',
                "text": "AGI 传播复盘：Founder Park 账号自然关注量约二三十个；LinkedIn 涨约 100 多粉",
            },
            {
                "id": 2,
                "owner_team": "社群",
                "source_id": 4101,
                "pointer": "半年规划",
                "blocked": 0,
                "entities": '["Founder Park"]',
                "text": "Founder Park 下半年规划：从内容驱动转向聚焦创业者核心需求的社区基础设施",
            },
        ],
        claim={
            "valid": False,
            "reason": "证据只证明两侧分别观察/规划，不能证明已形成合作推进",
        },
    ),
    case(
        id="g10",
        title="AGI 分栏：商量中却写成已上线",
        notes="基于 tmesh 真稿对抗：证据『仍在商量』，claim 写成已完成上线",
        expect="claim_invalid",
        source={"env": "tmesh", "slug": "2026-8-17", "adversarial": True},
        expect_type="info_complement",
        expect_tier="strong",
        rel={
            "title": "AGI 2026 现场内容分栏已上线",
            "decision_tier": "strong",
            "relation_type": "info_complement",
            "teams": ["品牌创意团队", "视频号团队"],
            "body": "视频号计划把 AGI 2026 活动现场相关内容单独分栏目，目前仍在商量分栏形式；分栏动因待明确。视频号内容按行业维度建立合集分类，栏目分类初稿由内容侧给到。双方已完成 AGI 2026 分栏目上线。",
            "details": [
                "视频号计划把 AGI 2026 活动现场相关内容单独分栏目，目前仍在商量分栏形式；分栏动因待明确",
                "视频号内容按行业维度建立合集分类，栏目分类初稿由内容侧给到",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "品牌创意团队",
                    "snippet": "视频号计划把 AGI 2026 活动现场相关内容单独分栏目，目前仍在商量分栏形式；分栏动因待明确",
                },
                {
                    "item_id": 2,
                    "team": "视频号团队",
                    "snippet": "视频号内容按行业维度建立合集分类，栏目分类初稿由内容侧给到，再与设计对接",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "品牌创意团队",
                "source_id": 43,
                "pointer": "妙记 19:13",
                "blocked": 0,
                "entities": '["AGI 2026"]',
                "text": "视频号计划把 AGI 2026 活动现场相关内容单独分栏目，目前仍在商量分栏形式；分栏动因待明确",
            },
            {
                "id": 2,
                "owner_team": "视频号团队",
                "source_id": 45,
                "pointer": "妙记 19:33",
                "blocked": 0,
                "entities": '["视频号"]',
                "text": "视频号内容按行业维度建立合集分类，栏目分类初稿由内容侧给到，再与设计对接",
            },
        ],
        claim={
            "valid": False,
            "reason": "证据明确『仍在商量/待明确』，不能支持『已完成上线』；lexical 可能仍过",
        },
    ),
    case(
        id="g11",
        title="秒动科技：尚无反馈却写成联合合作完成",
        notes="基于 tmesh 真稿对抗：证据『尚无反馈/计划探讨』，claim 写成已完成合作",
        expect="claim_invalid",
        source={"env": "tmesh", "slug": "2026-8-17", "adversarial": True},
        rel={
            "title": "秒动科技杨硕：两侧触点与选题计划",
            "decision_tier": "strong",
            "relation_type": "event_chain",
            "teams": ["Global Partnership 团队", "视频号团队"],
            "body": "联系杨硕（妙动/秒动科技）时，编辑部也通过 PR 这条线在联系同一人，尚无反馈。编辑部同事计划两周后参加视频号选题会，探讨海外一手人和公司能否成为视频选题信源。Global Partnership 与视频号已完成秒动科技联合合作推进。",
            "details": [
                "联系杨硕（妙动/秒动科技）时，编辑部也通过 PR 这条线在联系同一人，尚无反馈",
                "编辑部同事计划两周后参加视频号选题会，探讨海外一手人和公司能否成为视频选题信源",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "Global Partnership 团队",
                    "snippet": "联系杨硕（妙动/秒动科技）时，编辑部也通过 PR 这条线在联系同一人，尚无反馈",
                },
                {
                    "item_id": 2,
                    "team": "视频号团队",
                    "snippet": "编辑部同事计划两周后参加视频号选题会，探讨海外一手人和公司能否成为视频选题信源",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "Global Partnership 团队",
                "source_id": 40,
                "pointer": "20260815",
                "blocked": 0,
                "entities": '["杨硕", "秒动科技"]',
                "text": "联系杨硕（妙动/秒动科技）时，编辑部也通过 PR 这条线在联系同一人，尚无反馈",
            },
            {
                "id": 2,
                "owner_team": "视频号团队",
                "source_id": 45,
                "pointer": "妙记 01:17",
                "blocked": 0,
                "entities": '["编辑部"]',
                "text": "编辑部同事计划两周后参加视频号选题会，探讨海外一手人和公司能否成为视频选题信源",
            },
        ],
        claim={
            "valid": False,
            "reason": "证据只有并行触点与计划且尚无反馈，不能证明已完成联合合作推进",
        },
    ),
    case(
        id="g12",
        title="豆包平行触点却写成统一商务合作",
        notes="基于 tmesh 真稿对抗：parallel_tracks 证据存在，但『统一商务合作推进』过头",
        expect="claim_invalid",
        source={"env": "tmesh", "slug": "2026-8-17", "adversarial": True},
        expect_type="parallel_tracks",
        expect_tier="parallel",
        rel={
            "title": "豆包：三团队统一商务合作推进",
            "decision_tier": "strong",
            "relation_type": "event_chain",
            "teams": ["商业化团队", "编辑部", "视频号团队"],
            "body": "会议判断（非核实事实）：豆包手机、阶跃、荣耀推出不同技术路线产品。字节发布豆包工作，TRAE、扣子并入豆包。多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）。三团队已就豆包形成统一商务合作推进。",
            "details": [
                "会议判断（非核实事实）：豆包手机、阶跃、荣耀推出不同技术路线产品",
                "字节发布豆包工作，TRAE、扣子并入豆包",
                "多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "商业化团队",
                    "snippet": "会议判断（非核实事实）：AI手机是当前商业价值最高的AI硬件终端，豆包手机、阶跃、荣耀推出不同技术路线产品",
                },
                {
                    "item_id": 2,
                    "team": "编辑部",
                    "snippet": "字节发布豆包工作，TRAE、扣子并入豆包，与腾讯 WorkBuddy、阿里千问办公构成办公 Agent 竞争",
                },
                {
                    "item_id": 3,
                    "team": "视频号团队",
                    "snippet": "多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）及AI订阅制转Token计费",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "商业化团队",
                "source_id": 44,
                "pointer": "妙记 52:22",
                "blocked": 0,
                "entities": '["豆包"]',
                "text": "会议判断（非核实事实）：AI手机是当前商业价值最高的AI硬件终端，豆包手机、阶跃、荣耀推出不同技术路线产品",
            },
            {
                "id": 2,
                "owner_team": "编辑部",
                "source_id": 42,
                "pointer": "报道 8/25",
                "blocked": 0,
                "entities": '["豆包"]',
                "text": "字节发布豆包工作，TRAE、扣子并入豆包，与腾讯 WorkBuddy、阿里千问办公构成办公 Agent 竞争",
            },
            {
                "id": 3,
                "owner_team": "视频号团队",
                "source_id": 46,
                "pointer": "周数据",
                "blocked": 0,
                "entities": '["豆包"]',
                "text": "多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）及AI订阅制转Token计费",
            },
        ],
        claim={
            "valid": False,
            "reason": "证据只是同实体平行触点/观察，不能证明统一商务合作推进；词面易过 lexical",
        },
    ),
    case(
        id="g13",
        title="百度共现：Workshop 与选题不是联合活动",
        notes="基于 tmesh 真稿对抗：两队都提百度，但写成联合活动/合作",
        expect="claim_invalid",
        source={"env": "tmesh", "slug": "2026-8-17", "adversarial": True},
        rel={
            "title": "百度：社群与视频号联合活动已落地",
            "decision_tier": "strong",
            "relation_type": "event_chain",
            "teams": ["社群", "视频号团队"],
            "body": "活动排期含百度智能硬件 Workshop。周数据视频选题含百度内容方向观察。社群与视频号已就百度联合活动达成合作并完成落地。",
            "details": [
                "活动排期含百度智能硬件 Workshop",
                "周数据视频选题含百度内容方向观察",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "社群",
                    "snippet": "活动排期含百度智能硬件 Workshop",
                },
                {
                    "item_id": 2,
                    "team": "视频号团队",
                    "snippet": "周数据视频选题含百度内容方向观察",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "社群",
                "source_id": 41,
                "pointer": "活动排期",
                "blocked": 0,
                "entities": '["百度"]',
                "text": "活动排期：3 月 W_上海市场、GTC After Party、Openclaw 专场、百度智能硬件 Workshop 等陆续",
            },
            {
                "id": 2,
                "owner_team": "视频号团队",
                "source_id": 46,
                "pointer": "周数据百度",
                "blocked": 0,
                "entities": '["百度"]',
                "text": "周数据视频聚焦AI大厂观察：AI搜索流量、百度内容方向、Cloudflare 等",
            },
        ],
        claim={
            "valid": False,
            "reason": "同实体共现≠联合活动；证据无合作/落地事实；词面可能仍过 lexical",
        },
    ),
    case(
        id="g14",
        title="讨论了接触却写成已达成协议",
        notes="范围越界对抗：evidence=讨论接触，claim=已达成协议",
        expect="claim_invalid",
        source={"env": "synth", "slug": None, "adversarial": True},
        rel={
            "title": "面壁智能：编辑部与硅谷 BD 已达成合作协议",
            "decision_tier": "strong",
            "teams": ["编辑部", "硅谷 BD 团队"],
            "body": "编辑部与面壁智能沟通了后续采访可能性，尚未排期。硅谷 BD 团队关注面壁智能产品进展，内部讨论是否跟进。编辑部与硅谷 BD 团队已与面壁智能达成正式合作协议。",
            "details": [
                "编辑部与面壁智能沟通了后续采访可能性，尚未排期",
                "硅谷 BD 团队关注面壁智能产品进展，内部讨论是否跟进",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "编辑部",
                    "snippet": "编辑部与面壁智能沟通了后续采访可能性，尚未排期",
                },
                {
                    "item_id": 2,
                    "team": "硅谷 BD 团队",
                    "snippet": "硅谷 BD 团队关注面壁智能产品进展，内部讨论是否跟进",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "编辑部",
                "source_id": 101,
                "pointer": "面壁-编辑",
                "blocked": 0,
                "entities": '["面壁智能"]',
                "text": "编辑部与面壁智能沟通了后续采访可能性，尚未排期",
            },
            {
                "id": 2,
                "owner_team": "硅谷 BD 团队",
                "source_id": 202,
                "pointer": "面壁-BD",
                "blocked": 0,
                "entities": '["面壁智能"]',
                "text": "硅谷 BD 团队关注面壁智能产品进展，内部讨论是否跟进",
            },
        ],
        claim={
            "valid": False,
            "reason": "证据仅支持讨论/关注，不能支持已达成正式合作协议；词面可能仍过 lexical",
        },
    ),
    case(
        id="g15",
        title="豆包平行触点（正确平行表述）",
        notes="tmesh 真稿口径：平行触点表述，claim 成立；可与 g12 对照",
        expect="claim_valid",
        source={"env": "tmesh", "slug": "2026-8-17"},
        expect_type="parallel_tracks",
        expect_tier="parallel",
        rel={
            "title": "豆包：商业化、编辑部、视频号各自碰到的三个触点",
            "decision_tier": "parallel",
            "relation_type": "parallel_tracks",
            "teams": ["商业化团队", "编辑部", "视频号团队"],
            "body": "会议判断（非核实事实）：豆包手机、阶跃、荣耀推出不同技术路线产品。字节发布豆包工作，TRAE、扣子并入豆包。多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）。同一公司不同触点。",
            "details": [
                "会议判断（非核实事实）：豆包手机、阶跃、荣耀推出不同技术路线产品",
                "字节发布豆包工作，TRAE、扣子并入豆包",
                "多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）",
            ],
            "evidence": [
                {
                    "item_id": 1,
                    "team": "商业化团队",
                    "snippet": "会议判断（非核实事实）：豆包手机、阶跃、荣耀推出不同技术路线产品",
                },
                {
                    "item_id": 2,
                    "team": "编辑部",
                    "snippet": "字节发布豆包工作，TRAE、扣子并入豆包",
                },
                {
                    "item_id": 3,
                    "team": "视频号团队",
                    "snippet": "多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）",
                },
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "商业化团队",
                "source_id": 44,
                "pointer": "妙记 52:22",
                "blocked": 0,
                "entities": '["豆包"]',
                "text": "会议判断（非核实事实）：豆包手机、阶跃、荣耀推出不同技术路线产品",
            },
            {
                "id": 2,
                "owner_team": "编辑部",
                "source_id": 42,
                "pointer": "报道 8/25",
                "blocked": 0,
                "entities": '["豆包"]',
                "text": "字节发布豆包工作，TRAE、扣子并入豆包",
            },
            {
                "id": 3,
                "owner_team": "视频号团队",
                "source_id": 46,
                "pointer": "周数据",
                "blocked": 0,
                "entities": '["豆包"]',
                "text": "多条视频聚焦豆包做AI交易入口（抖音生活服务、佣金12%）",
            },
        ],
        claim={
            "valid": True,
            "reason": "平行触点表述与三侧证据一致，未升级为统一合作",
        },
    ),
    case(
        id="g16",
        title="evidence ref 指向不存在 item",
        notes="evidence 不足：ref 无效；应 drop",
        expect="drop",
        source={"env": "synth", "slug": None},
        rel={
            "title": "无效 ref 跨团队",
            "decision_tier": "strong",
            "teams": ["编辑部", "商业化团队"],
            "body": "两边都有进展",
            "evidence": [
                {"item_id": 9991, "team": "编辑部", "snippet": "幽灵证据"},
                {"item_id": 9992, "team": "商业化团队", "snippet": "幽灵证据2"},
            ],
        },
        items=[
            {
                "id": 1,
                "owner_team": "编辑部",
                "blocked": 0,
                "entities": '["甲"]',
                "text": "甲",
            }
        ],
        claim={"valid": False, "reason": "evidence item_id 不存在，无法支撑论断"},
    ),
]


def main() -> None:
    lines = [json.dumps(c, ensure_ascii=False) for c in CASES]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} n={len(CASES)}")


if __name__ == "__main__":
    main()
