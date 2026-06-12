"""
端到端冒烟测试
==============
覆盖：API 健康、数据统计、推荐接口、跨省份、985/211 过滤
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.recommend import recommend, list_provinces


def test_provinces():
    provinces = list_provinces()
    assert len(provinces) >= 20, f"省份数过少: {len(provinces)}"
    print(f"✅ 省份数: {len(provinces)}")
    for p in provinces[:3]:
        assert p["record_count"] > 100, f"{p['name']} 数据过少: {p['record_count']}"


def test_recommend_basic():
    r = recommend("河南", 580, "物理类", target_provinces=["北京"], top_n=10)
    assert r["summary"]["冲"] + r["summary"]["稳"] + r["summary"]["保"] == 10
    assert r["summary"]["candidate_total"] > 0
    print(f"✅ 河南 580 物理类 → 北京: 候选{r['summary']['candidate_total']}, {r['summary']}")


def test_recommend_national():
    r = recommend("江苏", 580, "历史类", top_n=10)
    assert len(r["recommendations"]) == 10
    # 检查全国推荐中至少出现 2 个不同省份
    locations = set(rec["school_location"] for rec in r["recommendations"])
    assert len(locations) >= 2, f"全国推荐但只出现了 {len(locations)} 个省份"
    print(f"✅ 江苏 580 历史类 → 全国: {len(locations)} 个不同省份")


def test_985_filter():
    r = recommend("河南", 600, "物理类", include_985_211_only=True, top_n=10)
    for rec in r["recommendations"]:
        assert rec["is_985"] == "是" or rec["is_211"] == "是", \
            f"985/211 过滤失败: {rec['school_name']}"
    print(f"✅ 985/211 过滤: {len(r['recommendations'])} 条全是重点院校")


def test_subject_types():
    for subj in ["物理类", "历史类"]:
        r = recommend("河南", 550, subj, top_n=5)
        # 不管哪种，结果都应该不空
        assert r["summary"]["candidate_total"] > 0
        print(f"✅ 河南 550 {subj}: 候选 {r['summary']['candidate_total']}")


if __name__ == "__main__":
    print("="*70)
    print("🧪 冒烟测试")
    print("="*70)
    test_provinces()
    test_recommend_basic()
    test_recommend_national()
    test_985_filter()
    test_subject_types()
    print("\n🎉 全部通过！")
