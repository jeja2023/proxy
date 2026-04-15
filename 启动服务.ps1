# 代理中转一键启动脚本 (Windows)

Write-Host "开始启动 PhantomDrop 代理中转服务..." -ForegroundColor Cyan

# 检查 Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 Docker，请确保 Docker Desktop 已运行。"
    exit
}

# 检查配置文件
if (-not (Test-Path "config.json")) {
    Write-Error "未找到 config.json 配置文件。"
    exit
}

# 检查 docker compose 命令
$composeCmd = "docker-compose"
if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    if (docker compose version) {
        $composeCmd = "docker compose"
    } else {
        Write-Error "未找到 docker-compose。"
        exit
    }
}

# 启动容器
Write-Host "正在拉起容器..." -ForegroundColor Yellow
Invoke-Expression "$composeCmd up -d"

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n------------------------------------------------" -ForegroundColor Green
    Write-Host "通用代理服务已成功启动！"
    Write-Host "监听端口: 2080 (HTTP)"
    Write-Host "测试命令: curl.exe -x http://127.0.0.1:2080 https://www.google.com -I"
    Write-Host "------------------------------------------------" -ForegroundColor Green
} else {
    Write-Host "启动失败，请检查 Docker 日志。" -ForegroundColor Red
}
