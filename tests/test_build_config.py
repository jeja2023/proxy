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


if __name__ == "__main__":
    unittest.main()
