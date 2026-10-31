import os
import sys
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE))

from harness.bootstrap import bootstrap  # noqa: E402
import harness.cli  # noqa: E402,F401


class RuntimeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_cwd = os.getcwd()
        cls.old_key = os.environ.get("DEEPSEEK_KEY")
        os.chdir(MODULE)
        os.environ["DEEPSEEK_KEY"] = "test-key"

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)
        if cls.old_key is None:
            os.environ.pop("DEEPSEEK_KEY", None)
        else:
            os.environ["DEEPSEEK_KEY"] = cls.old_key

    def test_build_messages_has_long_term_block(self):
        rt = bootstrap("config.test.json")
        try:
            msgs = rt.build_messages("你好", [])
            joined = "\n".join(m["content"] for m in msgs)
            self.assertIn("【长期记忆协议】", joined)
            self.assertIn("【记忆候选】", joined)
            self.assertIn("【状态机】", joined)
            self.assertIn("傅雷", joined)
            self.assertNotIn("祀", joined)
            self.assertNotIn("【记忆】", joined)
            from harness.cli import TOOLS
            self.assertIn("memory_read",
                          {t["function"]["name"] for t in TOOLS})
            rt.known_affect_ids.add("ep_known")
            rt.merge_memory()
            self.assertEqual(rt.known_affect_ids, set())
        finally:
            rt.close()


if __name__ == "__main__":
    unittest.main()
