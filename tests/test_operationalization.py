"""机检：Config 每个字段都必须出现在 PRD 的「操作化假设清单」中。

PRD 漏写任何参数 → 本测试失败（第 8 轮验收要求的防漂移机制）。
"""
import dataclasses
from pathlib import Path

from multiedge.config import Config

PRD = Path(__file__).resolve().parent.parent / "PRD.md"


def test_assumptions_section_exists():
    text = PRD.read_text(encoding="utf-8")
    assert "## 操作化假设清单" in text, "PRD 缺少「操作化假设清单」一节"


def test_every_config_field_documented():
    text = PRD.read_text(encoding="utf-8")
    missing = [f.name for f in dataclasses.fields(Config) if f.name not in text]
    assert not missing, f"PRD 操作化假设清单漏写 Config 字段: {missing}"
