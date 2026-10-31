import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.bootstrap import Runtime
from harness.long_term import LongTermMemory, _semantic_score
from harness.parser import parse
from harness.store import MemoryDB


class ParserLongTermTests(unittest.TestCase):
    def test_event_episode_used(self):
        text = (
            "【心念】有点在意。\n【正文】嗯。\n"
            '[[EVENT {"type":"commitment","title":"周三吃 KFC","text":"他说周三去","people":["他"]}]]\n'
            '[[EPISODE {"v":4,"a":3,"agency":4,"self_focus":2,"summary":"他说周三去","diary":"我记下了。"}]]\n'
            "[[USED ep_aaaaaaaa, ep_bbbbbbbb]]\n"
            "[[USER_STATE v=2 a=4 agency=3 self_focus=5]]\n"
            "[[STATE affection=+1 trust=+0 mood=平静 exit=0]]"
        )
        p = parse(text)
        self.assertEqual(p.body, "嗯。")
        self.assertEqual(p.events[0]["title"], "周三吃 KFC")
        self.assertEqual(p.episodes[0]["v"], 4)
        self.assertEqual(p.used, ["ep_aaaaaaaa", "ep_bbbbbbbb"])
        self.assertEqual(p.user_state, {"v": 2, "a": 4, "agency": 3, "self_focus": 5})
        self.assertEqual(p.state_delta["affection"], 1)

    def test_malformed_event_does_not_swallow_episode(self):
        p = parse('[[EVENT {"type":"fact","title":"坏行"}]\n'
                  '[[EPISODE {"summary":"好卡","diary":"我在意"}]]')
        self.assertEqual(p.events, [])
        self.assertEqual(len(p.episodes), 1)
        self.assertEqual(p.episodes[0]["summary"], "好卡")

    def test_none_tags(self):
        p = parse("正文\n[[EVENT none]]\n[[EPISODE none]]\n[[USED none]]")
        self.assertEqual(p.body, "正文")
        self.assertEqual(p.events, [])
        self.assertEqual(p.episodes, [])
        self.assertEqual(p.used, [])

    def test_anchor_fix_lines(self):
        p = parse("正文\n[[ANCHOR ep_aaaaaaaa T1:小明+头发, T2:小明+年龄]]\n"
                  "[[ANCHOR none]]")
        self.assertEqual(p.body, "正文")
        self.assertEqual(p.anchor_fixes,
                         {"ep_aaaaaaaa": ["T1:小明+头发", "T2:小明+年龄"]})


class LongTermStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ltm = LongTermMemory(self.tmp.name, "test")

    def tearDown(self):
        self.ltm.close()
        self.tmp.cleanup()

    def test_event_and_episode_roundtrip(self):
        ev = self.ltm.add_event({"type": "plan", "title": "周三 KFC", "text": "约好周三"}, turn=1)
        ep = self.ltm.add_episode({"v": 4, "a": 3, "agency": 4, "self_focus": 2,
                                   "summary": "他说周三去", "diary": "我记下了。"}, turn=1)
        self.assertTrue(ev and ev.startswith("ev_"))
        self.assertTrue(ep and ep.startswith("ep_"))
        rows = self.ltm.recent_episodes()
        self.assertEqual(rows[0]["id"], ep)
        self.assertIn("【长期记忆协议】", self.ltm.render_block())
        self.assertIn(ep, self.ltm.last_ids)

    def test_used_increments(self):
        ep = self.ltm.add_episode({"summary": "一句话", "diary": "一点感受"}, turn=1)
        self.assertEqual(self.ltm.mark_used([ep], turn=2), 1)
        row = self.ltm.recent_episodes()[0]
        self.assertEqual(row["used_count"], 1)
        self.assertIn("USED 1", self.ltm.debug_line())

    def test_episode_version_chain(self):
        self.ltm.allow_merge = True
        first = self.ltm.add_episode({"thread_id": "t1", "anchors": ["ev_1"],
                                      "summary": "a", "diary": "a"}, turn=1)
        second = self.ltm.add_episode({"thread_id": "t1", "summary": "b",
                                       "diary": "b"}, turn=2)
        third = self.ltm.add_episode({"thread_id": "t1", "summary": "c",
                                      "diary": "c"}, turn=3)
        fourth = self.ltm.add_episode({"thread_id": "t1", "summary": "d",
                                       "diary": "d"}, turn=4)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertNotEqual(third, fourth)
        rows = [dict(r) for r in self.ltm.conn.execute(
            "SELECT id,revision,status,parent_id FROM episodes ORDER BY rowid")]
        self.assertEqual([r["revision"] for r in rows], [3, 1])
        self.assertEqual(rows[0]["status"], "sealed")
        self.assertEqual(rows[1]["parent_id"], rows[0]["id"])

    def test_thread_id_primary(self):
        self.ltm.allow_merge = True
        a = self.ltm.add_episode({"thread_id": "x", "v": 3, "a": 3,
                                  "agency": 3, "self_focus": 3,
                                  "anchors": ["乐理"], "summary": "一", "diary": "我"}, turn=1)
        b = self.ltm.add_episode({"thread_id": "x", "v": 5, "a": 5,
                                  "agency": 5, "self_focus": 5,
                                  "anchors": ["领导"], "summary": "二", "diary": "我"}, turn=2)
        c = self.ltm.add_episode({"thread_id": "y", "v": 5, "a": 5,
                                  "agency": 5, "self_focus": 5,
                                  "anchors": ["领导"], "summary": "三", "diary": "我"}, turn=3)
        self.assertEqual(a, b)
        self.assertNotEqual(b, c)

    def test_no_merge_mode(self):
        self.ltm.allow_merge = False
        a = self.ltm.add_episode({"thread_id": "x", "summary": "一", "diary": "我"}, turn=1)
        b = self.ltm.add_episode({"thread_id": "x", "summary": "二", "diary": "我"}, turn=2)
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.ltm.all_episodes()), 2)

    def test_render_excludes_known(self):
        ep = self.ltm.add_episode({"summary": "已知候选", "diary": "日记"}, turn=1)
        block = self.ltm.render_block(exclude_ids=[ep])
        self.assertNotIn(ep, block)

    def test_index_folders_and_read(self):
        ev = self.ltm.add_event({"type": "plan", "title": "周三 KFC", "text": "约好"}, turn=1)
        folders = {f["id"] for f in self.ltm.list_folders()}
        self.assertIn("commitments", folders)
        rows = self.ltm.list_folder("commitments")
        self.assertIn(ev, {r["id"] for r in rows})
        rec = self.ltm.read_entry(ev)
        self.assertEqual(rec["_type"], "event")
        self.assertEqual(rec["title"], "周三 KFC")
        self.assertIn(ev, {r["id"] for r in self.ltm.search("KFC")})

    def test_anchors_render(self):
        self.ltm.add_episode({"summary": "锚点卡", "diary": "日记",
                              "anchors": ["T1:ev+test"]}, turn=1)
        block = self.ltm.render_block(full_count=1)
        self.assertIn("锚点：T1:ev+test", block)

    def test_bind_anchors_existing_missing_invalid(self):
        ep = self.ltm.add_episode({
            "anchors": ["T1:周三+KFC", "T2:小明+头发", "这是一个超过十个字的客观索引短语"],
            "summary": "锚点绑定", "diary": "我"}, turn=1)
        counts = self.ltm.bind_anchors({"T1:周三+KFC"}, episode_ids=[ep])
        self.assertEqual(counts, {"bound": 1, "missing": 1, "invalid": 1})
        links = {x["anchor"]: x for x in self.ltm.links_for(ep)}
        self.assertEqual(links["T1:周三+KFC"]["status"], "bound")
        self.assertEqual(links["T1:周三+KFC"]["objective_trigger"], "T1:周三+KFC")
        self.assertEqual(links["T2:小明+头发"]["status"], "missing")
        self.assertEqual(links["这是一个超过十个字的客观索引短语"]["status"], "invalid")
        self.assertEqual(self.ltm.episodes_for("T1:周三+KFC"), [ep])

    def test_bind_new_objective_same_turn(self):
        ep = self.ltm.add_episode({"anchors": ["T1:新+约定"], "summary": "新约定",
                                   "diary": "我"}, turn=2)
        counts = self.ltm.bind_anchors({"T1:新+约定"}, episode_ids=[ep])
        self.assertEqual(counts["bound"], 1)
        self.assertEqual(self.ltm.links_for(ep)[0]["status"], "bound")

    def test_unlink_and_prune_links(self):
        ep = self.ltm.add_episode({"anchors": ["T1:旧+约定"], "summary": "旧约定",
                                   "diary": "我"}, turn=3)
        self.ltm.bind_anchors({"T1:旧+约定"}, episode_ids=[ep])
        self.assertEqual(self.ltm.unlink_anchor("T1:旧+约定"), 1)
        self.assertEqual(self.ltm.links_for(ep), [])

        ep2 = self.ltm.add_episode({"anchors": ["T1:旧+照片"], "summary": "删除",
                                    "diary": "我"}, turn=4)
        self.ltm.bind_anchors({"T1:旧+照片"}, episode_ids=[ep2])
        self.assertEqual(self.ltm.prune_links(set()), 1)
        self.assertEqual(self.ltm.links_for(ep2), [])

    def test_user_state_logged_not_injected(self):
        self.assertTrue(self.ltm.add_user_state(
            {"v": 2, "a": 4, "agency": 3, "self_focus": 5}, turn=1))
        self.assertEqual(self.ltm.current_user_state()["self_focus"], 5)
        self.assertNotIn("【用户状态】", self.ltm.render_block())

    def test_delete_modes(self):
        ev = self.ltm.add_event({"type": "fact", "title": "可删事件", "text": "x"}, turn=1)
        ep = self.ltm.add_episode({"summary": "可删卡", "diary": "y"}, turn=1)
        self.assertEqual(self.ltm.delete_memory(ev, "soft")["mode"], "soft")
        self.assertNotIn(ev, {r["id"] for r in self.ltm.list_folder("facts")})
        self.assertEqual(self.ltm.delete_memory(ev, "hard")["mode"], "hard")
        self.assertIsNone(self.ltm.read_entry(ev))
        self.assertEqual(self.ltm.delete_memory(ep, "quarantine")["mode"], "quarantine")
        self.assertEqual(self.ltm.recent_episodes(), [])

    def test_no_anchor_card_is_pending(self):
        ep = self.ltm.add_episode({"summary": "缺锚", "diary": "我"}, turn=1)
        counts = self.ltm.bind_anchors(set())
        self.assertEqual(counts["missing"], 1)
        pending = self.ltm.pending_links()
        self.assertEqual(
            [(x["episode_id"], x["anchor"], x["status"], x["attempts"])
             for x in pending],
            [(ep, "", "missing", 0)])

    def test_anchor_fallback_stages(self):
        ep = self.ltm.add_episode({"summary": "缺锚", "diary": "我"}, turn=1)
        self.ltm.bind_anchors(set(), [ep])

        actions = self.ltm.anchor_actions()
        self.assertEqual(len(actions["fresh"]), 1)
        self.assertEqual(actions["notice"], actions["fresh"])
        self.assertEqual(actions["repair"], [])

        self.ltm.record_fault(self.ltm.pending_links()[0], turn=1,
                              payload="第一次的原始回复")
        actions = self.ltm.anchor_actions()
        self.assertEqual(len(actions["notice"]), 1)
        # 下一轮结束仍缺时才进入 repair
        self.assertEqual(len(actions["repair"]), 1)

        self.ltm.bump_fault(ep, turn=2, state="failed", payload="第二轮")
        actions = self.ltm.anchor_actions()
        self.assertEqual(actions["notice"], [])
        self.assertEqual(len(actions["failed"]), 1)
        rows = self.ltm.faults("failed")
        self.assertEqual(rows[0]["attempts"], 2)
        self.assertEqual(rows[0]["payload"], "第一次的原始回复")

    def test_set_anchors_clears_sentinel_and_resolves(self):
        ep = self.ltm.add_episode({"summary": "缺锚", "diary": "我"}, turn=1)
        self.ltm.bind_anchors(set(), [ep])
        self.ltm.record_fault(self.ltm.pending_links()[0], turn=1, payload="原文")

        self.assertTrue(self.ltm.set_anchors(ep, ["T1:小明+头发"]))
        self.assertEqual(self.ltm.links_for(ep), [])
        counts = self.ltm.bind_anchors({"T1:小明+头发"}, [ep])
        self.assertEqual(counts["bound"], 1)
        self.assertEqual(self.ltm.pending_links(), [])
        self.assertEqual(self.ltm.faults()[0]["state"], "resolved")

    def test_add_anchors_removes_sentinel(self):
        ep = self.ltm.add_episode({"summary": "缺锚", "diary": "我"}, turn=1)
        self.ltm.bind_anchors(set(), [ep])
        self.assertEqual(len(self.ltm.links_for(ep)), 1)
        self.ltm.add_anchors([ep], ["T1:新+约定"])
        self.assertEqual(self.ltm.links_for(ep), [])
        counts = self.ltm.bind_anchors({"T1:新+约定"}, [ep])
        self.assertEqual(counts["bound"], 1)

    def test_full_text_off_by_default(self):
        for i in range(3):
            self.ltm.add_episode({"summary": f"卡{i}", "diary": "我"}, turn=i + 1)
        block = self.ltm.render_block()
        self.assertIn("【记忆索引】", block)
        self.assertNotIn(" 日记：", block)

    def test_quotas_3_5_2(self):
        self.ltm.allow_merge = False
        semantic = []
        for i in range(5):
            semantic.append(self.ltm.add_episode({
                "summary": f"语义{i}", "diary": "我",
                "anchors": ["T1:青岛+灯塔"],
                "v": 5, "a": 5, "agency": 5, "self_focus": 5}, turn=i + 1))
        randoms = []
        for i in range(2):
            randoms.append(self.ltm.add_episode({
                "summary": f"随机{i}", "diary": "我",
                "v": 1, "a": 1, "agency": 1, "self_focus": 1}, turn=6 + i))
        delta = []
        for i in range(3):
            delta.append(self.ltm.add_episode({
                "summary": f"Δ{i}", "diary": "我",
                "v": 3, "a": 3, "agency": 3, "self_focus": 3}, turn=8 + i))
        self.ltm.render_block(limit=10, full_count=0, user_msg="青岛 灯塔",
                              rng=random.Random(0))
        got = self.ltm.last_ids
        self.assertEqual(len(got), 10)
        self.assertEqual(set(got[:3]), set(delta))
        self.assertEqual(set(got[3:8]), set(semantic))
        self.assertEqual(set(got[8:10]), set(randoms))
        for eid in got:
            self.assertIn(eid, self.ltm.last_scores)
        self.assertGreater(self.ltm.last_scores[got[0]]["delta"], 0)

    def test_role_alias_in_semantic_score(self):
        eid = self.ltm.add_episode({
            "summary": "无关", "anchors": ["T1:傅雷+傅聪"], "diary": "我",
            "v": 3, "a": 3, "agency": 3, "self_focus": 3}, turn=1)
        row = self.ltm.read_entry(eid)
        self.assertGreater(_semantic_score(row, "你答应我的事"), 0)

    def test_semantic_quota_does_not_pad_without_hits(self):
        self.ltm.allow_merge = False
        for i in range(10):
            self.ltm.add_episode({
                "summary": f"甲乙{i}", "diary": "我",
                "v": 3, "a": 3, "agency": 3, "self_focus": 3}, turn=i + 1)
        self.ltm.render_block(limit=10, full_count=0,
                              user_msg="丙丁", rng=random.Random(0))
        self.assertEqual(len(self.ltm.last_groups["semantic"]), 0)

    def test_candidate_pool_is_not_capped_at_50(self):
        self.ltm.allow_merge = False
        for i in range(60):
            self.ltm.add_episode({"summary": f"卡{i}", "diary": "我"},
                                 turn=i + 1)
        self.assertEqual(len(self.ltm._candidates()), 60)

    def test_fault_saves_available_tags(self):
        ep = self.ltm.add_episode({"summary": "缺锚", "diary": "我"}, turn=1)
        self.ltm.bind_anchors(set(), [ep])
        link = self.ltm.pending_links()[0]
        self.ltm.record_fault(link, turn=1, payload="原文",
                              available_tags=["T1:甲+乙", "T2:丙+丁"])
        self.assertEqual(json.loads(self.ltm.faults()[0]["available_tags"]),
                         ["T1:甲+乙", "T2:丙+丁"])

    def test_ingest_counts(self):
        counts = self.ltm.ingest(
            events=[{"type": "fact", "title": "事实"}],
            episodes=[{"summary": "摘要", "diary": "日记"}],
            used=[],
            turn=3,
        )
        self.assertEqual(counts, {"events": 1, "episodes": 1, "used": 0,
                                  "user_state": 0})


class ObjectiveBlockTests(unittest.TestCase):
    def test_only_semantic_hits(self):
        tmp = tempfile.TemporaryDirectory()
        mem = MemoryDB(tmp.name, "s")
        lt = LongTermMemory(tmp.name, "s")
        try:
            mem.upsert("T1:青岛+灯塔", "append", "灯塔", turn=1)
            mem.upsert("T1:北京+琴谱", "append", "琴谱", turn=2)
            rt = SimpleNamespace(memory=mem, long_term=lt, dedup_enabled=True,
                                 expanded_objectives=set())
            block = Runtime.objective_block(rt, "青岛的灯塔")
            self.assertIn("T1:青岛+灯塔", block)
            self.assertNotIn("T1:北京+琴谱", block)
        finally:
            mem.close()
            lt.close()
            tmp.cleanup()

    def test_objective_block_caps_at_ten_hits(self):
        tmp = tempfile.TemporaryDirectory()
        mem = MemoryDB(tmp.name, "s")
        lt = LongTermMemory(tmp.name, "s")
        try:
            for i in range(12):
                mem.upsert(f"T1:青岛+点{i}", "append", "x", turn=i)
            rt = SimpleNamespace(memory=mem, long_term=lt, dedup_enabled=True,
                                 expanded_objectives=set())
            block = Runtime.objective_block(rt, "青岛")
            self.assertEqual(block.count("mem:"), 10)
        finally:
            mem.close()
            lt.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
