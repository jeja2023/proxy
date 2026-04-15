#!/bin/bash

# ==========================================================
# PhantomDrop 代理中转 - 云服务器一键部署脚本
# ==========================================================

# 1. 定义工作目录 (使用当前用户的家目录)
WORK_DIR="$HOME/proxy-bridge"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR" || exit

echo "正在准备通用代理中转环境 (当前用户: $(whoami))..."

# 2. 生成 config.json
cat <<EOF > config.json
{
  "log": {
    "level": "info",
    "timestamp": true
  },
  "experimental": {
    "clash_api": {
      "external_controller": "0.0.0.0:9020",
      "external_ui": "ui",
      "secret": "phantom123",
      "default_mode": "proxy"
    }
  },
  "inbounds": [
    {
      "type": "http",
      "tag": "http-in",
      "listen": "0.0.0.0",
      "listen_port": 2080
    }
  ],
  "outbounds": [
    {
      "type": "tuic",
      "tag": "proxy",
      "server": "YOUR_SERVER_ADDRESS",
      "server_port": 8080,
      "uuid": "YOUR_UUID",
      "password": "YOUR_PASSWORD",
      "congestion_control": "bbr",
      "tls": {
        "enabled": true,
        "server_name": "YOUR_SERVER_NAME",
        "insecure": false,
        "alpn": ["h3"]
      }
    },
    {
      "type": "direct",
      "tag": "direct"
    }
  ],
  "route": {
    "rules": [
      {
        "outbound": "proxy",
        "network": ["tcp", "udp"]
      }
    ],
    "final": "proxy"
  }
}
EOF

# 3. 生成 docker-compose.yml
cat <<EOF > docker-compose.yml
services:
  proxy-bridge:
    image: ghcr.io/sagernet/sing-box:latest
    container_name: universal-proxy-bridge
    restart: always
    volumes:
      - ./config.json:/etc/sing-box/config.json
    ports:
      - "2080:2080"
      - "9020:9020"
    command: run -c /etc/sing-box/config.json
EOF

# 4. 检查并安装 Docker 环境
if ! [ -x "$(command -v docker)" ]; then
    echo "检测到未安装 Docker，正在尝试安装 (可能需要输入 sudo 密码)..."
    curl -fsSL https://get.docker.com | bash -s docker
    sudo systemctl enable --now docker
    # 将当前用户加入 docker 组，免 sudo 运行
    sudo usermod -aG docker "$USER"
    echo "已将 $USER 加入 docker 组，部分系统可能需要重新登录 SSH 才能生效。"
fi

# 5. 启动服务
echo "正在启动通用代理服务..."
if sudo docker compose version > /dev/null 2>&1; then
    sudo docker compose up -d
else
    sudo docker-compose up -d
fi

echo "=========================================================="
echo "部署完成！"
echo "工作目录: $WORK_DIR"
echo "代理端口: 2080 (HTTP)"
echo "提示: 请务必修改 \$WORK_DIR/config.json 中的节点信息后重启容器。"
echo "修改命令: vi \$WORK_DIR/config.json"
echo "重启命令: sudo docker restart universal-proxy-bridge"
echo "=========================================================="
