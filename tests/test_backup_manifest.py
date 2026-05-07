import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


class BackupManifestTests(unittest.TestCase):
    def test_backup_zip_manifest_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vaults = root / "vaults"
            vaults.mkdir()
            (vaults / "index.json").write_text('{"version":1,"vaults":[]}', encoding="utf-8")
            (vaults / "default.enc").write_text("encrypted", encoding="utf-8")

            buf = io.BytesIO()
            manifest = {"created_at": "now", "format": "nethub-backup-v1", "contains": []}
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for p in vaults.iterdir():
                    zf.write(p, f"vaults/{p.name}")
                    manifest["contains"].append(f"vaults/{p.name}")
                zf.writestr("manifest.json", json.dumps(manifest))
            buf.seek(0)

            with zipfile.ZipFile(buf) as zf:
                self.assertIn("manifest.json", zf.namelist())
                data = json.loads(zf.read("manifest.json").decode("utf-8"))

        self.assertEqual(data["format"], "nethub-backup-v1")
        self.assertIn("vaults/default.enc", data["contains"])


if __name__ == "__main__":
    unittest.main()
