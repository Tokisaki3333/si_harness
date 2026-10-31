import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.cli import fold_tool_blocks
from harness.assembler import Assembler
from harness.models import Turn
from harness.long_term import LongTermMemory
from harness.store import MemoryDB, Session


class IndexTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ltm = LongTermMemory(self.tmp.name, "t")

    def tearDown(self):
        self.ltm.close()
        self.tmp.cleanup()

    def test_clear_then_fuzzy(self):
        ep = self.ltm.add_episode({"summary": "一", "diary": "我"}, turn=1)
        self.ltm.index_upsert(ep, "episode", 1)
        self.assertEqual(self.ltm.index_clarity()[ep], "clear")
        self.ltm.index_apply_compression([1], [])
        self.assertEqual(self.ltm.index_clarity()[ep], "fuzzy")

    def test_recent_stays_clear(self):
        ep = self.ltm.add_episode({"summary": "一", "diary": "我"}, turn=3)
        self.ltm.index_upsert(ep, "episode", 3)
        self.ltm.index_apply_compression([1, 2], [3])
        self.assertEqual(self.ltm.index_clarity()[ep], "clear")

    def test_render_block_skips_clear(self):
        clear = self.ltm.add_episode({"summary": "清晰", "diary": "我"}, turn=1)
        forgotten = self.ltm.add_episode({"summary": "遗忘", "diary": "我"}, turn=2)
        self.ltm.index_upsert(clear, "episode", 1)
        self.ltm.index_upsert(forgotten, "episode", 2)
        self.ltm.index_mark([forgotten], "forgotten", 3)
        block = self.ltm.render_block(limit=10, full_count=0)
        self.assertNotIn(clear, block)
        self.assertIn(forgotten, block)

    def test_missing_index_row_is_treated_as_forgotten(self):
        ep = self.ltm.add_episode({"summary": "旧库", "diary": "我"}, turn=1)
        block = self.ltm.render_block(limit=10, full_count=0)
        self.assertIn(ep, block)

    def test_folded_target_is_marked_clear(self):
        ep = self.ltm.add_episode({"summary": "卡", "diary": "我"}, turn=1)
        self.ltm.index_upsert(ep, "episode", 1)
        self.ltm.index_mark([ep], "fuzzy", 2)
        self.ltm.index_clear([ep], 3)
        self.assertEqual(self.ltm.index_clarity()[ep], "clear")

    def test_reconcile_forgets_deleted_turns(self):
        ep = self.ltm.add_episode({"summary": "卡", "diary": "我"}, turn=1)
        self.ltm.index_upsert(ep, "episode", 1)
        self.assertEqual(self.ltm.index_reconcile({1, 2}), 0)
        self.assertEqual(self.ltm.index_reconcile({2}), 1)
        self.assertEqual(self.ltm.index_clarity()[ep], "forgotten")


class SessionToolsTests(unittest.TestCase):
    def test_reload_window_groups_tools_by_turn(self):
        tmp = tempfile.TemporaryDirectory()
        session = Session(tmp.name, "s", 10)
        try:
            session.append_turn("u1", "a1", turn=1,
                                tools=["[memory_read ep_1] 正文"])
            session.append_turn("u2", "a2", turn=2,
                                tools=["[memory_read ep_2] 正文2"])
            window = session.reload_window()
            self.assertEqual([t.index for t in window], [1, 2])
            self.assertEqual(window[0].tools, ["[memory_read ep_1] 正文"])
            self.assertEqual(window[1].tools, ["[memory_read ep_2] 正文2"])
        finally:
            session.close()
            tmp.cleanup()


class FoldToolBlocksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MemoryDB(self.tmp.name, "s")
        self.row = self.db.upsert("T1:小明+头发", "append", "黑发", turn=1)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_only_used_targets_are_folded(self):
        calls = [
            {"name": "memory_read", "args": {"target": f"mem:{self.row['id']}"},
             "output": f"T1:小明+头发：黑发"},
            {"name": "memory_read", "args": {"target": "mem:999"},
             "output": "T1:无关：内容"},
        ]
        blocks, cleared = fold_tool_blocks(
            calls, [f"mem:{self.row['id']}"], self.db)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].startswith(f"[memory_read mem:{self.row['id']}]"))
        self.assertEqual(cleared, ["T1:小明+头发"])

    def test_unused_is_not_folded(self):
        calls = [{"name": "memory_read",
                  "args": {"target": f"mem:{self.row['id']}"}, "output": "x"}]
        blocks, cleared = fold_tool_blocks(calls, [], self.db)
        self.assertEqual(blocks, [])
        self.assertEqual(cleared, [])

    def test_all_blocks_folded_without_budget(self):
        for i in range(19):
            self.db.upsert(f"T1:甲{i}+乙", "append", "x" * 1000, turn=1)
        rows = self.db.all()
        calls = [{"name": "memory_read", "args": {"target": f"mem:{r['id']}"},
                  "output": "x" * 1000} for r in rows]
        used = [f"mem:{r['id']}" for r in rows]
        blocks, cleared = fold_tool_blocks(calls, used, self.db)
        self.assertEqual(len(blocks), 20)
        self.assertEqual(len(cleared), 20)


class AssemblerOrderTests(unittest.TestCase):
    def test_tools_between_user_and_assistant(self):
        asm = Assembler(["sys"], "card")
        msgs = asm.build("u", [Turn("u1", "a1", index=1,
                                    tools=["[memory_read ep_1] x"])])
        self.assertEqual([m["content"] for m in msgs],
                         ["sys", "card", "[轮1] u1",
                          "[memory_read ep_1] x", "a1", "[轮2] u"])
        self.assertEqual([m["role"] for m in msgs],
                         ["system", "user", "user", "user", "assistant", "user"])

    def test_prefix_is_stable(self):
        asm = Assembler(["sys"], "card")
        first = asm.build("u1", [])
        second = asm.build("u2", [Turn("u1", "a1", index=1)])
        self.assertEqual(first[:2], second[:2])

    def test_user_input_before_dynamic_blocks(self):
        asm = Assembler(["sys"], "card")
        msgs = asm.build("u", [Turn("u1", "a1", index=1)],
                         scene="【场景】", state_line="【状态机】",
                         long_term_block="【记忆候选】")
        self.assertEqual([m["content"] for m in msgs],
                         ["sys", "card", "[轮1] u1", "a1", "[轮2] u",
                          "【场景】", "【状态机】", "【记忆候选】"])

    def test_compressed_lines_are_separate_messages(self):
        asm = Assembler(["sys"], "card")
        msgs = asm.build("u", [], compressed=["摘要一", "摘要二"])
        self.assertEqual([m["content"] for m in msgs],
                         ["sys", "card", "摘要一", "摘要二", "[轮1] u"])


if __name__ == "__main__":
    unittest.main()
