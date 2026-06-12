"""
EOL 数据抓取 - 各省高考一分一段表
================================
数据源：https://gaokao.eol.cn/{省份拼音}/dongtai/{年月}/t{年月日}_{id}.shtml
- 公开无反爬（Apache服务器）
- 各省2025年6月已发布
- 字段统一：分数 / 本段人数 / 累计人数（位次）

省份配置：
- 河南: he_nan (2025新高考3+1+2 物理/历史)
- 山东: shan_dong (2025新高考3+3 物理/历史)
- 北京: bei_jing (3+3)
- 重庆: chong_qing (3+1+2)
- 广东: guang_dong (3+1+2)
- 江苏: jiang_su (3+1+2)

Usage:
    python scrape_eol_rank.py --province he_nan
    python scrape_eol_rank.py --all   # 抓所有配置省份
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error

# ============ 配置 ============

DB_PATH = Path(__file__).parent.parent / "data" / "henan" / "gaokao_v2.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# 各省2025一分一段表URL（已实测可访问）
PROVINCE_PAGES = {
    "henan": {
        "name": "河南",
        "pinyin": "he_nan",
        "高考模式": "3+1+2 新高考（2025首届）",
        "urls": {
            ("2025", "物理类"): "https://gaokao.eol.cn/he_nan/dongtai/202506/t20250625_2676859.shtml",
            ("2025", "历史类"): "https://gaokao.eol.cn/he_nan/dongtai/202506/t20250625_2676858.shtml",
        },
    },
    "shandong": {
        "name": "山东",
        "pinyin": "shan_dong",
        "高考模式": "3+3 新高考",
        "urls": {
            ("2025", "全部"): "https://gaokao.eol.cn/shan_dong/dongtai/202506/t20250625_2677092.shtml",
        },
    },
    "beijing": {
        "name": "北京",
        "pinyin": "bei_jing",
        "高考模式": "3+3 新高考",
        "urls": {
            ("2025", "全部"): "https://gaokao.eol.cn/bei_jing/dongtai/202506/t20250625_2676934.shtml",
        },
    },
    "chongqing": {
        "name": "重庆",
        "pinyin": "chong_qing",
        "高考模式": "3+1+2 新高考",
        "urls": {
            ("2025", "物理类"): "https://gaokao.eol.cn/chong_qing/dongtai/202506/t20250624_2676788.shtml",
            # 历史类需另找
        },
    },
}

# ============ 数据库 ============

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rank_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    province TEXT NOT NULL,        -- 省份（拼音：henan/shandong）
    year INTEGER NOT NULL,         -- 年份
    subject_type TEXT NOT NULL,    -- 物理类/历史类/全部
    score TEXT NOT NULL,           -- 分数段（含686-750这样的范围）
    score_low INTEGER,             -- 该段最低分（解析后）
    score_high INTEGER,            -- 该段最高分
    count INTEGER NOT NULL,        -- 本段人数
    cumulative INTEGER NOT NULL,   -- 累计人数（位次）
    source_url TEXT,
    fetched_at INTEGER,
    UNIQUE(province, year, subject_type, score)
);
CREATE INDEX IF NOT EXISTS idx_rank_lookup
    ON rank_table(province, year, subject_type, score_low, score_high);
CREATE INDEX IF NOT EXISTS idx_rank_cumulative
    ON rank_table(province, year, subject_type, cumulative);

-- 省份元信息
CREATE TABLE IF NOT EXISTS province_meta (
    province TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    pinyin TEXT,
    gaokao_mode TEXT,              -- 高考模式
    score_full INTEGER DEFAULT 750,
    first_data_year INTEGER,
    last_fetch_at INTEGER
);
"""


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ============ 抓取与解析 ============

def fetch_page(url: str) -> str:
    """抓取单页HTML（urllib标准库）"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        # 自动检测编码
        data = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        # eol.cn 实际是 utf-8，强制用 utf-8
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode(charset, errors="ignore")


def parse_score_range(score_str: str) -> tuple[int | None, int | None]:
    """解析分数字符串 '686-750' / '700' / '100+' → (low, high)"""
    score_str = score_str.strip()
    if "-" in score_str:
        parts = score_str.split("-")
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None, None
    # 单分或单分+
    s = score_str.rstrip("+")
    try:
        v = int(s)
        return v, v
    except ValueError:
        return None, None


def parse_rank_table(html: str) -> list[dict]:
    """解析一分一段表 HTML → [{score, count, cumulative, score_low, score_high}, ...]"""
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S)
    if not tables:
        return []

    rows = []
    for t in tables:
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
        for tr in trs:
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)
            cells = [re.sub(r"<[^>]+>", "", c).strip().replace("&nbsp;", " ") for c in cells]
            cells = [c for c in cells if c]
            if len(cells) == 3:
                # 检查是否就是 [分数, 人数, 累计人数] 格式
                score, count, cumulative = cells[0], cells[1], cells[2]
                # 累计人数应该是纯数字
                if cumulative.isdigit():
                    low, high = parse_score_range(score)
                    if low is not None:
                        rows.append({
                            "score": score,
                            "score_low": low,
                            "score_high": high,
                            "count": int(count) if count.isdigit() else 0,
                            "cumulative": int(cumulative),
                        })
    return rows


def save_to_db(conn, province, year, subject_type, rows, source_url):
    """批量写入数据库（INSERT OR REPLACE）"""
    now = int(datetime.now().timestamp())
    inserted = 0
    for r in rows:
        try:
            conn.execute(
                """INSERT OR REPLACE INTO rank_table
                (province, year, subject_type, score, score_low, score_high,
                 count, cumulative, source_url, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (province, year, subject_type, r["score"],
                 r["score_low"], r["score_high"],
                 r["count"], r["cumulative"],
                 source_url, now),
            )
            inserted += 1
        except sqlite3.Error as e:
            print(f"  ⚠️  插入失败 {r}: {e}")
    conn.commit()

    # 更新省份元信息
    conn.execute(
        """INSERT OR REPLACE INTO province_meta
        (province, name, pinyin, gaokao_mode, first_data_year, last_fetch_at)
        VALUES (?,?,?,?,?,?)""",
        (province,
         PROVINCE_PAGES[province]["name"],
         PROVINCE_PAGES[province]["pinyin"],
         PROVINCE_PAGES[province]["高考模式"],
         year, now),
    )
    conn.commit()
    return inserted


# ============ 校验工具 ============

def validate_rank_data(province, year, subject_type, conn):
    """校验数据完整性：累计人数应单调递减（高分→低分）"""
    cur = conn.execute(
        """SELECT score, count, cumulative FROM rank_table
           WHERE province=? AND year=? AND subject_type=?
           ORDER BY score_low DESC""",
        (province, year, subject_type),
    )
    rows = cur.fetchall()
    if not rows:
        return False, "无数据"
    # 累计人数应该从大到小
    last_cum = float("inf")
    issues = []
    for score, count, cum in rows:
        if cum > last_cum:
            issues.append(f"累计人数逆序: {score}={cum} > 上一个={last_cum}")
        last_cum = cum
    if issues:
        return False, "; ".join(issues[:3])
    # 头部数据
    top = rows[0]
    bottom = rows[-1]
    return True, f"顶部: {top[0]}={top[2]}人, 底部: {bottom[0]}={bottom[2]}人, 共{len(rows)}条"


def rank_to_score(province, year, subject_type, target_rank, conn):
    """位次反查分数：给定位次（累计人数），返回对应的分数段"""
    cur = conn.execute(
        """SELECT score, score_low, score_high, count, cumulative FROM rank_table
           WHERE province=? AND year=? AND subject_type=? AND cumulative <= ?
           ORDER BY cumulative DESC LIMIT 1""",
        (province, year, subject_type, target_rank),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "score_range": row[0],
        "score_low": row[1],
        "score_high": row[2],
        "cumulative": row[4],
    }


# ============ 主流程 ============

def scrape_province(prov_key: str, conn, verbose=True):
    """抓取一个省份的所有数据"""
    info = PROVINCE_PAGES.get(prov_key)
    if not info:
        print(f"❌ 未知省份: {prov_key}")
        return

    print(f"\n{'='*70}")
    print(f"📍 省份: {info['name']} ({prov_key})")
    print(f"   模式: {info['高考模式']}")
    print(f"   URL 集合: {len(info['urls'])}")
    print('='*70)

    for (year, subject_type), url in info["urls"].items():
        print(f"\n  🔄 抓取 {year} {subject_type} ...")
        try:
            html = fetch_page(url)
        except Exception as e:
            print(f"  ❌ 抓取失败: {e}")
            continue

        rows = parse_rank_table(html)
        if not rows:
            print(f"  ❌ 解析无数据")
            continue

        inserted = save_to_db(conn, prov_key, year, subject_type, rows, url)
        print(f"  ✅ 写入 {inserted} 条记录")

        # 校验
        ok, msg = validate_rank_data(prov_key, year, subject_type, conn)
        status = "✅" if ok else "⚠️ "
        print(f"  {status} 校验: {msg}")

        if verbose and rows:
            print(f"  头3条: {rows[:3]}")
            print(f"  尾3条: {rows[-3:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--province", help="省份key，如 he_nan")
    ap.add_argument("--all", action="store_true", help="抓所有配置省份")
    ap.add_argument("--query", help="位次反查分数，如 10000")
    args = ap.parse_args()

    conn = init_db()
    print(f"📁 数据库: {DB_PATH}")
    print(f"📅 当前时间: {datetime.now().isoformat()[:19]}")

    if args.province or args.all:
        targets = list(PROVINCE_PAGES.keys()) if args.all else [args.province]
        for p in targets:
            scrape_province(p, conn)
    elif args.query:
        # 位次反查演示
        target = int(args.query)
        for prov in PROVINCE_PAGES:
            print(f"\n{prov}:")
            for (year, subj) in [("2025", "物理类"), ("2025", "历史类")]:
                r = rank_to_score(prov, year, subj, target, conn)
                if r:
                    print(f"  位次 {target}（{year} {subj}）→ 分数段 {r['score_range']}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
