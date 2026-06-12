"""
FastAPI 后端 - 高考志愿推荐系统
================================
Endpoints:
- GET  /                      → 主页 HTML
- GET  /api/provinces         → 支持的省份列表
- GET  /api/subject-types     → 支持的科类
- POST /api/recommend         → 10 条志愿推荐
- GET  /api/stats             → 数据统计
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# 让 core 包可导入
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import recommend
from core.recommend import list_provinces, recommend as do_recommend
from core.rank_engine import query_score


PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时检查数据库"""
    db_path = PROJECT_ROOT / "data" / "national" / "admissions.db"
    if not db_path.exists():
        print(f"⚠️  数据库未找到: {db_path}")
        print("   请先运行: python3 scrapers/import_admissions.py --dir <xlsx目录>")
    else:
        size_mb = db_path.stat().st_size / 1024 / 1024
        print(f"✅ 数据库已加载: {db_path} ({size_mb:.1f} MB)")
    yield


app = FastAPI(
    title="高考志愿填报推荐系统",
    description="基于 2025 真实录取数据的智能推荐（冲稳保三档）",
    version="2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============ Pydantic 模型 ============

class RecommendRequest(BaseModel):
    source_province: str = Field(description="考生所在省（中文）", example="河南")
    score: int = Field(ge=100, le=750, description="高考分数")
    subject_type: str = Field(default="物理类", description="科类", example="物理类")
    year: int = Field(default=2025, description="参考年份")
    target_provinces: list[str] | None = Field(default=None, description="目标省份列表（中文），None=全国")
    top_n: int = Field(default=10, ge=5, le=20, description="推荐条数")
    include_985_211_only: bool = Field(default=False, description="只推荐 985/211")


# ============ API 路由 ============

@app.get("/")
async def index():
    """主页"""
    html_path = TEMPLATES_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>模板未找到</h1><p>请创建 templates/index.html</p>", status_code=500)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/provinces")
async def get_provinces():
    """返回支持的省份列表"""
    provinces = list_provinces()
    return {
        "count": len(provinces),
        "provinces": provinces,
    }


@app.get("/api/subject-types")
async def get_subject_types():
    """返回支持的科类"""
    return {
        "options": [
            {"value": "物理类", "label": "物理类（3+1+2 / 3+3）", "gaokao_modes": ["3+1+2", "3+3"]},
            {"value": "历史类", "label": "历史类（3+1+2 / 3+3）", "gaokao_modes": ["3+1+2", "3+3"]},
            {"value": "综合", "label": "综合（不分文理）", "gaokao_modes": ["3+3"]},
        ]
    }


@app.post("/api/recommend")
async def api_recommend(req: RecommendRequest):
    """志愿推荐"""
    try:
        result = do_recommend(
            source_province=req.source_province,
            score=req.score,
            subject_type=req.subject_type,
            year=req.year,
            target_provinces=req.target_provinces,
            top_n=req.top_n,
            include_985_211_only=req.include_985_211_only,
        )
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"推荐失败: {e}")


@app.get("/api/stats")
async def stats():
    """数据库统计"""
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "national" / "admissions.db"
    if not db_path.exists():
        return {"error": "数据库未初始化"}

    conn = sqlite3.connect(str(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0]
        schools = conn.execute("SELECT COUNT(DISTINCT school_name) FROM admissions").fetchone()[0]
        majors = conn.execute("SELECT COUNT(DISTINCT major_name) FROM admissions WHERE major_name != '' AND major_name IS NOT NULL").fetchone()
        majors = majors[0] if majors else 0
        years = [r[0] for r in conn.execute("SELECT DISTINCT year FROM admissions ORDER BY year DESC").fetchall() if r[0]]
        provinces = [r[0] for r in conn.execute("SELECT DISTINCT source_province FROM admissions ORDER BY source_province").fetchall()]

        by_prov = conn.execute("""
            SELECT source_province, COUNT(*) FROM admissions
            GROUP BY source_province ORDER BY 2 DESC
        """).fetchall()

        return {
            "total_records": total,
            "school_count": schools,
            "major_count": majors,
            "year_coverage": years,
            "province_count": len(provinces),
            "provinces": provinces,
            "by_province": [{"name": r[0], "count": r[1]} for r in by_prov],
        }
    finally:
        conn.close()


@app.get("/api/query-score")
async def query_score_api(province: str = "henan", year: int = 2025,
                          subject_type: str = "物理类", score: int = 600):
    """位次反查（一分一段表）"""
    r = query_score(province, year, subject_type, score)
    return r


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
