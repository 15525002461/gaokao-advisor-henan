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
    score_window: int = 30,
    limit_per_school: int = 5,
    major_keywords: list[str] | None = None,
) -> list[dict]:
    """
    获取候选专业记录（按"院校最低分"做主筛选）

    智能窗口：
      - 上限：min(我分 + score_window, 750)
      - 下限：高分考生拉到 200 分差（"我能不能上"的全部学校）
              低分考生只拉到 score_window 分差

    Args:
        major_keywords: 限定专业范围（SQL OR LIKE 多关键词）
    """
    upper = min(score + score_window, 750)

    # 智能下限：高分考生需要看到"碾压级"学校
    if score >= 600:
        # 高分考生：拉所有"低于我 200 分以内"的学校
        # 例：745 → 拉到 545 以上的学校
        lower = max(score - 200, 100)
    else:
        # 普通考生：仅拉 ±score_window 范围
        lower = max(score - score_window, 100)

    conn = _get_conn()
    try:
        where = """
            source_province = ?
            AND year = ?
            AND subject_type = ?
            AND min_score IS NOT NULL
            AND min_score BETWEEN ? AND ?
        """
        params = [source_province, year, subject_type, lower, upper]

        if target_provinces:
            placeholders = ",".join("?" for _ in target_provinces)
            where += f" AND school_location IN ({placeholders})"
            params.extend(target_provinces)

        if major_keywords:
            # 多个关键词 OR LIKE
            kw_clauses = []
            for _ in major_keywords:
                kw_clauses.append("(major_name LIKE ? OR major_remark LIKE ?)")
                params.append(f"%{major_keywords[len(kw_clauses)-1]}%")
                params.append(f"%{major_keywords[len(kw_clauses)-1]}%")
            where += f" AND ({' OR '.join(kw_clauses)})"

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
            LIMIT 10000
        """
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ============ 分类 & 排序 ============

def _classify(score: int, school_min_score: int) -> tuple[str | None, str | None]:
    """根据分数差判断冲稳保档
    gap_internal: 院校分 - 我分（正数=院校比我高）
      冲档：gap_internal > 0           （院校分比我高 0+ 分）
        - 0 < gap ≤ 15  → "冲一冲"
        - gap > 15      → "大胆冲"
      稳档：-5 ≤ gap_internal ≤ 5  （±5 分，稳妥）
      保档：gap_internal < -5           （院校分比我低 5+ 分）
        - -30 ≤ gap < -5  → "保底"
        - -60 ≤ gap < -30 → "稳妥保底"
        - gap < -60       → "绝对稳妥"
    """
    gap = school_min_score - score
    if -5 <= gap <= 5:
        return "稳", "稳妥"
    if 0 < gap <= 15:
        return "冲", "冲一冲"
    if gap > 15:
        return "冲", "大胆冲"
    if -30 <= gap < -5:
        return "保", "保底"
    if -60 <= gap < -30:
        return "保", "稳妥保底"
    # gap < -60
    return "保", "绝对稳妥"


def _build_reason(item: dict, tier: str, score_gap_user: int) -> str:
    """生成推荐理由（score_gap_user = 我的分 - 院校分，正数=我高）"""
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

    if tier == "冲":
        if score_gap_user < 0:
            parts.append(f"您还差 {abs(score_gap_user)} 分，需冲刺")
        else:
            parts.append(f"院校去年最低分 {item.get('min_score')}，录取希望大")
    elif tier == "稳":
        if score_gap_user == 0:
            parts.append("院校去年最低分与您分数相同，录取希望大")
        elif score_gap_user > 0:
            parts.append(f"您高出院校去年最低分 {score_gap_user} 分，录取希望大")
        else:
            parts.append(f"院校去年最低分 {item.get('min_score')} 比您高 {abs(score_gap_user)} 分，稳妥")
    elif tier == "保":
        if score_gap_user > 60:
            parts.append(f"您高出院校去年最低分 {score_gap_user} 分，绝对稳妥")
        elif score_gap_user > 30:
            parts.append(f"您高出院校去年最低分 {score_gap_user} 分，稳妥保底")
        else:
            parts.append(f"您高出院校去年最低分 {score_gap_user} 分，保底稳妥")

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
    major_keyword: str | None = None,
    major_category: str | None = None,
) -> dict:
    """
    跨省份志愿推荐主接口（按院校聚合）

    Args:
        source_province: 考生所在省（中文，如"河南"）
        score: 考生分数
        subject_type: 科类（物理类/历史类/综合）
        year: 参考哪一年的录取数据
        target_provinces: 目标省份列表，None=全国
        top_n: 返回条数
        include_985_211_only: 是否只推荐 985/211
        major_keyword: 目标专业（自由文本，SQL LIKE 匹配），如"计算机"
        major_category: 目标专业分类 key（从 MAJOR_CATEGORIES 取），如"计算机类"

    Returns:
        {
            "input": {...},
            "summary": {"冲": n, "稳": n, "保": n, "candidate_total": N},
            "recommendations": [
                {
                    "school_name", "school_location", "is_985", "is_211",
                    "school_min_score", "school_min_rank",
                    "score_gap", "tier", "tier_label", "reason",
                    "majors": [{major_name, group_code, min_score, min_rank, score_gap, ...}, ...]
                }
            ]
        }
    """
    # 解析 major_category → 多个专业名
    if major_category:
        from .major_categories import MAJOR_CATEGORIES, get_category_keywords
        keywords = get_category_keywords(major_category)
        if keywords:
            major_keyword = None  # 优先用分类
    elif major_keyword:
        from .major_categories import normalize_keyword
        keywords = [normalize_keyword(major_keyword)]
    else:
        keywords = None

    candidates = get_candidate_schools(
        source_province=source_province,
        year=year,
        subject_type=subject_type,
        score=score,
        target_provinces=target_provinces,
        score_window=40,
        major_keywords=keywords,
    )

    # 按学校分组
    schools_map: dict[str, dict] = {}

    for c in candidates:
        if include_985_211_only and c.get("is_985") != "是" and c.get("is_211") != "是":
            continue

        school_name = c["school_name"]
        if school_name not in schools_map:
            schools_map[school_name] = {
                "school_name": school_name,
                "school_code": c.get("school_code", ""),
                "school_location": c.get("school_location", ""),
                "school_nature": c.get("school_nature", ""),
                "is_985": c.get("is_985", ""),
                "is_211": c.get("is_211", ""),
                "batch": c.get("batch", ""),
                "subject_req": c.get("subject_req", ""),
                "majors": [],
            }

        # 跳过专业组/专业完全空的记录（避免"院校专业组"这种空记录）
        major_name = c.get("major_name", "") or ""
        group_code = c.get("group_code", "") or ""

        # 过滤掉"专业组名"：当指定了 major_keywords 时，只保留匹配的专业
        # 院校分表里 major_name 可能是"机械类"、"电气类"等大类名（不是真专业）
        if major_keywords_set := set(k or "" for k in (keywords or [])):
            if major_name and not any(kw in major_name for kw in major_keywords_set):
                continue
            if not major_name:
                # 没有专业名的记录（如"院校专业组"），按 group_code 没法判定专业
                # 如果 keywords 不为空，跳过这些空记录
                continue

        schools_map[school_name]["majors"].append({
            "major_name": major_name,
            "major_code": c.get("major_code", ""),
            "group_code": group_code,
            "subject_req": c.get("subject_req", ""),
            "min_score": c.get("min_score"),
            "min_rank": c.get("min_rank") or 0,
            "admit_count": c.get("admit_count"),
            "score_diff": c.get("score_diff"),
            "major_remark": c.get("major_remark", ""),
        })

    # 计算每个学校的"代表分"（所有专业中最低的 min_score）
    tiered: dict[str, list[dict]] = {"冲": [], "稳": [], "保": []}
    all_schools = []

    for school in schools_map.values():
        majors = school["majors"]
        # 过滤掉 min_score 为空的
        valid_majors = [m for m in majors if m["min_score"]]
        if not valid_majors:
            continue

        # 院校代表分 = 所有专业中最低录取分
        school_min_score = min(m["min_score"] for m in valid_majors)
        # 找到对应专业的 min_rank
        school_min_major = min(valid_majors, key=lambda x: x["min_score"])
        school_min_rank = school_min_major["min_rank"]

        # 院校级分差
        gap_user = score - school_min_score
        tier, label = _classify(score, school_min_score)
        if not tier or not label:
            continue

        # 计算每个专业的分差
        for m in valid_majors:
            m["score_gap"] = score - m["min_score"]

        # 招生总数（粗略）
        total_admit = sum(m["admit_count"] for m in valid_majors if m["admit_count"])

        # 推荐理由
        reason = _build_reason_school(school, tier, gap_user, total_admit)

        # 按专业 min_score 降序排（最高分专业在前）
        valid_majors.sort(key=lambda x: x["min_score"] or 0, reverse=True)

        school_reco = {
            **school,
            "majors": valid_majors,
            "school_min_score": school_min_score,
            "school_min_rank": school_min_rank,
            "total_admit": total_admit,
            "score_gap": gap_user,
            "tier": tier,
            "tier_label": label,
            "reason": reason,
        }
        tiered[tier].append(school_reco)
        all_schools.append(school_reco)

    # 排序
    tiered["冲"].sort(key=lambda x: x["score_gap"], reverse=True)
    tiered["稳"].sort(key=lambda x: abs(x["score_gap"]))
    tiered["保"].sort(key=lambda x: x["score_gap"])

    # 配比
    plan = {"冲": 3, "稳": 4, "保": 3} if top_n == 10 else (
        {"冲": 5, "稳": 5, "保": 5} if top_n == 15 else
        {"冲": 7, "稳": 7, "保": 6} if top_n == 20 else None
    )

    if plan:
        result = []
        for tier, cnt in plan.items():
            result.extend(tiered[tier][:cnt])
        if len(result) < top_n:
            for tier in ["冲", "稳", "保"]:
                remaining = [x for x in tiered[tier] if x not in result]
                result.extend(remaining[:top_n - len(result)])
                if len(result) >= top_n:
                    break
        recommendations = result[:top_n]
    else:
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
            "major_keyword": major_keyword,
            "major_category": major_category,
        },
        "summary": {
            "冲": len([r for r in recommendations if r["tier"] == "冲"]),
            "稳": len([r for r in recommendations if r["tier"] == "稳"]),
            "保": len([r for r in recommendations if r["tier"] == "保"]),
            "candidate_total": len(all_schools),
        },
        "recommendations": recommendations,
    }


def _build_reason_school(school: dict, tier: str, score_gap_user: int, total_admit: int) -> str:
    """按院校级别生成推荐理由"""
    parts = []
    if school.get("is_985") == "是":
        parts.append("985 院校")
    elif school.get("is_211") == "是":
        parts.append("211 院校")
    if school.get("school_nature") == "公办":
        parts.append("公办")

    location = school.get("school_location", "")
    if location and location not in ("", school.get("school_name")):
        parts.append(f"位于{location}")

    if tier == "冲":
        if score_gap_user < 0:
            parts.append(f"院校最低分比您高 {abs(score_gap_user)} 分，需冲刺")
        else:
            parts.append(f"院校最低分 {school.get('school_min_score')}，录取希望大")
    elif tier == "稳":
        if score_gap_user == 0:
            parts.append("院校最低分与您分数相同，录取希望大")
        elif score_gap_user > 0:
            parts.append(f"您高出院校最低分 {score_gap_user} 分，录取希望大")
        else:
            parts.append(f"院校最低分比您高 {abs(score_gap_user)} 分，稳妥")
    elif tier == "保":
        if score_gap_user > 60:
            parts.append(f"您高出院校最低分 {score_gap_user} 分，绝对稳妥")
        elif score_gap_user > 30:
            parts.append(f"您高出院校最低分 {score_gap_user} 分，稳妥保底")
        else:
            parts.append(f"您高出院校最低分 {score_gap_user} 分，保底稳妥")

    if total_admit:
        parts.append(f"招生 {total_admit} 人")

    return "，".join(parts) if parts else "录取可能"


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
