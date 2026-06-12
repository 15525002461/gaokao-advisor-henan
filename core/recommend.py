"""
跨省份志愿推荐引擎（冲稳保）
============================
输入：考生所在省、科类、分数、目标省份范围
输出：10 条具体院校+专业推荐，分"冲/稳/保"三档

数据源：admissions 表（已导入 100+ 万条）

核心算法：
- 冲：目标院校去年最低分 > 考生分 0-15 分（冲一冲可能上车）
- 稳：目标院校去年最低分 ±5 分以内（差不多能录）
- 保：目标院校去年最低分 < 考生分 5-30 分（保底）
"""
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).parent.parent / "data" / "national" / "admissions.db"


# ============ 配置 ============

# 冲稳保的分数档差（相对"该院校去年最低分"）
TIER_CONFIG = {
    "冲": {"min_gap": 0, "max_gap": 15},     # 院校最低分比我高 0-15 分
    "稳": {"min_gap": -5, "max_gap": 5},     # 院校最低分 ±5 分
    "保": {"min_gap": -30, "max_gap": -5},   # 院校最低分比我低 5-30 分
}

DEFAULT_TOP_N = 10
DEFAULT_YEAR = 2025


@dataclass
class RecommendItem:
    """单条推荐"""
    tier: str                # 冲/稳/保
    tier_label: str          # 冲冲/稳妥/保底
    school_name: str
    school_location: str
    school_nature: str
    is_985: str
    is_211: str
    major_name: str
    group_code: str
    subject_req: str
    batch: str
    last_year_min_score: int
    last_year_min_rank: int
    admit_count: Optional[int]
    score_gap: int           # 院校最低分 - 我的分数（正数=我高，负数=我低）
    reason: str              # 推荐理由

    def to_dict(self):
        return asdict(self)


# ============ 数据库查询 ============

def _get_conn() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def list_provinces() -> list[dict]:
    """返回支持的省份列表（带数据年份和记录数）"""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            SELECT name, province, gaokao_mode, record_count
            FROM province_meta
            WHERE record_count > 100
            ORDER BY record_count DESC
        """)
        return [{
            "name": row[0], "key": row[1],
            "gaokao_mode": row[2], "record_count": row[3],
        } for row in cur.fetchall()]
    finally:
        conn.close()


def get_candidate_schools(
    source_province: str,
    year: int,
    subject_type: str,
    score: int,
    target_provinces: list[str] | None = None,
    score_window: int = 30,  # 院校分 ± 这个范围
    limit_per_school: int = 5,  # 每个学校最多取几个专业
) -> list[dict]:
    """
    获取候选专业记录（按"院校最低分"做主筛选）

    默认：院校去年最低分 ∈ [score - score_window, score + score_window]
    """
    conn = _get_conn()
    try:
        # 主筛选：取院校分表（或专业分表）的相关记录
        # 目标：找出"考生分数能上"的院校+专业
        where = """
            source_province = ?
            AND year = ?
            AND subject_type = ?
            AND min_score IS NOT NULL
            AND min_score BETWEEN ? AND ?
        """
        params = [source_province, year, subject_type,
                  score - score_window, score + score_window]

        if target_provinces:
            placeholders = ",".join("?" for _ in target_provinces)
            where += f" AND school_location IN ({placeholders})"
            params.extend(target_provinces)

        sql = f"""
            SELECT
                id, school_name, school_code, school_location,
                school_nature, is_985, is_211,
                group_code, subject_req,
                major_name, major_code, major_remark,
                batch, recruit_type, admit_count,
                min_score, min_rank, score_diff
            FROM admissions
            WHERE {where}
            ORDER BY min_score DESC
            LIMIT 2000
        """
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ============ 分类 & 排序 ============

def _classify(score: int, school_min_score: int) -> tuple[str | None, str | None]:
    """根据分数差判断冲稳保档"""
    gap = school_min_score - score  # 正数=我高
    if -5 <= gap <= 5:
        return "稳", "稳妥"
    if 0 < gap <= 15:
        return "冲", "冲一冲"
    if -30 <= gap < -5:
        return "保", "保底"
    if gap > 15:
        return "冲", "大胆冲"  # 分数高出很多，可以冲更高目标
    # gap < -30：差太多，跳过
    return None, None


def _build_reason(item: dict, tier: str, score_gap: int) -> str:
    """生成推荐理由"""
    parts = []
    if item.get("is_985") == "是":
        parts.append("985 院校")
    elif item.get("is_211") == "是":
        parts.append("211 院校")
    if item.get("school_nature") == "公办":
        parts.append("公办")

    location = item.get("school_location", "")
    if location and location not in ("", item.get("school_name")):
        parts.append(f"位于{location}")

    tier_desc = {
        "冲": f"院校去年最低分高出您 {score_gap} 分，需冲刺",
        "稳": f"院校去年最低分 {item.get('min_score')} 与您分数接近，录取希望大",
        "保": f"院校去年最低分低于您 {abs(score_gap)} 分，保底稳妥",
    }
    if tier in tier_desc:
        parts.append(tier_desc[tier])

    if item.get("admit_count"):
        parts.append(f"去年招生 {item['admit_count']} 人")

    return "，".join(parts) if parts else "录取可能"


def recommend(
    source_province: str,
    score: int,
    subject_type: str = "物理类",
    year: int = DEFAULT_YEAR,
    target_provinces: list[str] | None = None,
    top_n: int = DEFAULT_TOP_N,
    include_985_211_only: bool = False,
) -> dict:
    """
    跨省份志愿推荐主接口

    Args:
        source_province: 考生所在省（中文，如"河南"）
        score: 考生分数
        subject_type: 科类（物理类/历史类/综合）
        year: 参考哪一年的录取数据
        target_provinces: 目标省份列表，None=全国
        top_n: 返回条数
        include_985_211_only: 是否只推荐 985/211

    Returns:
        {
            "input": {...},
            "summary": {"冲": n, "稳": n, "保": n},
            "recommendations": [RecommendItem, ...]
        }
    """
    candidates = get_candidate_schools(
        source_province=source_province,
        year=year,
        subject_type=subject_type,
        score=score,
        target_provinces=target_provinces,
        score_window=40,  # 拉大范围，留筛选余地
    )

    # 分类
    tiered: dict[str, list[RecommendItem]] = {"冲": [], "稳": [], "保": []}
    seen_school_majors = set()  # 去重：同一学校+专业只取一次

    for c in candidates:
        if include_985_211_only and c.get("is_985") != "是" and c.get("is_211") != "是":
            continue

        min_score = c.get("min_score")
        if not min_score:
            continue

        tier, label = _classify(score, min_score)
        if not tier or not label:
            continue

        # 同校+同专业组+同专业 去重
        dedup_key = (c["school_name"], c.get("major_name", ""), c.get("group_code", ""))
        if dedup_key in seen_school_majors:
            continue
        seen_school_majors.add(dedup_key)

        gap = min_score - score
        item = RecommendItem(
            tier=tier,
            tier_label=label,
            school_name=c["school_name"],
            school_location=c.get("school_location", ""),
            school_nature=c.get("school_nature", ""),
            is_985=c.get("is_985", ""),
            is_211=c.get("is_211", ""),
            major_name=c.get("major_name", ""),
            group_code=c.get("group_code", ""),
            subject_req=c.get("subject_req", ""),
            batch=c.get("batch", ""),
            last_year_min_score=min_score,
            last_year_min_rank=c.get("min_rank") or 0,
            admit_count=c.get("admit_count"),
            score_gap=gap,
            reason=_build_reason(c, tier, gap),
        )
        tiered[tier].append(item)

    # 排序 + 取 Top N
    # 冲档：按 gap 升序（gap 越小越值得冲）
    # 稳档：按 gap 绝对值升序
    # 保档：按 gap 降序（gap 越负越稳）
    tiered["冲"].sort(key=lambda x: x.score_gap)
    tiered["稳"].sort(key=lambda x: abs(x.score_gap))
    tiered["保"].sort(key=lambda x: -x.score_gap)  # 负 gap 越大越好

    # 配比：3 冲 + 4 稳 + 3 保 = 10 条
    plan = {"冲": 3, "稳": 4, "保": 3} if top_n == 10 else None
    if plan:
        result = []
        for tier, cnt in plan.items():
            result.extend(tiered[tier][:cnt])
        # 不足时从其他档补
        if len(result) < top_n:
            for tier in ["冲", "稳", "保"]:
                remaining = [x for x in tiered[tier] if x not in result]
                result.extend(remaining[:top_n - len(result)])
                if len(result) >= top_n:
                    break
        recommendations = result[:top_n]
    else:
        # 通用比例
        all_items = []
        for tier in ["冲", "稳", "保"]:
            all_items.extend(tiered[tier])
        recommendations = all_items[:top_n]

    return {
        "input": {
            "source_province": source_province,
            "year": year,
            "subject_type": subject_type,
            "score": score,
            "target_provinces": target_provinces or "全国",
            "top_n": top_n,
        },
        "summary": {
            "冲": len([r for r in recommendations if r.tier == "冲"]),
            "稳": len([r for r in recommendations if r.tier == "稳"]),
            "保": len([r for r in recommendations if r.tier == "保"]),
            "candidate_total": sum(len(v) for v in tiered.values()),
        },
        "recommendations": [r.to_dict() for r in recommendations],
    }


# ============ CLI 测试 ============

if __name__ == "__main__":
    print("="*70)
    print("🎓 高考志愿推荐引擎 - 测试")
    print("="*70)

    print("\n支持的省份:")
    for p in list_provinces()[:10]:
        print(f"  {p['name']} ({p['key']}): {p['record_count']} 条")

    print("\n" + "="*70)
    print("📊 河南考生，物理类，600 分，目标北京")
    print("="*70)
    result = recommend("河南", 600, "物理类", target_provinces=["北京"], top_n=10)
    print(f"  候选总数: {result['summary']['candidate_total']}")
    print(f"  冲/稳/保: {result['summary']['冲']}/{result['summary']['稳']}/{result['summary']['保']}")
    print()
    for r in result["recommendations"]:
        print(f"  [{r['tier_label']}] {r['school_name']} - {r['major_name'] or '(院校组)'}")
        print(f"    院校分: {r['last_year_min_score']} ({r['score_gap']:+d}分)")
        print(f"    {r['reason']}")
        print()
