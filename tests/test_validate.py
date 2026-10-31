import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.validate import (repair_memory_protocol, repair_anchor_protocol,
                             repair_mem_protocol, repair_used_protocol,
                             ensure_protocol)
from harness.parser import parse


class FakeClient:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(message={"content": self.content}, usage={})


class MemoryRepairTests(unittest.TestCase):
    def test_broken_event_is_repaired(self):
        text = '[[EVENT {"type":"fact","title":"坏行"}]\n'
        p = parse(text)
        self.assertEqual(p.events, [])
        fixed = ('[[EVENT {"type":"fact","title":"修好","text":"事实"}]]\n'
                 '[[EPISODE {"summary":"好卡","diary":"我在意"}]]')
        client = FakeClient(fixed)
        p2, repaired = repair_memory_protocol(client, [], text, p)
        self.assertTrue(repaired)
        self.assertEqual(client.calls, 1)
        self.assertEqual(p2.events[0]["title"], "修好")
        self.assertEqual(p2.episodes[0]["summary"], "好卡")

    def test_clean_text_does_not_call(self):
        text = '[[EVENT {"type":"fact","title":"正常"}]]'
        client = FakeClient("")
        _, repaired = repair_memory_protocol(client, [], text, parse(text))
        self.assertFalse(repaired)
        self.assertEqual(client.calls, 0)

    def test_anchor_repair(self):
        bad = [{"episode_id": "ep_abc", "anchor": "终身印在脑中", "status": "invalid"}]
        client = FakeClient("[[ANCHOR ep_abc T1:小明+头发, T2:小明+年龄]]")
        fixes = repair_anchor_protocol(client, [], "", bad, ["T1:小明+头发"])
        self.assertEqual(client.calls, 1)
        self.assertEqual(fixes, {"ep_abc": ["T1:小明+头发", "T2:小明+年龄"]})

    def test_mem_repair(self):
        parsed = parse("")
        client = FakeClient(
            "[[MEM trigger=T1:小明+头发 op=append body=小明头发很长]]")
        fixed, repaired = repair_mem_protocol(client, [], "", parsed)
        self.assertTrue(repaired)
        self.assertEqual(client.calls, 1)
        self.assertEqual(fixed.mem_ops[0]["trigger"], "T1:小明+头发")

    def test_state_repair_keeps_protocol_lines(self):
        text = ('正文\n[[ANCHOR ep_abc T1:小明+头发]]\n'
                '[[EPISODE {"summary":"卡","diary":"我"}]]')
        client = FakeClient(
            "[[STATE affection=+1 trust=+0 mood=平静 loc=书房 exit=0]]\n[[MEM none]]")
        vr = ensure_protocol(client, [], text, allow_missing=False)
        self.assertTrue(vr.repaired)
        self.assertEqual(vr.parsed.anchor_fixes, {"ep_abc": ["T1:小明+头发"]})
        self.assertEqual(vr.parsed.episodes[0]["summary"], "卡")

    def test_used_clear_or_read_is_allowed(self):
        client = FakeClient("")
        used, repaired = repair_used_protocol(
            client, [], "", ["ep_clear", "ep_read"],
            {"ep_clear": "clear"}, {"ep_read"})
        self.assertFalse(repaired)
        self.assertEqual(used, ["ep_clear", "ep_read"])
        self.assertEqual(client.calls, 0)

    def test_used_non_clear_is_repaired(self):
        client = FakeClient("[[USED ep_clear, ep_read]]")
        used, repaired = repair_used_protocol(
            client, [], "", ["ep_clear", "ep_bad"],
            {"ep_clear": "clear"}, {"ep_read"})
        self.assertTrue(repaired)
        self.assertEqual(client.calls, 1)
        self.assertEqual(used, ["ep_clear", "ep_read"])

    def test_mem_none_does_not_call(self):
        parsed = parse("[[MEM none]]")
        client = FakeClient("")
        fixed, repaired = repair_mem_protocol(client, [], "[[MEM none]]", parsed)
        self.assertFalse(repaired)
        self.assertEqual(client.calls, 0)
        self.assertEqual(fixed.mem_ops, [])


if __name__ == "__main__":
    unittest.main()
