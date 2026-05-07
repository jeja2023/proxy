import unittest
import os
import tempfile
from pathlib import Path

import yaml

from core.build_config import build_singbox_config, dedupe_urls, generate_clash_yaml, parse_urls_text


class BuildConfigTests(unittest.TestCase):
    def test_parse_urls_text_ignores_blank_lines_and_comments(self) -> None:
        text = """
        # comment

        vless://user@example.com:443?type=tcp#node-a
        ss://YWVzLTEyOC1nY206cGFzcw@example.org:8388#node-b
        """

        urls = parse_urls_text(text)

        self.assertEqual(len(urls), 2)
        self.assertIn("#node-a", urls[0])
        self.assertIn("#node-b", urls[1])

    def test_dedupe_urls_preserves_first_occurrence(self) -> None:
        urls = [" vless://a@example.com:443#one ", "ss://b@example.com:8388#two", "vless://a@example.com:443#one"]

        unique, duplicate_count = dedupe_urls(urls)

        self.assertEqual(unique, ["vless://a@example.com:443#one", "ss://b@example.com:8388#two"])
        self.assertEqual(duplicate_count, 1)

    def test_generate_clash_yaml_escapes_special_characters(self) -> None:
        proxies = [
            {
                "name": "node: one # primary",
                "type": "hysteria2",
                "server": "example.com",
                "port": 443,
                "password": "pass:with#chars",
                "sni": "example.com",
            }
        ]

        data = yaml.safe_load(generate_clash_yaml(proxies))

        self.assertEqual(data["proxies"][0]["name"], "node: one # primary")
        self.assertEqual(data["proxies"][0]["password"], "pass:with#chars")
        self.assertEqual(data["proxy-groups"][0]["proxies"], ["node: one # primary"])

    def test_build_singbox_config_applies_template_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            template = Path(td) / "template.json"
            template.write_text('{"log": {"level": "debug"}}', encoding="utf-8")
            old = os.environ.get("SINGBOX_TEMPLATE_PATH")
            os.environ["SINGBOX_TEMPLATE_PATH"] = str(template)
            try:
                cfg = build_singbox_config(["vless://00000000-0000-0000-0000-000000000000@example.com:443?type=tcp#one"])
            finally:
                if old is None:
                    os.environ.pop("SINGBOX_TEMPLATE_PATH", None)
                else:
                    os.environ["SINGBOX_TEMPLATE_PATH"] = old

        self.assertEqual(cfg["log"]["level"], "debug")

    def test_build_singbox_config_adds_https_proxy_inbound(self) -> None:
        updates = {
            "SINGBOX_HTTPS_PROXY_ENABLED": "1",
            "SINGBOX_HTTPS_PROXY_PORT": "2443",
            "SINGBOX_TLS_CERT_PATH": "/certs/fullchain.pem",
            "SINGBOX_TLS_KEY_PATH": "/certs/privkey.pem",
            "SINGBOX_TLS_SERVER_NAME": "proxy.example.com",
        }
        old = {key: os.environ.get(key) for key in updates}
        os.environ.update(updates)
        try:
            cfg = build_singbox_config(["vless://00000000-0000-0000-0000-000000000000@example.com:443?type=tcp#one"])
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        inbound = next(item for item in cfg["inbounds"] if item["tag"] == "https-in")
        self.assertEqual(inbound["listen_port"], 2443)
        self.assertTrue(inbound["tls"]["enabled"])
        self.assertEqual(inbound["tls"]["certificate_path"], "/certs/fullchain.pem")
        self.assertEqual(inbound["tls"]["key_path"], "/certs/privkey.pem")
        self.assertEqual(inbound["tls"]["server_name"], "proxy.example.com")

    def test_https_proxy_requires_certificate_paths(self) -> None:
        old_enabled = os.environ.get("SINGBOX_HTTPS_PROXY_ENABLED")
        old_cert = os.environ.get("SINGBOX_TLS_CERT_PATH")
        old_key = os.environ.get("SINGBOX_TLS_KEY_PATH")
        os.environ["SINGBOX_HTTPS_PROXY_ENABLED"] = "1"
        os.environ.pop("SINGBOX_TLS_CERT_PATH", None)
        os.environ.pop("SINGBOX_TLS_KEY_PATH", None)
        try:
            with self.assertRaises(ValueError):
                build_singbox_config(["vless://00000000-0000-0000-0000-000000000000@example.com:443?type=tcp#one"])
        finally:
            for key, value in {
                "SINGBOX_HTTPS_PROXY_ENABLED": old_enabled,
                "SINGBOX_TLS_CERT_PATH": old_cert,
                "SINGBOX_TLS_KEY_PATH": old_key,
            }.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
