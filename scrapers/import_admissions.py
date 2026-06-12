"""
导入录取分数线 xlsx 数据 → SQLite
================================
支持多种列序变种，通过表头列名映射自动识别：
- 标准 17 列（专业分）：年份, 院校名称, 院校代码, 科类, 批次, 选科要求, 专业, ...
- 标准 15 列（院校分）：年份, 院校名称, 院校代码, 科类, 批次, 专业组, 选科要求, 录取人数, ...
- 内蒙/宁夏/吉林变体：年份, 院校名称, 院校代码, 批次, 科类, 专业, ...（列名也变）
- 内蒙古院校变体：年份, 学校, 所在省, 公私性质, 科类, 录取批次, ...
- 上海院校（带招生类型）：年份, 院校名称, ..., 批次, 招生类型, 专业组, ...
- 山西投档：院校代号, 院校名称, 科类名称, 专业组, 最低分（无院校性质）
- 上海扩展：生源地, 院校专业组代码, ..., 平均分

Usage:
    python3 import_admissions.py --dir /Users/admin/Desktop/录取分数线
    python3 import_admissions.py --dir ... --only henan,zhejiang
    python3 import_admissions.py --dir ... --dry-run
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import openpyxl


# ============ 路径配置 ============

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "national" / "admissions.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ============ 数据库 Schema ============

SCHEMA_SQL = """
-- 省份元信息
CREATE TABLE IF NOT EXISTS province_meta (
    province TEXT PRIMARY KEY,        -- 拼音 key
    name TEXT NOT NULL,                -- 中文名
    gaokao_mode TEXT,                  -- 3+1+2 / 3+3 / 传统
    record_count INTEGER DEFAULT 0,
    last_import_at INTEGER
);

-- 录取分数主表
CREATE TABLE IF NOT EXISTS admissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_province TEXT NOT NULL,     -- 考生所在省（中文）
    source_province_key TEXT,          -- 拼音 key
    year INTEGER,
    subject_type TEXT,                 -- 物理类/历史类/综合/全部
    batch TEXT,                        -- 本科批/本科提前批/...
    recruit_type TEXT,                 -- 招生类型（普通类/国家专项等）
    school_code TEXT,
    school_name TEXT NOT NULL,
    school_location TEXT,              -- 学校所在省
    school_nature TEXT,                -- 公办/民办
    is_985 TEXT,                       -- 是/否
    is_211 TEXT,
    group_code TEXT,                   -- 专业组
    subject_req TEXT,                  -- 选科要求
    major_code TEXT,
    major_name TEXT,
    major_remark TEXT,
    admit_count INTEGER,
    min_score INTEGER,                 -- 最低分（整数）
    min_score_raw TEXT,                -- 原始分字符串
    min_rank INTEGER,                  -- 最低位次
    max_score INTEGER,                 -- 最高分（部分文件有）
    avg_score INTEGER,                 -- 平均分
    score_diff INTEGER,                -- 批次线差
    source_file TEXT,
    source_format TEXT,
    imported_at INTEGER,
    UNIQUE(source_province, year, subject_type, school_code, major_code, group_code, batch, recruit_type)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_adm_score
    ON admissions(source_province, year, subject_type, min_score);
CREATE INDEX IF NOT EXISTS idx_adm_rank
    ON admissions(source_province, year, subject_type, min_rank);
CREATE INDEX IF NOT EXISTS idx_adm_school
    ON admissions(school_name, year);
CREATE INDEX IF NOT EXISTS idx_adm_target_prov
    ON admissions(school_location, year);
CREATE INDEX IF NOT EXISTS idx_adm_major
    ON admissions(major_name);
"""


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ============ 省份识别 & 标准化 ============

# 文件名 → (省份中文, 拼音 key, 高考模式)
PROVINCE_MAP = {
    "河南": ("河南", "henan", "3+1+2 新高考"),
    "黑龙江": ("黑龙江", "heilongjiang", "3+1+2 新高考"),
    "陕西": ("陕西", "shaanxi", "3+1+2 新高考"),
    "辽宁": ("辽宁", "liaoning", "3+1+2 新高考"),
    "福建": ("福建", "fujian", "3+1+2 新高考"),
    "甘肃": ("甘肃", "gansu", "3+1+2 新高考"),
    "湖北": ("湖北", "hubei", "3+1+2 新高考"),
    "海南": ("海南", "hainan", "3+1+2 新高考"),
    "河北": ("河北", "hebei", "3+1+2 新高考"),
    "江西": ("江西", "jiangxi", "3+1+2 新高考"),
    "广西": ("广西", "guangxi", "3+1+2 新高考"),
    "广东": ("广东", "guangdong", "3+1+2 新高考"),
    "山东": ("山东", "shandong", "3+3 新高考"),
    "山东省": ("山东", "shandong", "3+3 新高考"),  # 山东省变体
    "安徽": ("安徽", "anhui", "3+1+2 新高考"),
    "天津": ("天津", "tianjin", "3+3 新高考"),
    "四川": ("四川", "sichuan", "3+1+2 新高考"),
    "吉林省": ("吉林", "jilin", "3+1+2 新高考"),
    "吉林": ("吉林", "jilin", "3+1+2 新高考"),
    "北京": ("北京", "beijing", "3+3 新高考"),
    "内蒙古": ("内蒙古", "neimenggu", "3+1+2 新高考"),
    "云南": ("云南", "yunnan", "3+1+2 新高考"),
    "上海": ("上海", "shanghai", "3+3 新高考"),
    "重庆": ("重庆", "chongqing", "3+1+2 新高考"),
    "贵州": ("贵州", "guizhou", "3+1+2 新高考"),
    "西藏": ("西藏", "xizang", "3+1+2 新高考"),
    "湖南": ("湖南", "hunan", "3+1+2 新高考"),
    "浙江": ("浙江", "zhejiang", "3+3 新高考"),
    "江苏": ("江苏", "jiangsu", "3+1+2 新高考"),
    "新疆": ("新疆", "xinjiang", "3+1+2 新高考"),
    "宁夏": ("宁夏", "ningxia", "3+1+2 新高考"),
    "山西": ("山西", "shanxi", "3+1+2 新高考"),
    "河北": ("河北", "hebei", "3+1+2 新高考"),
    "河北省": ("河北", "hebei", "3+1+2 新高考"),  # 河北省变体
}


def detect_province_from_filename(filename: str) -> tuple[str, str, str] | None:
    """从文件名识别省份"""
    if "山西" in filename:
        return PROVINCE_MAP["山西"]
    if "上海" in filename:
        return PROVINCE_MAP["上海"]
    # 优先匹配 "在X省/在X的"（长度 2-3 字符的省份名）
    m = re.search(r"在(\w{2,3}省?)(?:的|_)(\w+)?", filename)
    if m:
        prov = m.group(1)
        if prov in PROVINCE_MAP:
            return PROVINCE_MAP[prov]
    # 退而求其次：通用匹配
    m = re.search(r"在(\w{2,3})的", filename)
    if m and m.group(1) in PROVINCE_MAP:
        return PROVINCE_MAP[m.group(1)]
    return None


# ============ 通用字段解析工具 ============

def _clean(v) -> str:
    if v is None:
        return ""
    return str(v).strip().replace("\u3000", " ").replace("\xa0", " ")


def _to_int(v) -> int | None:
    if v is None or v == "":
        return None
    s = str(v).strip()
    if "." in s:
        try:
            return int(float(s))
        except ValueError:
            return None
    try:
        return int(s)
    except ValueError:
        return None


def _to_raw(v) -> str:
    if v is None or v == "":
        return ""
    return str(v).strip()


# ============ 基于表头映射的通用解析器 ============

# 各种列名 → 标准字段 的同义词
COLUMN_ALIASES = {
    "year": ["年份"],
    "school_name": ["院校名称", "学校"],
    "school_code": ["院校代码", "院校代号"],
    "school_location": ["学校所在", "所在省", "院校所在省"],
    "school_nature": ["学校性质", "公私性质"],
    "subject_type": ["科类", "科类名称"],
    "batch": ["批次", "录取批次"],
    "recruit_type": ["招生类型"],
    "group_code": ["专业组", "院校专业组代码"],
    "subject_req": ["选科要求"],
    "major_name": ["专业", "专业名称"],
    "major_code": ["专业代码"],
    "major_remark": ["专业备注"],
    "admit_count": ["录取人数", "招生人数"],
    "min_score": ["最低分数", "最低分", "最低投档分"],
    "min_rank": ["最低位次", "最低分位", "最低分段"],
    "max_score": ["最高分", "最高分数"],
    "avg_score": ["平均分"],
    "score_diff": ["批次线差", "线差", "分差"],
    "is_985": ["是否985", "985"],
    "is_211": ["是否211", "211"],
    "source_province_field": ["生源地"],
    "avg_rank": ["平均位次"],
}


def _build_header_map(header_row: list[str]) -> dict[str, int]:
    """表头行 → {标准字段名: 列索引}"""
    mapping = {}
    for std_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in header_row:
                idx = header_row.index(alias)
                mapping[std_name] = idx
                break
    return mapping


def _parse_with_header_map(ws, header_map: dict, year_default: int = 2025) -> list[dict]:
    """根据 header_map 解析所有数据行"""
    rows = []
    for row in ws.iter_rows(values_only=True):
        cells = [_clean(c) for c in row]
        if not any(cells):
            continue
        if "院校名称" in cells or "学校" in cells or "生源地" in cells:
            # 还是表头
            continue
        rec = {}
        for std_name, idx in header_map.items():
            if idx < len(cells):
                val = cells[idx]
                if std_name in ("year",):
                    rec[std_name] = int(val) if val.isdigit() else year_default
                elif std_name in ("min_score", "min_rank", "admit_count", "max_score", "avg_score", "score_diff"):
                    rec[std_name] = _to_int(val)
                else:
                    rec[std_name] = val
        if rec.get("school_name"):
            rec.setdefault("year", year_default)
            rec.setdefault("min_score_raw", rec.get("min_score", "") if rec.get("min_score") else "")
            rows.append(rec)
    return rows


# ============ 格式检测 ============

def detect_format_and_header(ws) -> tuple[str, dict]:
    """返回 (格式名, header_map)"""
    # 找表头行
    header = None
    header_row_idx = -1
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= 5:
            break
        cells = [_clean(c) for c in row]
        flat = " ".join(cells)
        # 山西投档：表头在 R3 (院校代号, 院校名称, 科类名称, 专业组, 最低分)
        if "院校代号" in cells and "院校名称" in cells and "科类名称" in cells:
            header = cells
            header_row_idx = i
            break
        # 上海扩展（表头在 R2）
        if "生源地" in cells and "院校专业组代码" in cells:
            header = cells
            header_row_idx = i
            break
        # 标准表头（含"院校名称"或"学校"）
        if ("院校名称" in cells or "学校" == cells[1] if len(cells) > 1 else False) and "最低" in flat:
            header = cells
            header_row_idx = i
            break
    if not header:
        return "unknown", {}

    # 判断格式
    if "院校代号" in header:
        return "shanxi", {"shanxi": True}  # 特殊解析

    # 用 alias 映射建 header_map
    header_map = _build_header_map(header)
    # shanghai_extended 也兼容 alias 解析
    if "生源地" in header and "院校专业组代码" in header:
        return "shanghai_extended", header_map

    # 用 alias 映射建 header_map
    header_map = _build_header_map(header)

    # 进一步细分
    if "招生类型" in header and "专业组" in header:
        return "school_with_recruit_type", header_map  # 上海院校
    if "学校" == header[1] if len(header) > 1 else False:
        return "neimenggu_school", header_map  # 内蒙古院校
    if "招生人数" in header:
        return "alt_province_major", header_map  # 内蒙/宁夏/吉林 专业分
    if "录取人数" in header and "专业组" in header:
        return "standard_school", header_map
    if "专业" in header and "专业代码" in header:
        return "standard_major", header_map
    return "generic", header_map


# ============ 山西特殊解析 ============

def parse_shanxi(ws) -> list[dict]:
    rows = []
    last_name = ""
    last_code = ""
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        cells = [_clean(c) for c in row]
        if i < 3:  # 跳过标题/批次
            continue
        if not any(cells):
            continue
        if len(cells) < 5:
            continue
        # 跳过表头行
        if "院校代号" in cells and "院校名称" in cells:
            continue
        code = cells[0]
        name = cells[1]
        subj = cells[2]
        group = cells[3]
        score = cells[4]

        if name:
            last_name = name
            last_code = code
        else:
            name = last_name
            code = last_code

        main_score = None
        if score:
            try:
                main_score = int(float(score))
            except ValueError:
                pass

        rows.append({
            "year": 2025,
            "school_name": name,
            "school_code": code,
            "subject_type": subj,
            "batch": "普通本科批",
            "group_code": group,
            "subject_req": "",
            "admit_count": None,
            "min_score": main_score,
            "min_score_raw": score,
            "min_rank": None,
            "score_diff": None,
            "school_location": "山西",
            "school_nature": "",
            "is_985": "",
            "is_211": "",
            "major_name": "",
            "major_code": "",
            "major_remark": "",
            "recruit_type": "",
        })
    return rows


# ============ 主解析调度 ============

def parse_worksheet(ws, fmt: str, header_map: dict) -> list[dict]:
    if fmt == "shanxi":
        return parse_shanxi(ws)
    if fmt == "unknown":
        return []
    if fmt == "shanghai_extended":
        # 上海扩展：列名基本一致于上海院校，但有"院校专业组代码"
        return _parse_with_header_map(ws, header_map, year_default=2025)
    if fmt == "zhejiang_plan":
        return []  # 招生计划不需要导入
    # 其他都走通用解析
    return _parse_with_header_map(ws, header_map, year_default=2025)


# ============ 数据库写入 ============

def save_records(conn: sqlite3.Connection, records: list[dict],
                 source_file: str, fmt: str,
                 source_prov: str, source_prov_key: str) -> int:
    if not records:
        return 0
    now = int(time.time())
    sql = """INSERT OR IGNORE INTO admissions
        (source_province, source_province_key, year, subject_type, batch, recruit_type,
         school_code, school_name, school_location, school_nature, is_985, is_211,
         group_code, subject_req, major_code, major_name, major_remark,
         admit_count, min_score, min_score_raw, min_rank, max_score, avg_score, score_diff,
         source_file, source_format, imported_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for r in records:
        data.append((
            source_prov, source_prov_key,
            r.get("year"), r.get("subject_type"), r.get("batch"), r.get("recruit_type", ""),
            r.get("school_code", ""), r.get("school_name", ""),
            r.get("school_location", ""), r.get("school_nature", ""),
            r.get("is_985", ""), r.get("is_211", ""),
            r.get("group_code", ""), r.get("subject_req", ""),
            r.get("major_code", ""), r.get("major_name", ""), r.get("major_remark", ""),
            r.get("admit_count"), r.get("min_score"), r.get("min_score_raw", ""),
            r.get("min_rank"), r.get("max_score"), r.get("avg_score"),
            r.get("score_diff"),
            source_file, fmt, now,
        ))
    cur = conn.executemany(sql, data)
    conn.commit()
    return cur.rowcount


def update_province_meta(conn, prov_key, prov_name, gaokao_mode, added):
    now = int(time.time())
    cur = conn.execute("SELECT record_count FROM province_meta WHERE province=?", (prov_key,))
    row = cur.fetchone()
    if row:
        conn.execute(
            """UPDATE province_meta SET record_count=record_count+?, last_import_at=? WHERE province=?""",
            (added, now, prov_key),
        )
    else:
        conn.execute(
            """INSERT INTO province_meta (province, name, gaokao_mode, record_count, last_import_at)
               VALUES (?,?,?,?,?)""",
            (prov_key, prov_name, gaokao_mode, added, now),
        )
    conn.commit()


# ============ 单文件处理 ============

def process_file(conn, filepath: str, dry_run=False) -> dict:
    fname = os.path.basename(filepath)
    prov_info = detect_province_from_filename(fname)
    if not prov_info:
        return {"file": fname, "status": "skip", "reason": "省份识别失败"}

    prov_name, prov_key, gaokao_mode = prov_info
    print(f"\n📄 {fname}")
    print(f"   省份: {prov_name} ({prov_key})  模式: {gaokao_mode}")

    try:
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    except Exception as e:
        return {"file": fname, "status": "error", "reason": str(e)}

    total_inserted = 0
    for sname in wb.sheetnames:
        ws = wb[sname]
        fmt, header_map = detect_format_and_header(ws)
        if fmt == "unknown":
            print(f"   Sheet '{sname}' ({ws.max_row}行) ⚠️  格式未识别，跳过")
            continue
        if fmt == "zhejiang_plan":
            print(f"   Sheet '{sname}'  ⏭  跳过招生计划表")
            continue
        records = parse_worksheet(ws, fmt, header_map)
        if not records:
            print(f"   Sheet '{sname}' ({ws.max_row}行, fmt={fmt}) ⚠️  0 条记录")
            continue

        if dry_run:
            print(f"   Sheet '{sname}' ({ws.max_row}行, fmt={fmt}) → 解析 {len(records)} 条")
            total_inserted += len(records)
            continue

        added = save_records(conn, records, fname, fmt, prov_name, prov_key)
        print(f"   Sheet '{sname}' ({ws.max_row}行, fmt={fmt}) → 入库 {added} 条 (新)")
        total_inserted += added

    wb.close()

    if not dry_run and total_inserted > 0:
        update_province_meta(conn, prov_key, prov_name, gaokao_mode, total_inserted)

    return {"file": fname, "status": "ok", "inserted": total_inserted, "province": prov_name}


# ============ 主流程 ============

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="xlsx 文件目录")
    ap.add_argument("--only", help="只导入指定省份，逗号分隔")
    ap.add_argument("--dry-run", action="store_true", help="不入库只测试")
    args = ap.parse_args()

    files = sorted([f for f in os.listdir(args.dir)
                    if f.endswith(('.xlsx', '.xls')) and not f.startswith('~$')])

    if args.only:
        only_keys = set(args.only.split(","))
        def _match(f):
            info = detect_province_from_filename(f)
            return info and info[1] in only_keys
        files = [f for f in files if _match(f)]
        print(f"🔍 筛选省份: {only_keys}, 命中 {len(files)} 个文件")

    print(f"📁 {args.dir}")
    print(f"📋 待处理: {len(files)} 个文件")
    print(f"💾 数据库: {DB_PATH}")
    if args.dry_run:
        print("🧪 DRY RUN 模式（不入库）")

    conn = None if args.dry_run else init_db()

    t0 = time.time()
    results = []
    for f in files:
        path = os.path.join(args.dir, f)
        result = process_file(conn, path, dry_run=args.dry_run)
        results.append(result)

    print(f"\n{'='*70}")
    print(f"📊 导入汇总")
    print(f"{'='*70}")
    total_new = sum(r.get("inserted", 0) for r in results)
    print(f"总文件: {len(results)}")
    print(f"成功: {sum(1 for r in results if r.get('status') == 'ok')}")
    print(f"失败/跳过: {sum(1 for r in results if r.get('status') != 'ok')}")
    print(f"记录数: {total_new} (dry-run 解析; 真导入则是去重后入库数)")
    print(f"耗时: {time.time()-t0:.1f}s")

    if not args.dry_run and conn:
        print(f"\n📈 数据库现状:")
        for row in conn.execute(
            "SELECT source_province, COUNT(*) FROM admissions GROUP BY source_province ORDER BY 2 DESC"
        ):
            print(f"   {row[0]}: {row[1]} 条")
        total = conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0]
        print(f"   📊 总计: {total} 条")
        conn.close()


if __name__ == "__main__":
    main()
