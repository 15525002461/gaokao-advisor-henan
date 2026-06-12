"""
核心算法层 - 位次法推荐引擎
============================
基于一分一段表的纯位次推荐（不依赖逐校数据）
"""
from dataclasses import dataclass
from pathlib import Path
import sqlite3


DB_PATH = Path(__file__).parent.parent / "data" / "henan" / "gaokao_v2.db"


@dataclass
class RankResult:
    """位次反查结果"""
    province: str
    year: int
    subject_type: str          # 物理类/历史类/全部
    score: int                  # 输入分数
    score_range: str            # 命中分数段
    rank: int                   # 累计位次（不超过该分数的人数）
    rank_at_score: int          # 严格小于该分数的位次
    tier: str                   # 985/211/普通本科/专科
    tier_desc: str              # 档次详细说明
    advice: str                 # 报考建议


# 河南2025年位次→档次映射（基于2025招生计划+录取数据经验值）
# 实际项目会从院校投档线表动态计算
TIER_THRESHOLDS_HENAN_2025 = {
    "物理类": [
        (2000, "顶尖985", "清华、北大、复旦、交大、浙大等顶尖985，强基计划主战场"),
        (15000, "中上985", "武大、华科、中山、厦大、南开等985王牌专业，部分顶级211"),
        (50000, "次985/211", "山大、川大、重大、合工大、南航、西南交大等211头部+985中后段"),
        (100000, "中部211/头部双非", "郑大、南昌大、广西大、燕大、上海大等211+头部双非"),
        (180000, "末段211/普通一本", "偏远211+省会城市一本+特色双非"),
        (260000, "普通本科", "民办本科+独立学院+专科本科"),
        (350000, "本科压线", "本科线附近，需重点关注征集志愿"),
        (500000, "专科", "公办专科+优质民办专科"),
        (999999, "专科以下", "建议考虑高职单招/技能型路径"),
    ],
    "历史类": [
        (1500, "顶尖985", "北大、复旦、人大、武大、浙大、南大等顶尖985"),
        (10000, "中上985", "中山、厦大、川大、山大、南开等985王牌+顶级财经政法"),
        (30000, "次985/211", "重大、合工大、南航、苏大、上大等211头部"),
        (70000, "中部211/头部双非", "郑大、南昌大、湖南师、华中师等师范类+财经类"),
        (130000, "末段211/普通一本", "偏远211+省会城市一本+特色双非"),
        (200000, "普通本科", "民办本科+独立学院+专科本科"),
        (260000, "本科压线", "本科线附近，需重点关注征集志愿"),
        (320000, "专科", "公办专科+优质民办专科"),
        (999999, "专科以下", "建议考虑高职单招/技能型路径"),
    ],
}


def get_tier(rank: int, subject_type: str, province: str = "henan") -> tuple[str, str]:
    """根据位次和选科类型，返回 (档次, 详细说明)"""
    if province != "henan":
        # 兜底：粗略按全国通用档次
        if rank <= 10000: return "顶尖985", "顶级高校（具体取决于省份）"
        if rank <= 50000: return "中上985", "985/211头部"
        if rank <= 150000: return "211级别", "211工程+头部双非"
        if rank <= 300000: return "普通本科", "省会一本+特色双非"
        return "专科", "专科批次"

    thresholds = TIER_THRESHOLDS_HENAN_2025.get(subject_type, TIER_THRESHOLDS_HENAN_2025["物理类"])
    for max_rank, tier, desc in thresholds:
        if rank <= max_rank:
            return tier, desc
    return "专科以下", "建议考虑高职单招/技能型路径"


def rank_lookup(province: str, year: int, subject_type: str, score: int) -> RankResult | None:
    """
    位次反查（核心API）

    输入：省份、年份、选科类型、分数
    输出：RankResult（位次+档次+建议）
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在: {DB_PATH}，请先运行 scrape_eol_rank.py")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # 找到 score_low <= 输入分数 <= score_high 的段
        cur = conn.execute(
            """SELECT * FROM rank_table
               WHERE province=? AND year=? AND subject_type=?
                 AND score_low <= ? AND score_high >= ?
               ORDER BY score_low DESC LIMIT 1""",
            (province, year, subject_type, score, score),
        )
        row = cur.fetchone()
        if not row:
            return None

        # 该分数段内的排名估算：累计 + (count / 段内分布估算)
        # 简化：直接用 cumulative 作为"不超过该分数段的累计"
        cumulative = row["cumulative"]
        # 严格小于该段最低分的累计 = cumulative - count
        rank_at_score = cumulative - row["count"]

        tier, tier_desc = get_tier(cumulative, subject_type, province)

        # 报考建议
        advice = _build_advice(tier, score, subject_type, province)

        return RankResult(
            province=province,
            year=year,
            subject_type=subject_type,
            score=score,
            score_range=row["score"],
            rank=cumulative,
            rank_at_score=rank_at_score,
            tier=tier,
            tier_desc=tier_desc,
            advice=advice,
        )
    finally:
        conn.close()


def _build_advice(tier: str, score: int, subject_type: str, province: str) -> str:
    """基于档次生成报考建议"""
    advice_map = {
        "顶尖985": f"🎯 分数 {score} 属顶尖段。建议：1) 强基计划（清北复交等） 2) 综合评价（南科大、上科大）3) 国家专项（贫困地区）",
        "中上985": f"🎯 分数 {score} 属中上985段。建议：1) 985王牌专业优先 2) 顶尖211的王牌专业（如上财、央财、北邮）3) 提前批（公费师范、军校）",
        "次985/211": f"🎯 分数 {score} 属次985/211段。建议：1) 中等985的普通专业 2) 头部211的王牌专业 3) 沿海地区211保底",
        "中部211/头部双非": f"🎯 分数 {score} 属中部211段。建议：1) 省内211（郑大）保底 2) 一线城市双非的王牌专业 3) 关注专业组内的具体专业冷热",
        "末段211/普通一本": f"🎯 分数 {score} 属末段211/一本段。建议：1) 偏远211 2) 省会城市王牌双非 3) 注意专业组搭配",
        "普通本科": f"🎯 分数 {score} 属普通本科段。建议：1) 民办本科保底 2) 关注国家专项/地方专项 3) 提前批（公费医学、农科）",
        "本科压线": f"⚠️ 分数 {score} 接近本科线。建议：1) 重点关注征集志愿 2) 公办专科的王牌专业 3) 考虑省外偏远本科",
        "专科": f"📌 分数 {score} 属专科段。建议：1) 公办专科的王牌专业（如电力、医学专科）2) 提前批专科（警校、军籍）3) 考虑专升本路径",
        "专科以下": f"📌 分数 {score} 建议：1) 高职单招（春季招生） 2) 技能型专业（订单培养） 3) 中外合作专科",
    }
    return advice_map.get(tier, "暂无具体建议")


def query_score(province: str = "henan",
                year: int = 2025,
                subject_type: str = "物理类",
                score: int = 600) -> dict:
    """便捷查询接口（返回 dict 便于 API 序列化）"""
    result = rank_lookup(province, year, subject_type, score)
    if not result:
        return {"error": f"未找到 {province} {year} {subject_type} 分数 {score} 的数据"}

    return {
        "input": {"province": province, "year": year, "subject_type": subject_type, "score": score},
        "result": {
            "score_range": result.score_range,
            "rank": result.rank,                # 不超过该分数的人数
            "rank_at_score": result.rank_at_score,  # 严格小于该分数的人数
            "tier": result.tier,
            "tier_desc": result.tier_desc,
            "advice": result.advice,
        }
    }


if __name__ == "__main__":
    # 简单测试
    print("="*70)
    print("核心算法测试")
    print("="*70)
    for subject in ["物理类", "历史类"]:
        for score in [600, 550, 500, 450, 400, 350]:
            r = query_score("henan", 2025, subject, score)
            if "error" in r:
                print(f"  ❌ {subject} {score}分: {r['error']}")
            else:
                res = r["result"]
                print(f"  {subject} {score}分 → 位次{res['rank']:>6} ({res['tier']:>10})")
