"""结构化主体名归一。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.qa_structured import _entity_norm


def test_entity_norm_strips_suffix():
    assert _entity_norm("面壁智能有限公司") == _entity_norm("面壁智能")
    assert _entity_norm(" 面壁 智能 ") == "面壁智能"
