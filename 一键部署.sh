#!/bin/bash

# ==========================================================
# 网枢 NetHub - 云服务器一键部署脚本
# ==========================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}>>> 开始部署 网枢 NetHub 代理中转服务...${NC}"

# 1. 检查并安装 Docker 环境
if ! [ -x "$(command -v docker)" ]; then
    echo -e "${YELLOW}检测到未安装 Docker，正在尝试安装...${NC}"
    curl -fsSL https://get.docker.com | bash -s docker
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER" || true
    echo -e "${GREEN}Docker 安装完成。建议重新登录 SSH 以生效权限。${NC}"
fi

# 2. 检查 Docker Compose (V2 优先)
DOCKER_COMPOSE="docker compose"
if ! docker compose version > /dev/null 2>&1; then
    if [ -x "$(command -v docker-compose)" ]; then
        DOCKER_COMPOSE="docker-compose"
    else
        echo -e "${YELLOW}正在安装 Docker Compose 插件...${NC}"
        sudo apt-get update && sudo apt-get install -y docker-compose-plugin || sudo apt-get install -y docker-compose || sudo yum install -y docker-compose
    fi
fi

# 3. 配置文件初始化
echo -e "${GREEN}>>> 初始化配置文件...${NC}"

# 初始化 .env
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}已创建 .env 配置文件。${NC}"
    else
        echo -e "${RED}错误: 缺少 .env.example 模板文件。${NC}"
        exit 1
    fi
fi

# 生成随机密钥的函数
generate_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 24 | tr -d '\n'
    else
        cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1
    fi
}

update_env() {
    local key=$1
    local value=$2
    if grep -q "^${key}=" .env; then
        # 注意：这里使用简单替换，不考虑复杂转义
        sed -i "s|^${key}=.*|${key}=${value}|" .env
    else
        echo "${key}=${value}" >> .env
    fi
}

# 检查并补全关键配置
CLASH_SECRET=$(grep "^CLASH_API_SECRET=" .env | cut -d'=' -f2 | xargs)
if [ -z "$CLASH_SECRET" ]; then
    AUTO_SECRET=$(generate_secret)
    echo -e "${YELLOW}已自动生成 CLASH_API_SECRET 随机密钥。${NC}"
    update_env "CLASH_API_SECRET" "$AUTO_SECRET"
fi

PANEL_PASS=$(grep "^PANEL_ADMIN_PASSWORD=" .env | cut -d'=' -f2 | xargs)
if [ -z "$PANEL_PASS" ]; then
    echo -e "${YELLOW}请设置管理面板的管理员密码 (PANEL_ADMIN_PASSWORD): ${NC}"
    read -r INPUT_PASS
    if [ -z "$INPUT_PASS" ]; then
        INPUT_PASS=$(generate_secret | cut -c1-12)
        echo -e "${YELLOW}未输入密码，已自动生成临时密码: ${INPUT_PASS}${NC}"
    fi
    update_env "PANEL_ADMIN_PASSWORD" "$INPUT_PASS"
fi

SESSION_SECRET=$(grep "^PANEL_SESSION_SECRET=" .env | cut -d'=' -f2 | xargs)
if [ -z "$SESSION_SECRET" ]; then
    AUTO_SESSION=$(generate_secret)
    update_env "PANEL_SESSION_SECRET" "$AUTO_SESSION"
fi

# 提示用户配置 HTTP 代理的公网端口
PROXY_PORT=$(grep "^SINGBOX_HTTP_PORT=" .env | cut -d'=' -f2 | xargs)
if [ -z "$PROXY_PORT" ] || [ "$PROXY_PORT" = "5986" ] || [ "$PROXY_PORT" = "2080" ]; then
    echo -e "${YELLOW}请输入 HTTP 代理外网宿主机端口 (默认 5986，强烈建议改为高位随机端口如 34567 以增强安全性防扫描): ${NC}"
    read -r INPUT_PORT
    if [ -z "$INPUT_PORT" ]; then
        INPUT_PORT="5986"
        echo -e "${YELLOW}已确认使用 HTTP 代理默认端口: ${INPUT_PORT}${NC}"
    else
        if [[ "$INPUT_PORT" =~ ^[0-9]+$ ]] && [ "$INPUT_PORT" -ge 1 ] && [ "$INPUT_PORT" -le 65535 ]; then
            echo -e "${GREEN}HTTP 代理外网端口已设置为: ${INPUT_PORT}${NC}"
        else
            echo -e "${RED}输入不是合法的端口号 (1-65535)，将退回使用默认端口: 5986${NC}"
            INPUT_PORT="5986"
        fi
    fi
    update_env "SINGBOX_HTTP_PORT" "$INPUT_PORT"
fi

# 提示用户配置管理面板的外网端口
PANEL_PORT=$(grep "^PANEL_PORT=" .env | cut -d'=' -f2 | xargs)
if [ -z "$PANEL_PORT" ] || [ "$PANEL_PORT" = "8080" ]; then
    echo -e "${YELLOW}请输入管理面板外网访问端口 (默认 8080，建议修改为高位随机端口如 45678 以提升保密性): ${NC}"
    read -r INPUT_PANEL_PORT
    if [ -z "$INPUT_PANEL_PORT" ]; then
        INPUT_PANEL_PORT="8080"
        echo -e "${YELLOW}已确认使用管理面板默认端口: ${INPUT_PANEL_PORT}${NC}"
    else
        if [[ "$INPUT_PANEL_PORT" =~ ^[0-9]+$ ]] && [ "$INPUT_PANEL_PORT" -ge 1 ] && [ "$INPUT_PANEL_PORT" -le 65535 ]; then
            echo -e "${GREEN}管理面板外网端口已设置为: ${INPUT_PANEL_PORT}${NC}"
        else
            echo -e "${RED}输入不是合法的端口号 (1-65535)，将退回使用默认端口: 8080${NC}"
            INPUT_PANEL_PORT="8080"
        fi
    fi
    update_env "PANEL_PORT" "$INPUT_PANEL_PORT"
fi

# 初始化 config.json (如果不存在)
if [ ! -f "config.json" ]; then
    echo -e "${YELLOW}正在创建基础 config.json...${NC}"
    # 获取最新的 secret 保证一致性
    CURRENT_SECRET=$(grep "^CLASH_API_SECRET=" .env | cut -d'=' -f2 | xargs)
    cat > config.json <<EOF
{
  "log": {
    "level": "info",
    "timestamp": true
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
      "type": "selector",
      "tag": "代理选择",
      "outbounds": ["direct"],
      "default": "direct"
    },
    {
      "type": "direct",
      "tag": "direct"
    }
  ],
  "route": {
    "rules": [
      {
        "inbound": ["http-in"],
        "outbound": "代理选择"
      }
    ],
    "final": "代理选择"
  },
  "experimental": {
    "clash_api": {
      "external_controller": "0.0.0.0:9020",
      "secret": "${CURRENT_SECRET}",
      "default_mode": "rule"
    }
  }
}
EOF
fi

# 3.5 权限修复 (针对 Docker 容器内的 UID 10001)
echo -e "${GREEN}>>> 修复目录权限...${NC}"
mkdir -p data/vaults
# 统一将数据目录 and 配置文件所有权交给容器内的非 root 用户 (UID 10001)
sudo chown -R 10001:10001 data config.json || true
sudo chmod -R 775 data config.json || true

# 4. 启动服务
echo -e "${GREEN}>>> 正在拉取镜像并启动容器服务...${NC}"
sudo $DOCKER_COMPOSE --profile panel up -d --build

# 5. 状态检查与输出
if [ $? -eq 0 ]; then
    # 尝试获取公网 IP
    IP_ADDR=$(curl -s --max-time 3 https://api64.ipify.org || curl -s --max-time 3 https://ifconfig.me || echo "您的服务器IP")
    
    CURRENT_PANEL_PORT=$(grep "^PANEL_PORT=" .env | cut -d'=' -f2 | xargs || echo "8080")
    if [ -z "$CURRENT_PANEL_PORT" ]; then
        CURRENT_PANEL_PORT="8080"
    fi

    echo -e "\n${GREEN}================================================${NC}"
    echo -e "${GREEN}部署成功！网枢 NetHub 已准备就绪。${NC}"
    echo -e "------------------------------------------------"
    echo -e "管理面板地址: ${YELLOW}http://${IP_ADDR}:${CURRENT_PANEL_PORT}${NC}"
    echo -e "管理员账号:   ${YELLOW}$(grep "^PANEL_ADMIN_USER=" .env | cut -d'=' -f2 | xargs || echo "admin")${NC}"
    echo -e "管理员密码:   ${YELLOW}$(grep "^PANEL_ADMIN_PASSWORD=" .env | cut -d'=' -f2 | xargs)${NC}"
    echo -e "------------------------------------------------"
    PROXY_PORT=$(grep "^SINGBOX_HTTP_PORT=" .env | cut -d'=' -f2 | xargs || echo "5986")
    if [ -z "$PROXY_PORT" ]; then
        PROXY_PORT="5986"
    fi
    echo -e "HTTP 代理端口: ${YELLOW}${PROXY_PORT}${NC}"
    echo -e "测试命令: curl -x http://127.0.0.1:${PROXY_PORT} https://www.google.com -I"
    echo -e "------------------------------------------------"
    echo -e "常用管理命令:"
    echo -e "  查看实时日志: sudo $DOCKER_COMPOSE logs -f"
    echo -e "  重启所有服务: sudo $DOCKER_COMPOSE --profile panel restart"
    echo -e "  更新并重建:   sudo $DOCKER_COMPOSE --profile panel up -d --build"
    echo -e "  停止并卸载:   sudo $DOCKER_COMPOSE --profile panel down"
    echo -e "${GREEN}================================================${NC}"
else
    echo -e "${RED}部署失败，请执行 'sudo $DOCKER_COMPOSE logs' 查看原因。${NC}"
fi
