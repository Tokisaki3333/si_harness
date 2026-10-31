import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.store.memory import MemoryDB


class MemoryDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MemoryDB(self.tmp.name, "test")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_body_with_semicolon_stays_one_entry(self):
        self.db.upsert("T1:青岛+港", "append", "灯塔已废弃；塔身被潮水泡黑", turn=1)
        self.db.upsert("T1:青岛+港", "append", "无灯可用", turn=2)
        row = self.db.get("T1:青岛+港")
        self.assertEqual(json.loads(row["entries"]),
                         ["灯塔已废弃；塔身被潮水泡黑", "无灯可用"])
        self.assertIn("灯塔已废弃；塔身被潮水泡黑；无灯可用", self.db.search("T1:青岛+港"))

    def test_replace_keeps_only_new_body(self):
        self.db.upsert("T1:青岛+港", "append", "旧说法", turn=1)
        self.db.upsert("T1:青岛+港", "replace", "新说法；补充", turn=2)
        self.assertEqual(json.loads(self.db.get("T1:青岛+港")["entries"]), ["新说法；补充"])

    def test_append_is_idempotent(self):
        self.db.upsert("T2:小明+头发", "append", "黑发", turn=1)
        self.db.upsert("T2:小明+头发", "append", "黑发", turn=2)
        self.assertEqual(json.loads(self.db.get("T2:小明+头发")["entries"]), ["黑发"])

    def test_legacy_semicolon_rows_still_readable(self):
        self.db.conn.execute(
            "INSERT INTO memory(session_id,trigger,summary,entries,importance,turn,invalid,updated_at) "
            "VALUES(?,?,?,?,?,?,0,?)",
            ("test", "T1:旧+格式", "旧甲", "旧甲；旧乙", 0.5, 1, 1))
        self.db.conn.commit()
        self.assertEqual(self.db.search("T1:旧+格式"), "T1:旧+格式：旧甲；旧乙")

    def test_search_by_id_and_index_line(self):
        row = self.db.upsert("T3:小明+就读于+清华", "append", "小明在清华读书", turn=1)
        self.assertIn("小明在清华读书", self.db.search_by_id(row["id"]))
        self.assertIn("T3:小明+就读于+清华", self.db.index_line())


if __name__ == "__main__":
    unittest.main()
