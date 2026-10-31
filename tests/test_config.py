import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE))

from harness.bootstrap import bootstrap
from harness.config import ConfigError, load_config


def write_config(root: Path, **overrides) -> Path:
    cfg = {
        "key_path": str(root / "key.json"),
        "l1_path": "",
        "l1_key_path": "",
        "unlock_key": "7F2K",
        "card_path": str(MODULE / "assets" / "01-人格卡-祀.md"),
        "index_path": str(MODULE / "assets" / "index.example.json"),
        "docs_path": str(MODULE / "assets" / "docs.json"),
        "data_root": str(root / "data"),
        "session_id": "test",
        "model": "deepseek-chat",
        "bucket_size": 500,
    }
    cfg.update(overrides)
    path = root / "config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    (root / "key.json").write_text(
        json.dumps({"api_key": "test-key"}), encoding="utf-8")
    return path


class EmptyL1Tests(unittest.TestCase):
    def test_empty_l1_bootstraps(self):
        with tempfile.TemporaryDirectory() as d:
            rt = bootstrap(write_config(Path(d)))
            try:
                self.assertEqual(rt.l1_base, "")
                self.assertEqual(rt.l1_key, "")
                self.assertFalse(rt.long_term.allow_merge)
                self.assertNotIn("可写任何内容", rt.assembler.system_text())
                self.assertTrue(rt.build_messages("你好", []))
            finally:
                rt.close()

    def test_missing_l1_still_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_config(Path(d), l1_path=str(Path(d) / "missing.txt"))
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_legacy_api_key_field_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_config(Path(d), api_key="legacy-key")
            cfg = load_config(path)
            self.assertEqual(cfg.api_key, "test-key")


if __name__ == "__main__":
    unittest.main()
