#!/bin/bash
echo "Restarting 小宇量化 (JZhu Trading)..."
echo ""

# Pre-flight: reject paths with spaces / non-ASCII (see start.sh for rationale).
case "$PWD" in
  *" "*)
    echo "============================================"
    echo "  [错误 / ERROR] 安装路径包含空格 / Install path contains a space"
    echo "  当前路径 / Current: $PWD"
    echo ""
    echo "  Docker Desktop 不能可靠处理带空格的 bind-mount 路径。"
    echo "  请把 jzhu-trading 文件夹移到无空格路径下重新运行。"
    echo ""
    echo "  Move the folder to a no-space path and re-run."
    echo "  推荐 / Recommended: ~/jzhu-trading/"
    echo "============================================"
    exit 1
    ;;
esac
# Whitelist check: path must only contain safe chars (a-z A-Z 0-9 / . _ -)
if printf '%s' "$PWD" | grep -qE '[^a-zA-Z0-9/._ -]'; then
    echo "============================================"
    echo "  [错误 / ERROR] 安装路径包含非 ASCII 字符 / Non-ASCII path"
    echo "  当前路径 / Current: $PWD"
    echo ""
    echo "  请把文件夹移到纯英文路径下,然后重新运行 ./restart.sh。"
    echo "  Move the folder to a pure-English path and re-run."
    echo "  推荐 / Recommended: ~/jzhu-trading/"
    echo "============================================"
    exit 1
fi

export HOST_INSTALL_PATH="$PWD"

# LAN IP detection (see start.sh for rationale). Silently empty on failure.
HOST_LAN_IP=$(ipconfig getifaddr en0 2>/dev/null)
[ -z "$HOST_LAN_IP" ] && HOST_LAN_IP=$(ipconfig getifaddr en1 2>/dev/null)
[ -z "$HOST_LAN_IP" ] && HOST_LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
export HOST_LAN_IP

# Check Docker Desktop
if ! docker info >/dev/null 2>&1; then
    echo "============================================"
    echo "  ❌ Docker Desktop 未启动"
    echo ""
    echo "  请先安装并启动 Docker Desktop（免费）："
    echo "  https://docker.com/products/docker-desktop"
    echo ""
    echo "  点击 Download Docker Desktop，选择匹配的版本："
    echo "    Mac (Apple芯片 M1/M2/M3/M4)  → Apple Silicon"
    echo "    Mac (Intel芯片)               → Intel Chip"
    echo "    Windows (绝大多数电脑)         → Windows AMD64"
    echo ""
    echo "  安装后无需注册登录，弹出的登录页面直接 Skip 跳过即可"
    echo "  启动 Docker Desktop 后重新运行 ./restart.sh"
    echo "============================================"
    exit 1
fi

# Stop our own containers first — releases their host-port bindings cleanly.
docker compose down 2>/dev/null

# If another Docker container is holding our ports, stop it too.
# (Never kill host processes ourselves: on Docker Desktop those ports may be
# held by com.docker.backend / vpnkit, and kill -9 would take Docker down.)
our_project=$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')
for port in 3000 8180; do
    while IFS=$'\t' read -r cid cname cproject; do
        [ -z "$cid" ] && continue
        if [ "$cproject" != "$our_project" ]; then
            echo "⚠️  端口 $port 被另一个 Docker 容器占用: $cname (项目: ${cproject:-无})"
            echo "   正在停止该容器..."
            docker stop "$cid" >/dev/null 2>&1
        fi
    done < <(docker ps --filter "publish=$port" --format "{{.ID}}	{{.Names}}	{{.Label \"com.docker.compose.project\"}}")
done

echo "正在检查更新并启动服务，请稍候..."
docker compose pull --quiet
docker compose up -d
echo ""
echo "============================================"
echo "  启动成功！正在打开浏览器..."
echo "  http://localhost:3000"
echo "============================================"

# Auto-open browser (works on Mac and Linux)
sleep 3
if command -v open >/dev/null 2>&1; then
    open http://localhost:3000
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://localhost:3000
fi
