import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
PANEL_DIR = ROOT / "panel"
if str(PANEL_DIR) not in sys.path:
    sys.path.insert(0, str(PANEL_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from panel import main as panel_main


def config_with_users(users=None):
    inbound = {
        "type": "http",
        "tag": "http-in",
        "listen": "0.0.0.0",
        "listen_port": 2080,
    }
    if users is not None:
        inbound["users"] = users
    return {"inbounds": [inbound], "outbounds": []}


class ProxyAuthReloadTests(unittest.TestCase):
    def test_no_auth_change_does_not_close_connections(self) -> None:
        old = config_with_users([{"username": "u", "password": "p"}])
        new = config_with_users([{"username": "u", "password": "p"}])

        self.assertFalse(panel_main._proxy_auth_change_requires_connection_close(old, new))

    def test_enabling_proxy_auth_closes_existing_connections(self) -> None:
        old = config_with_users()
        new = config_with_users([{"username": "u", "password": "p"}])

        self.assertTrue(panel_main._proxy_auth_change_requires_connection_close(old, new))

    def test_changing_proxy_credentials_closes_existing_connections(self) -> None:
        old = config_with_users([{"username": "u", "password": "old"}])
        new = config_with_users([{"username": "u", "password": "new"}])

        self.assertTrue(panel_main._proxy_auth_change_requires_connection_close(old, new))

    def test_disabling_proxy_auth_closes_existing_connections(self) -> None:
        old = config_with_users([{"username": "u", "password": "p"}])
        new = config_with_users()

        self.assertTrue(panel_main._proxy_auth_change_requires_connection_close(old, new))


class ProxyAuthStartupCleanupTests(unittest.IsolatedAsyncioTestCase):
    def _mock_api_ready(self):
        """模拟 Clash API 已就绪的响应"""
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        return mock_resp

    async def test_startup_closes_connections_when_proxy_auth_is_required(self) -> None:
        with (
            patch.object(panel_main, "_ensure_config_auth_matches_env", return_value=False),
            patch.object(panel_main, "_http_proxy_auth_required", return_value=True),
            patch.object(panel_main, "_close_proxy_connections_sync", return_value=True) as close_mock,
            patch.object(panel_main, "_sync_clash_api_request", return_value=self._mock_api_ready()),
            patch.object(panel_main.asyncio, "sleep", new=AsyncMock()),
        ):
            await panel_main._enforce_proxy_auth_connections_on_startup()

        # 多轮清理，应调用多次
        self.assertGreaterEqual(close_mock.call_count, 1)

    async def test_startup_skips_cleanup_when_proxy_auth_is_not_required(self) -> None:
        with (
            patch.object(panel_main, "_ensure_config_auth_matches_env", return_value=False),
            patch.object(panel_main, "_http_proxy_auth_required", return_value=False),
            patch.object(panel_main, "_close_proxy_connections_sync", return_value=True) as close_mock,
            patch.object(panel_main, "_sync_clash_api_request", return_value=self._mock_api_ready()),
            patch.object(panel_main.asyncio, "sleep", new=AsyncMock()),
        ):
            await panel_main._enforce_proxy_auth_connections_on_startup()

        close_mock.assert_not_called()

    async def test_startup_skips_when_api_not_ready(self) -> None:
        with (
            patch.object(panel_main, "_http_proxy_auth_required", return_value=True),
            patch.object(panel_main, "_close_proxy_connections_sync", return_value=True) as close_mock,
            patch.object(panel_main, "_sync_clash_api_request", return_value=None),
            patch.object(panel_main.asyncio, "sleep", new=AsyncMock()),
        ):
            await panel_main._enforce_proxy_auth_connections_on_startup()

        # API 未就绪时不应尝试断开连接
        close_mock.assert_not_called()

    async def test_startup_patches_config_and_reloads_when_auth_missing(self) -> None:
        """环境变量已设置鉴权但 config.json 中缺少 users 时，应补写并重载内核"""
        with (
            patch.object(panel_main, "_ensure_config_auth_matches_env", return_value=True) as patch_mock,
            patch.object(panel_main, "_reload_singbox_config_sync", return_value=True) as reload_mock,
            patch.object(panel_main, "_http_proxy_auth_required", return_value=True),
            patch.object(panel_main, "_close_proxy_connections_sync", return_value=True) as close_mock,
            patch.object(panel_main, "_sync_clash_api_request", return_value=self._mock_api_ready()),
            patch.object(panel_main.asyncio, "sleep", new=AsyncMock()),
        ):
            await panel_main._enforce_proxy_auth_connections_on_startup()

        # 配置被修正后应触发重载
        patch_mock.assert_called_once()
        reload_mock.assert_called_once()
        # 重载后应断开连接
        self.assertGreaterEqual(close_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
