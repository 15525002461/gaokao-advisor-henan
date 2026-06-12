# 🎓 gaokao-advisor-henan (v2)

> **高考志愿填报智能推荐系统** — 基于 **真实录取数据**（106 万条）的跨省份冲稳保推荐

## ✨ v2.0 核心升级（2026-06）

| 维度 | v1 (已归档) | **v2 (本项目)** |
|------|------------|----------------|
| 数据源 | `static-data.gaokao.cn`（**已404**） | 桌面 xlsx 真实数据（**106 万条**）|
| 年份 | 2021-2024 | **2022-2025（4 年）** |
| 省份覆盖 | 全国通用（空壳）| **30 省份 + 2,883 所院校** |
| 推荐方法 | 逐校分数匹配（空）| **位次法 + 冲稳保三档 + 跨省份** |
| 推荐结果 | 无 | **具体院校 + 专业 + 推荐理由** |

## 📊 数据规模

```
📁 录取记录: 1,068,551 条
🏫 院校数:   2,883 所
📚 专业数:   2,693 个
📍 省份:     30 个（缺青海/内蒙古/中国港澳台）
📅 年份:     2022/2023/2024/2025
```

数据从 `~/Desktop/录取分数线/` 61 个 xlsx 导入，覆盖省份：
- **多年数据**（22-25 年）：重庆、贵州、西藏、湖南、浙江、江苏、新疆、宁夏
- **2025 单年**：河南、黑龙江、陕西、辽宁、福建、甘肃、湖北、海南、河北、江西、广西、广东、山东、安徽、天津、四川、吉林、北京、内蒙古、云南、上海
- **特殊格式**：山西（物理类/历史类分文件、含小数分）

## 🏗️ 项目结构

```
gaokao-advisor-henan/
├── scrapers/
│   ├── scrape_eol_rank.py     # eol.cn 一分一段表抓取（位次法基础）
│   └── import_admissions.py   # ⭐ xlsx → SQLite 导入（4 种格式自适应）
├── core/
│   ├── rank_engine.py         # 位次反查 + 档次判定
│   └── recommend.py           # ⭐ 跨省份冲稳保推荐引擎
├── api/
│   └── server.py              # ⭐ FastAPI 后端（5 个端点）
├── templates/
│   └── index.html             # ⭐ 完整前端 UI
├── data/
│   ├── henan/gaokao_v2.db     # 一分一段表（1192 条）
│   └── national/admissions.db # ⭐ 录取主表（478 MB / 106 万条）
└── requirements.txt
```

## 🚀 快速使用

### 1. 导入数据

```bash
# 导入桌面 xlsx 数据
python3 scrapers/import_admissions.py --dir /Users/admin/Desktop/录取分数线

# 增量导入指定省份
python3 scrapers/import_admissions.py --dir /Users/admin/Desktop/录取分数线 --only henan,zhejiang

# 试运行不写库
python3 scrapers/import_admissions.py --dir ... --dry-run
```

### 2. 启动 API 服务

```bash
python3 -m uvicorn api.server:app --host 0.0.0.0 --port 8000
# 访问 http://localhost:8000
```

### 3. Python API 调用

```python
from core.recommend import recommend

# 河南考生，物理类 580 分，目标北京/上海
result = recommend("河南", 580, "物理类", target_provinces=["北京", "上海"], top_n=10)
for r in result["recommendations"]:
    print(f"[{r['tier_label']}] {r['school_name']} - {r['major_name']}")
    print(f"  去年最低分 {r['last_year_min_score']} ({r['score_gap']:+d}分)")
```

## 📡 API 端点

| Method | Path | 用途 |
|--------|------|------|
| `GET`  | `/` | 主页（Web UI）|
| `GET`  | `/api/provinces` | 支持的省份列表 |
| `GET`  | `/api/subject-types` | 科类选项 |
| `POST` | `/api/recommend` | **核心：10 条志愿推荐** |
| `GET`  | `/api/stats` | 数据库统计 |
| `GET`  | `/api/query-score` | 一分一段表位次反查 |

### POST /api/recommend

**请求**:
```json
{
    "source_province": "河南",
    "score": 580,
    "subject_type": "物理类",
    "year": 2025,
    "target_provinces": ["北京", "上海"],
    "top_n": 10,
    "include_985_211_only": false
}
```

**响应**:
```json
{
    "input": {...},
    "summary": {"冲": 3, "稳": 4, "保": 3, "candidate_total": 520},
    "recommendations": [
        {
            "tier": "冲",
            "tier_label": "冲一冲",
            "school_name": "上海工程技术大学",
            "school_location": "上海",
            "school_nature": "公办",
            "is_985": "否",
            "is_211": "否",
            "major_name": "(院校专业组)",
            "group_code": "（101）",
            "subject_req": "...",
            "batch": "本科批",
            "last_year_min_score": 586,
            "last_year_min_rank": 63025,
            "admit_count": null,
            "score_gap": 6,
            "reason": "公办，位于上海，院校去年最低分高出您 6 分，需冲刺"
        }
    ]
}
```

## 🎯 核心算法：冲稳保

```
输入：考生分数 S
↓
查"去年院校最低分"在 [S-30, S+15] 范围内的所有院校+专业
↓
按分数差分类：
  冲：院校最低分 - S ∈ (0, 15]    （院校比我高 1-15 分，需冲刺）
  稳：院校最低分 - S ∈ [-5, 0]    （±5 分，录取希望大）
  保：院校最低分 - S ∈ [-30, -5)  （院校比我低 5-30 分，保底）
↓
按 tier 内排序 + 配比（3冲+4稳+3保）→ 输出 10 条
↓
每条生成推荐理由（985/211 标签 + 地区 + 公办 + 招生人数）
```

## 🌐 公网访问

通过 Cloudflare Tunnel 暴露：

```
https://european-institutional-continuity-loop.trycloudflare.com
```

⚠️ **临时隧道**，重启后会变化。生产建议用 named tunnel 或部署到 Zeabur。

## 🗄️ 数据表 Schema

```sql
-- 录取主表（admissions.db，~478 MB / 106 万行）
CREATE TABLE admissions (
    id INTEGER PRIMARY KEY,
    source_province TEXT,        -- 考生所在省
    source_province_key TEXT,    -- 拼音 key
    year INTEGER,
    subject_type TEXT,           -- 物理类/历史类/综合
    batch TEXT,                  -- 本科批/本科提前批
    recruit_type TEXT,           -- 招生类型（普通类/国家专项等）
    school_code TEXT,
    school_name TEXT,
    school_location TEXT,        -- 学校所在省
    school_nature TEXT,          -- 公办/民办
    is_985 TEXT, is_211 TEXT,
    group_code TEXT,             -- 专业组
    subject_req TEXT,            -- 选科要求
    major_code TEXT, major_name TEXT, major_remark TEXT,
    admit_count INTEGER,
    min_score INTEGER,           -- 最低分
    min_score_raw TEXT,          -- 原始分（保留小数）
    min_rank INTEGER,            -- 最低位次
    max_score INTEGER, avg_score INTEGER,
    score_diff INTEGER,          -- 批次线差
    source_file TEXT, source_format TEXT,
    imported_at INTEGER
);

-- 一分一段表（gaokao_v2.db，1192 条河南）
CREATE TABLE rank_table (
    province TEXT, year INTEGER, subject_type TEXT,
    score TEXT, score_low INTEGER, score_high INTEGER,
    count INTEGER, cumulative INTEGER
);
```

## 🛣️ 路线图

- [x] **Phase 0**：数据验证（eol.cn）
- [x] **Phase 1**：河南一分一段表入库
- [x] **Phase 2.1**：FastAPI 后端 + 简单查询页
- [x] **Phase 2.3**：106 万条真实录取数据导入
- [x] **Phase 3**：跨省份冲稳保推荐引擎
- [x] **Phase 3.1**：30 省份支持
- [ ] **Phase 4**：多算法融合（位次法 + 线差法 + 招生计划分析）
- [ ] **Phase 5**：志愿表导出（PDF/Excel）
- [ ] **Phase 6**：提前批 / 专项计划专项推荐
- [ ] **Phase 7**：志愿顺序优化（按录取概率排序）

## 🔗 相关

- **桌面数据源**：`~/Desktop/录取分数线/` （61 个 xlsx）
- **公网链接**：https://european-institutional-continuity-loop.trycloudflare.com
- **GitHub**：https://github.com/15525002461/gaokao-advisor-henan
- **旧版归档**：https://github.com/15525002461/gaokao-advisor

---

最后更新：2026-06-12 | 数据：106 万条 | 覆盖：30 省份 4 年
