"""结构化主体名归一。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.qa_structured import _entity_keys, _entity_norm, _infer_section, _split_entity_aliases


def test_entity_norm_strips_suffix():
    assert _entity_norm("面壁智能有限公司") == _entity_norm("面壁智能")
    assert _entity_norm(" 面壁 智能 ") == "面壁智能"


def test_split_entity_aliases_slash_composite():
    """事实表把多主体塞一行（"A / B / C"）时，必须拆出原子名。"""
    parts = _split_entity_aliases("灵瑙科技 / 飞声助听器 / 亲宝宝 / 氧刻 / 影眸科技")
    assert "影眸科技" in parts
    assert "亲宝宝" in parts
    # 原串仍保留（用于整串精确匹配）
    assert "灵瑙科技 / 飞声助听器 / 亲宝宝 / 氧刻 / 影眸科技" in parts


def test_split_entity_aliases_paren_and_colon():
    """"X（Y）：说明" 形态要拆出 X / Y，并去掉说明尾巴。"""
    parts = _split_entity_aliases("刘靖康（影石 Insta360）：编辑部接触、视频号出镜")
    assert "刘靖康" in parts
    assert "影石 Insta360" in parts
    assert _split_entity_aliases("擎羽科技（晴雨科技）")[:2] == ["晴雨科技", "擎羽科技"]


def test_split_entity_aliases_title_dot():
    assert _split_entity_aliases("破壳创智 · 吴伟")[:2] == ["破壳创智", "吴伟"]


def test_entity_keys_intersect_across_composite():
    """复合串与原子名必须能互相命中（e15=0 的根因）。"""
    composite = _entity_keys("灵瑙科技 / 飞声助听器 / 亲宝宝 / 氧刻 / 影眸科技")
    assert _entity_keys("影眸科技") & composite
    assert not (_entity_keys("面壁智能") & composite)


def test_infer_section_relation_words():
    assert _infer_section("编辑部和商业化团队在可同步关系上有哪些重叠？") == "关系"
    assert _infer_section("两边同步了哪些客户") == "关系"
    # 单实体/普通问句仍走接触
    assert _infer_section("编辑部接触了面壁智能吗") == "接触"
