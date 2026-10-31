"""真实 API 冒烟：注入初始记忆，跑 3 封家书。

用法（在 modules/shi_harness 下）：
  $env:DEEPSEEK_KEY="..."
  python tests/acceptance/api_smoke_3.py
  python tests/acceptance/api_smoke_3.py --dry-run   # 不调 API

检查：
  1. 模型是否主动调用 memory_read
  2. 是否新建有效 MEM / EPISODE
  3. 展开项是否写回 clear，初始记忆是否以 forgotten 出现在候选
"""
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

MODULE = Path(__file__).resolve().parents[2]
ROOT = MODULE.parents[1]
OUT = ROOT / "test-data" / "api-smoke"
DATA = OUT / "data-3"
sys.path.insert(0, str(MODULE))

from harness.bootstrap import bootstrap  # noqa: E402
from harness.cli import TOOLS, fold_tool_blocks, make_executor  # noqa: E402
from harness.models import Turn  # noqa: E402
from harness.parser import parse  # noqa: E402
from harness.tools import run_tools  # noqa: E402
from harness.validate import repair_memory_protocol  # noqa: E402

INIT_MEM = [
    ("T1:青岛+灯塔", "青岛港外海有一座废弃灯塔，塔身被潮水泡黑"),
    ("T4:傅聪+寄+琴谱", "傅聪誊清一份琴谱英文目录，随家书寄回"),
    ("T5:一九五四年+北京+家书", "一九五四年傅雷在北京写家书"),
]
INIT_EP = [
    {"summary": "想到灯塔，我心里发沉",
     "diary": "我当时想起那座被潮水泡黑的灯塔，觉得有些事回不去。",
     "anchors": ["T1:青岛+灯塔"]},
    {"summary": "目录折好，没敢先给他看",
     "diary": "我当时想把目录压到最底下，等他问起再拿出来。",
     "anchors": ["T4:傅聪+寄+琴谱"]},
]


class FakeClient:
    def chat(self, *args, **kwargs):
        return SimpleNamespace(message={"content": ""}, usage={})


def make_config() -> Path:
    base = json.loads((MODULE / "config.fulei.json").read_text(encoding="utf-8"))
    base["data_root"] = str(DATA)
    base["session_id"] = "api-3"
    path = OUT / "config-3.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def seed(rt) -> None:
    for trigger, body in INIT_MEM:
        rt.memory.upsert(trigger, "append", body, turn=0)
        rt.long_term.index_upsert(trigger, "objective", 0, "forgotten")
    for card in INIT_EP:
        eid = rt.long_term.add_episode(
            dict(card, v=3, a=3, agency=3, self_focus=3), turn=0)
        rt.long_term.index_upsert(eid, "episode", 0, "forgotten")
    rt.bind_affect_anchors()


def fake_reply(turn: int) -> str:
    return (
        "【心念】想先说灯塔。\n【正文】灯塔的事我记着。\n"
        f'[[EVENT {{"type":"fact","title":"第{turn}封","text":"读到第{turn}封家书"}}]]\n'
        f'[[EPISODE {{"thread_id":"信件{turn}","anchors":["T1:青岛+灯塔"],'
        f'"v":3,"a":3,"agency":3,"self_focus":3,"summary":"第{turn}封",'
        f'"diary":"我读着信。"}}]]\n'
        "[[MEM trigger=T1:青岛+灯塔 op=append body=灯塔仍然废弃]]\n"
        "[[USED T1:青岛+灯塔]]"
    )


def main() -> int:
    dry = "--dry-run" in sys.argv
    if dry:
        os.environ.setdefault("DEEPSEEK_KEY", "dry-run")
    os.chdir(MODULE)
    shutil.rmtree(DATA, ignore_errors=True)
    cfg_path = make_config()
    rt = bootstrap(str(cfg_path))
    rt.long_term.allow_merge = False
    seed(rt)

    letters = [json.loads(x) for x in
               (ROOT / "test-data" / "傅雷家书" / "letters.jsonl")
               .read_text(encoding="utf-8").splitlines() if x.strip()][:3]
    tools = [t for t in TOOLS if t["function"]["name"] == "memory_read"]
    window, log = [], {"dry_run": dry, "turns": [], "assert": {}}
    try:
        for i, item in enumerate(letters, 1):
            msgs = rt.build_messages(item["text"], window)
            candidates = list(rt.long_term.last_ids)
            if dry:
                text = fake_reply(i)
                calls = [{"name": "memory_read",
                          "args": {"target": "T1:青岛+灯塔"},
                          "output": "T1:青岛+灯塔：青岛港外海有一座废弃灯塔"}]
            else:
                run = run_tools(rt.client, msgs, tools, make_executor(rt),
                                max_rounds=3, max_tokens=2000)
                text = run.final.get("content") or ""
                calls = run.calls
            p = parse(text)
            p, repaired = repair_memory_protocol(
                FakeClient() if dry else rt.client, msgs, text, p)
            counts = rt.long_term.ingest(
                events=p.events, episodes=p.episodes, used=p.used,
                user_state=p.user_state, turn=i)
            for op in p.mem_ops:
                rt.memory.upsert(op["trigger"], op["op"], op["body"], turn=i)
                rt.long_term.index_upsert(op["trigger"], "objective", i)
            for eid in rt.long_term.last_episode_ids:
                rt.long_term.index_upsert(eid, "episode", i)
            rt.sync_memory()
            rt.bind_affect_anchors()
            folded, folded_targets = fold_tool_blocks(calls, p.used, rt.memory)
            if folded_targets:
                rt.long_term.index_clear(folded_targets, i)
            rt.record_turn(item["text"], text, i, tools=folded)
            window.append(Turn(item["text"], text, index=i, tools=folded))
            log["turns"].append({
                "turn": i,
                "candidates": candidates,
                "tools": [(c["name"], c["args"]) for c in calls],
                "used": p.used,
                "new_mem": counts["events"],
                "new_episodes": counts["episodes"],
                "folded": folded,
                "clarity": rt.long_term.index_clarity(),
                "reply": text,
            })
        clarity = rt.long_term.index_clarity()
        log["assert"]["memory_read_called"] = any(
            name == "memory_read"
            for t in log["turns"] for name, _ in t["tools"])
        log["assert"]["new_mem"] = sum(t["new_mem"] for t in log["turns"])
        log["assert"]["new_episodes"] = sum(t["new_episodes"] for t in log["turns"])
        log["assert"]["expanded_is_clear"] = all(
            clarity.get(target) == "clear"
            for t in log["turns"] for target in t["used"])
        log["pass"] = (log["assert"]["memory_read_called"]
                       and log["assert"]["new_mem"] >= 1
                       and log["assert"]["new_episodes"] >= 1
                       and log["assert"]["expanded_is_clear"])
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        report = OUT / "runs" / ("smoke3-dry.json" if dry else "smoke3.json")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(log, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        rt.close()
    print("WROTE", report, "PASS" if log.get("pass") else "FAIL")
    return 0 if log.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
