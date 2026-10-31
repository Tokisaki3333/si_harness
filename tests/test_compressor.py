import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.compressor import CompressConfig, maybe_compress
from harness.models import Turn


class FakeClient:
    def chat(self, *args, **kwargs):
        return SimpleNamespace(message={"content": "- 摘要一\n- 摘要二"}, usage={})


class CaptureClient:
    def __init__(self):
        self.messages = None

    def chat(self, msgs, **kwargs):
        self.messages = msgs
        return SimpleNamespace(
            message={"content": "- 摘要一\n- 摘要二\n- 摘要三"}, usage={})


class CompressorTests(unittest.TestCase):
    def test_compress_request_matches_main_prefix(self):
        turns = [Turn(f"u{i}", f"a{i}", index=i) for i in range(1, 7)]
        cfg = CompressConfig(context_limit=1, recent_turns_min=3,
                             recent_tokens_max=0, summary_min_items=1,
                             summary_max_tokens=100, fixed_tokens=0,
                             min_compress_turns=3)
        client = CaptureClient()
        prefix = [{"role": "system", "content": "sys"},
                  {"role": "user", "content": "card"}]
        res = maybe_compress(turns, ["旧摘要"], cfg, client=client,
                             prefix_msgs=prefix)
        contents = [m["content"] for m in client.messages]
        self.assertEqual(contents[:3], ["sys", "card", "旧摘要"])
        self.assertEqual(contents[3:6], ["[轮1] u1", "a1", "[轮2] u2"])
        self.assertNotIn("[轮4] u4", contents)
        self.assertEqual([t.index for t in res.recent], [4, 5, 6])
        self.assertEqual(res.lines,
                         ["旧摘要", "- 摘要一", "- 摘要二", "- 摘要三"])

    def test_three_kept_zero_token_takes_the_rest(self):
        turns = [Turn(f"u{i}", f"a{i}", index=i) for i in range(1, 7)]
        cfg = CompressConfig(context_limit=1, recent_turns_min=3,
                             recent_tokens_max=0, summary_min_items=1,
                             summary_max_tokens=100, fixed_tokens=0,
                             min_compress_turns=3)
        res = maybe_compress(turns, [], cfg, client=FakeClient(),
                             prefix_msgs=[{"role": "user", "content": "p"}])
        self.assertEqual([t.index for t in res.recent], [4, 5, 6])
        self.assertEqual([t.index for t in res.taken], [1, 2, 3])
        self.assertEqual(len(res.new_lines), 2)


if __name__ == "__main__":
    unittest.main()
