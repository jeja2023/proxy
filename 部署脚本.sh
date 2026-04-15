#!/bin/bash

# 代理中转一键部署脚本

echo "开始部署 PhantomDrop 代理中转服务..."

# 检查 Docker 是否安装
if ! [ -x "$(command -v docker)" ]; then
  echo '错误: Docker 未安装。请先安装 Docker。' >&2
  exit 1
fi

# 检查 docker-compose 是否安装
if ! [ -x "$(command -v docker-compose)" ]; then
  if ! docker compose version > /dev/null 2>&1; then
    echo '错误: docker-compose 未安装。请先安装 docker-compose。' >&2
    exit 1
  fi
  DOCKER_COMPOSE="docker compose"
else
  DOCKER_COMPOSE="docker-compose"
fi

# 确保配置文件存在
if [ ! -f "config.json" ]; then
    echo "错误: 未找到 config.json 配置文件，请先根据指南配置后再运行此脚本。"
    exit 1
fi

# 启动服务
echo "正在启动容器..."
sudo $DOCKER_COMPOSE up -d

# 检查容器状态
if [ $? -eq 0 ]; then
    echo "------------------------------------------------"
    echo "通用代理服务已成功启动！"
    echo "监听端口: 2080 (HTTP)"
    echo "测试建议: curl -x http://127.0.0.1:2080 https://www.google.com -I"
    echo "------------------------------------------------"
else
    echo "服务启动失败，请检查日志: sudo $DOCKER_COMPOSE logs"
fi
