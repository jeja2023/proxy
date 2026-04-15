# Proxy Bridge 代理中转服务

本工具用于将复杂的加密协议（VLESS, Hysteria2, TUIC, Shadowsocks）桥接转换为标准 HTTP 代理，主要用于支持 OpenAI 自动化注册等仅支持标准协议的项目。

## 🚀 快速开始

### 1. 准备节点
1. 打开 `generate_singbox.py`。
2. 将您的节点链接粘贴到 `urls` 列表中。
3. 运行转换脚本：`python generate_singbox.py`。
   - 这将在本地生成通用的 `config.json`。

### 2. 在服务器部署
如果您在云服务器上运行，请直接执行整合后的部署脚本：
```bash
chmod +x 一键部署.sh
./一键部署.sh
```
*脚本会自动检查并安装 Docker 环境（如果缺失），并启动中转服务。*

### 3. 在 Windows 本地运行
右键点击 `启动服务.ps1`，选择“使用 PowerShell 运行”即可。

---

## 🛠️ 运维与验证

- **验证代理**: `curl -x http://127.0.0.1:2080 https://www.google.com -I`
- **查看日志**: `docker compose logs -f`
- **重启服务**: `docker compose restart`
- **停止服务**: `docker compose down`

## 📁 文件说明
- `config.json`: 核心配置文件（由脚本生成）。
- `config.json.example`: 配置模板（不含私密信息）。
- `generate_singbox.py`: 节点转换工具。
- `docker-compose.yml`: Docker 编排配置。
- `一键部署.sh`: Linux 环境自动化部署脚本。
- `启动服务.ps1`: Windows 环境启动脚本。

---
> [!IMPORTANT]
> **安全提示**：默认监听端口为 `2080`。在公网服务器运行时，请务必在安全组中限制来源 IP，或仅限本地访问，防止代理被盗用。
