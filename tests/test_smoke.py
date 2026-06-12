"""
端到端冒烟测试
==============
覆盖：API 健康、数据统计、推荐接口、跨省份、985/211 过滤、按院校聚合、专业分类筛选
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
    locations = set(rec["school_location"] for rec in r["recommendations"])
    assert len(locations) >= 2, f"全国推荐但只出现了 {len(locations)} 个省份"
    print(f"✅ 江苏 580 历史类 → 全国: {len(locations)} 个不同省份")


def test_985_filter():
    r = recommend("河南", 600, "物理类", include_985_211_only=True, top_n=10)
    for rec in r["recommendations"]:
        assert rec["is_985"] == "是" or rec["is_211"] == "是", \
            f"985/211 过滤失败: {rec['school_name']}"
    print(f"✅ 985/211 过滤: {len(r['recommendations'])} 条全是重点院校")


def test_school_aggregation():
    """需求 1: 验证按院校聚合（每张卡 = 一所学校，含 majors 列表）"""
    r = recommend("河南", 580, "物理类", target_provinces=["北京"], top_n=5)
    for school in r["recommendations"]:
        assert "school_name" in school
        assert "school_min_score" in school
        assert "majors" in school
        assert len(school["majors"]) >= 1
        # 院校最低分应该是该校所有专业中最低的
        min_score = min(m["min_score"] for m in school["majors"] if m["min_score"])
        assert school["school_min_score"] == min_score, \
            f"院校最低分不正确: {school['school_name']}"
    avg = sum(len(s["majors"]) for s in r["recommendations"]) / len(r["recommendations"])
    print(f"✅ 按院校聚合: {len(r['recommendations'])} 所学校, 平均 {avg:.1f} 个专业/校")


def test_major_category():
    """需求 2 进阶版: 计算机类分类筛选"""
    r = recommend("河南", 600, "物理类", major_category="计算机类", top_n=5)
    assert len(r["recommendations"]) > 0
    for school in r["recommendations"]:
        for m in school["majors"]:
            major = m["major_name"] or ""
            assert any(kw in major for kw in [
                "计算机", "软件", "数据", "人工智能",
                "网络", "信息", "智能", "物联网", "区块链",
            ]), f"计算机类匹配了非计算机专业: {major}"
    print(f"✅ 计算机类分类: {len(r['recommendations'])} 所学校全是计算机相关")


def test_major_keyword():
    """需求 2 简单版: 关键词搜索"""
    r = recommend("河南", 600, "物理类", major_keyword="人工智能", top_n=3)
    assert len(r["recommendations"]) > 0
    for school in r["recommendations"]:
        for m in school["majors"]:
            assert "人工智能" in (m["major_name"] or ""), \
                f"关键词匹配错误: {m['major_name']}"
    print(f"✅ 关键词「人工智能」: {len(r['recommendations'])} 所学校精确匹配")


def test_subject_types():
    for subj in ["物理类", "历史类"]:
        r = recommend("河南", 550, subj, top_n=5)
        assert r["summary"]["candidate_total"] > 0
        print(f"✅ 河南 550 {subj}: 候选 {r['summary']['candidate_total']}")


def test_high_score_aggregation():
    """高分考生 745+ 修复回归"""
    r = recommend("河南", 745, "物理类", top_n=5)
    assert r["summary"]["candidate_total"] > 0
    print(f"✅ 河南 745 物理类: {r['summary']}, 候选 {r['summary']['candidate_total']}")


if __name__ == "__main__":
    print("="*70)
    print("🧪 冒烟测试")
    print("="*70)
    test_provinces()
    test_recommend_basic()
    test_recommend_national()
    test_985_filter()
    test_school_aggregation()
    test_major_category()
    test_major_keyword()
    test_subject_types()
    test_high_score_aggregation()
    print("\n🎉 全部通过！")
