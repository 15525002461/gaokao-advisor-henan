#!/bin/bash
# ==============================================================================
# gaokao-advisor-henan 一键部署脚本（Ubuntu 20.04 / 22.04）
# ==============================================================================
# 在服务器上跑一行：
#   curl -fsSL https://raw.githubusercontent.com/15525002461/gaokao-advisor-henan/main/deploy.sh | bash
#
# 前提：服务器上已有 ~/gaokao-advisor-henan/data/national/admissions.db
#       （从 Mac 上 scp 过来：scp ~/Desktop/admissions_2025_106w.db ubuntu@43.159.170.65:~/gaokao-advisor-henan/data/national/admissions.db）
# ==============================================================================
set -e

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $1"; }
ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
err()   { echo -e "${RED}❌ $1${NC}"; }

# ---------- 配置 ----------
REPO_URL="https://github.com/15525002461/gaokao-advisor-henan.git"
APP_DIR="$HOME/gaokao-advisor-henan"
APP_PORT="${APP_PORT:-8000}"
DB_PATH="$APP_DIR/data/national/admissions.db"
CFD_BIN="/usr/local/bin/cloudflared"
CFD_LOG="$HOME/cloudflared.log"
CFD_URL_FILE="$HOME/gaokao_tunnel_url.txt"

# ---------- 0. 预检 ----------
log "========== gaokao-advisor 一键部署 =========="

if [ "$EUID" -eq 0 ]; then
    err "请不要用 root 跑这个脚本（用 ubuntu 等普通用户）"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    log "Python3 未安装，正在安装..."
    sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip python3-venv
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
ok "Python $PY_VERSION"

# ---------- 1. 端口检查 ----------
if ss -ltn 2>/dev/null | grep -q ":$APP_PORT "; then
    warn "端口 $APP_PORT 已被占用，先杀掉..."
    sudo fuser -k ${APP_PORT}/tcp 2>/dev/null || true
    sleep 2
fi

# ---------- 2. 克隆/更新代码 ----------
if [ -d "$APP_DIR/.git" ]; then
    log "更新已有代码..."
    cd "$APP_DIR" && git pull --rebase --autostash
else
    log "克隆代码..."
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi
ok "代码就位: $APP_DIR"

# ---------- 3. 数据库检查 ----------
mkdir -p "$APP_DIR/data/national"
if [ ! -f "$DB_PATH" ]; then
    err "数据库不存在: $DB_PATH"
    err "请先在 Mac 上跑:"
    err "  scp ~/Desktop/admissions_2025_106w.db ubuntu@43.159.170.65:~/gaokao-advisor-henan/data/national/admissions.db"
    err "然后重跑本脚本"
    exit 1
fi
DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
ok "数据库就位: $DB_PATH ($DB_SIZE)"

# ---------- 4. Python 虚拟环境 ----------
if [ ! -d "$APP_DIR/venv" ]; then
    log "创建 Python 虚拟环境..."
    python3 -m venv "$APP_DIR/venv"
fi
log "安装依赖..."
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
ok "Python 依赖就位"

# ---------- 5. 写 gaokao.service ----------
SERVICE_FILE="/tmp/gaokao.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=gaokao-advisor FastAPI
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python -m uvicorn api.server:app --host 0.0.0.0 --port $APP_PORT
Restart=always
RestartSec=5
StandardOutput=append:$HOME/gaokao.log
StandardError=append:$HOME/gaokao.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo mv "$SERVICE_FILE" /etc/systemd/system/gaokao.service
sudo systemctl daemon-reload
sudo systemctl enable gaokao.service
sudo systemctl restart gaokao.service
sleep 3

# 验证后端
if curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://localhost:$APP_PORT/api/stats" | grep -q "200"; then
    ok "后端启动成功 (http://localhost:$APP_PORT)"
else
    err "后端启动失败，看日志: tail -50 $HOME/gaokao.log"
    exit 1
fi

# ---------- 6. 装 cloudflared ----------
if [ ! -x "$CFD_BIN" ]; then
    log "下载 cloudflared..."
    cd /tmp
    # 备用下载源
    if ! curl -fsSL -o cloudflared "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"; then
        warn "GitHub 慢，备用："
        curl -fsSL -o cloudflared "https://cloudflared.binarycow.com/cloudflared-linux-amd64"
    fi
    chmod +x cloudflared
    sudo mv cloudflared "$CFD_BIN"
    ok "cloudflared 装好: $CFD_BIN"
fi

# ---------- 7. 写 cf-tunnel.service ----------
SERVICE_FILE="/tmp/cf-tunnel.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Cloudflare Tunnel for gaokao-advisor
After=network.target gaokao.service
Wants=gaokao.service

[Service]
Type=simple
User=$USER
ExecStart=$CFD_BIN tunnel --url http://localhost:$APP_PORT --no-autoupdate --protocol http2
Restart=always
RestartSec=5
StandardOutput=append:$CFD_LOG
StandardError=append:$CFD_LOG

[Install]
WantedBy=multi-user.target
EOF
sudo mv "$SERVICE_FILE" /etc/systemd/system/cf-tunnel.service
sudo systemctl daemon-reload
sudo systemctl enable cf-tunnel.service
sudo systemctl restart cf-tunnel.service
ok "cloudflared 隧道已启动，等待 URL..."

# ---------- 8. 提取公网 URL ----------
for i in {1..30}; do
    if [ -s "$CFD_LOG" ]; then
        TUNNEL_URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$CFD_LOG" | head -1)
        if [ -n "$TUNNEL_URL" ]; then
            echo "$TUNNEL_URL" > "$CFD_URL_FILE"
            break
        fi
    fi
    sleep 1
done

# ---------- 9. 验证 ----------
sleep 2
if [ -n "$TUNNEL_URL" ]; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$TUNNEL_URL/")
else
    HTTP_CODE="?"
    TUNNEL_URL="(URL 还在生成，5 秒后重试：cat $CFD_URL_FILE)"
fi

echo
echo "=============================================================================="
ok "部署完成！"
echo "=============================================================================="
echo
echo "🌐 公网链接:  ${GREEN}$TUNNEL_URL${NC}"
echo "🏠 本地地址:  http://localhost:$APP_PORT"
echo
echo "📊 服务状态:"
sudo systemctl status gaokao.service --no-pager -n 0 2>/dev/null | head -3 | sed 's/^/   /'
sudo systemctl status cf-tunnel.service --no-pager -n 0 2>/dev/null | head -3 | sed 's/^/   /'
echo
echo "📝 日志位置:"
echo "   后端:  tail -f $HOME/gaokao.log"
echo "   隧道:  tail -f $CFD_LOG"
echo
echo "🔧 运维命令:"
echo "   sudo systemctl status gaokao        # 后端状态"
echo "   sudo systemctl status cf-tunnel     # 隧道状态"
echo "   sudo systemctl restart gaokao       # 重启后端"
echo "   sudo systemctl restart cf-tunnel    # 重启隧道（⚠️ URL 会变）"
echo "   cat $CFD_URL_FILE                    # 查当前 URL"
echo
echo "💡 提示："
echo "   - 服务器重启不会丢 URL（systemd 自动拉起）"
echo "   - 但 cloudflared 重启会换 URL，URL 存在 $CFD_URL_FILE"
echo "   - 想要永久 URL？注册 Cloudflare 账号 + 自己的域名 = named tunnel"
echo
if [ "$HTTP_CODE" = "200" ]; then
    ok "公网验证 HTTP $HTTP_CODE ✅"
else
    warn "公网验证 HTTP $HTTP_CODE (可能还在冷启动，等 10 秒再试)"
fi
echo
