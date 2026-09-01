"""团队 → T 类型自动映射。"""
from app import ingest


def test_default_stype_for_team():
    assert ingest.default_stype_for_team("视频号团队") == "T11"
    assert ingest.default_stype_for_team("硅谷 BD 团队") == "T3"
    assert ingest.default_stype_for_team("Global Partnership 团队") == "T10"
    assert ingest.default_stype_for_team("音频播客团队") == "T12"
    assert ingest.default_stype_for_team("内容中心·数据聚合") == "T13"
    assert ingest.default_stype_for_team("未知") == "T6"


def test_editorial_picks():
    assert ingest.default_stype_for_team("编辑部 · 沟通记录") == "T1"
    assert ingest.default_stype_for_team("编辑部 · 选题表") == "T2"
    assert ingest.default_stype_for_team("编辑部 · 周例会") == "T6"
    assert ingest.owner_team_for_pick("编辑部 · 选题表") == "编辑部"
    assert ingest.source_pick_for("编辑部", "T2") == "编辑部 · 选题表"


def test_infer_source_meta_team_drives_stype():
    meta = ingest.infer_source_meta("视频号周数据.csv", "播放 点赞", default_team="", filename_only=True)
    assert meta["team"] == "视频号团队"
    assert meta["stype"] == "T11"

    meta = ingest.infer_source_meta("gp_weekly.txt", "partnership update", default_team="Global Partnership 团队", filename_only=True)
    assert meta["team"] == "Global Partnership 团队"
    assert meta["stype"] == "T10"

    meta = ingest.infer_source_meta("选题表.csv", "选题池 刊发", default_team="", filename_only=True)
    assert meta["team"] == "编辑部 · 选题表"
    assert meta["stype"] == "T2"

    meta = ingest.infer_source_meta("编辑部例会.docx", "周例会 妙记 会议纪要", default_team="", filename_only=True)
    assert meta["team"] == "编辑部 · 周例会"
    assert meta["stype"] == "T6"

    meta = ingest.infer_source_meta("沟通记录.csv", "一手对话 攻坚讨论", default_team="", filename_only=True)
    assert meta["team"] == "编辑部 · 沟通记录"
    assert meta["stype"] == "T1"


def test_upload_ignores_body_when_filename_only():
    """正文含「硅谷 BD」等关键词时，上传仍只认文件名。"""
    meta = ingest.infer_source_meta(
        "视频号周数据.csv",
        "硅谷 BD 建联 行程 出差",
        default_team="",
        filename_only=True,
    )
    assert meta["team"] == "视频号团队"
    assert meta["stype"] == "T11"

    meta = ingest.infer_source_meta(
        "沟通记录.csv",
        "周例会 妙记 会议纪要",
        default_team="",
        filename_only=True,
    )
    assert meta["team"] == "编辑部 · 沟通记录"
    assert meta["stype"] == "T1"


def test_geekpark_english_maps_to_en_site():
    from app import db

    assert db.normalize_team("GeekPark English") == "英文站"
    meta = ingest.infer_source_meta("GeekPark English RSS.txt", "The Overnight", default_team="", filename_only=False)
    assert meta["team"] == "英文站"


def test_content_agg_report_filename_is_bundle():
    name = "内容聚合报告_近7天_20260825至20260831_生成20260901_1355.txt"
    meta = ingest.infer_source_meta(name, "", default_team="品牌创意团队", filename_only=True)
    assert meta["team"] == "内容中心·数据聚合"
    assert meta["stype"] == "T13"
    assert meta["channel"] == "aggregator"
