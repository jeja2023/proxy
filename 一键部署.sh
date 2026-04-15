#!/bin/bash

# ==========================================================
# Proxy Bridge - 云服务器一键部署脚本
# ==========================================================

echo "开始部署代理中转服务..."

# 1. 检查并安装 Docker 环境
if ! [ -x "$(command -v docker)" ]; then
    echo "检测到未安装 Docker，正在尝试安装..."
    curl -fsSL https://get.docker.com | bash -s docker
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
    echo "Docker 安装完成。建议重新登录 SSH 以生效权限。"
fi

# 2. 检查 docker-compose
if ! [ -x "$(command -v docker-compose)" ]; then
    if ! docker compose version > /dev/null 2>&1; then
        echo "正在安装 docker-compose..."
        sudo apt update && sudo apt install docker-compose -y
    fi
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

# 3. 确保配置文件存在
if [ ! -f "config.json" ]; then
    if [ -f "config.json.example" ]; then
        echo "未找到 config.json，正在根据模板创建示例..."
        cp config.json.example config.json
    else
        echo "错误: 缺少配置模板文件。"
        exit 1
    fi
fi

# 4. 启动服务
echo "正在启动容器服务..."
sudo $DOCKER_COMPOSE up -d

# 5. 状态检查
if [ $? -eq 0 ]; then
    echo "------------------------------------------------"
    echo "部署成功！"
    echo "代理端口: 2080 (HTTP)"
    echo "测试命令: curl -x http://127.0.0.1:2080 https://www.google.com -I"
    echo "------------------------------------------------"
else
    echo "部署失败，请执行 'docker compose logs' 查看原因。"
fi
