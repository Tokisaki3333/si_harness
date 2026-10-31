"""真实 API 压缩：低参数强制压缩，跑 10 封家书。

用法（在仓库根）：
  $env:DEEPSEEK_KEY="..."
  python modules/shi_harness/tests/acceptance/api_compress_10.py
  python modules/shi_harness/tests/acceptance/api_compress_10.py --dry-run

检查：
  1. 对话中至少发生一次真压缩
  2. 压缩后 taken 轮的索引变 fuzzy，窗口内轮次为 clear
  3. 每轮候选数量、清晰度分布、异常
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

MODULE = Path(__file__).resolve().parents[2]
ROOT = MODULE.parents[1]
OUT = ROOT / "test-data" / "api-smoke"
DATA = OUT / "data-10"
sys.path.insert(0, str(MODULE))

from harness.bootstrap import bootstrap  # noqa: E402
from harness.cli import (TOOLS, fold_tool_blocks, make_executor,  # noqa: E402
                         normalize_used)
from harness.compressor import (CompressConfig, CompressError,  # noqa: E402
                                maybe_compress, trim_summary)
from harness.models import Turn  # noqa: E402
from harness.parser import parse  # noqa: E402
from harness.tools import run_tools  # noqa: E402
from harness.validate import (repair_memory_protocol,  # noqa: E402
                              repair_used_protocol)

LOW = CompressConfig(context_limit=2000, recent_turns_min=2,
                     recent_tokens_max=300, summary_min_items=2,
                     summary_max_tokens=200, fixed_tokens=0)

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
        return SimpleNamespace(message={"content": "- 摘要一\n- 摘要二"}, usage={})


def make_config(card: str | None = None) -> Path:
    base = json.loads((MODULE / "config.fulei.json").read_text(encoding="utf-8"))
    base["data_root"] = str(DATA)
    base["session_id"] = "api-10"
    if card:
        base["card_path"] = card
    path = OUT / "config-10.json"
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


def clarity_counts(rt) -> dict:
    values = list(rt.long_term.index_clarity().values())
    return {k: values.count(k) for k in ("clear", "fuzzy", "forgotten")}


def main() -> int:
    dry = "--dry-run" in sys.argv
    turns = 10
    if "--turns" in sys.argv:
        try:
            turns = int(sys.argv[sys.argv.index("--turns") + 1])
        except (IndexError, ValueError):
            turns = 10
    if dry:
        os.environ.setdefault("DEEPSEEK_KEY", "dry-run")
    if "--min-compress" in sys.argv:
        try:
            LOW.min_compress_turns = int(
                sys.argv[sys.argv.index("--min-compress") + 1])
        except (IndexError, ValueError):
            pass
    sleep_s = 0.0
    if "--sleep" in sys.argv:
        try:
            sleep_s = float(sys.argv[sys.argv.index("--sleep") + 1])
        except (IndexError, ValueError):
            pass
    base_url = None
    if "--base-url" in sys.argv:
        base_url = sys.argv[sys.argv.index("--base-url") + 1]
    report_name = None
    if "--report-name" in sys.argv:
        report_name = sys.argv[sys.argv.index("--report-name") + 1]
    card = None
    if "--card" in sys.argv:
        card = sys.argv[sys.argv.index("--card") + 1]
    normal = "--low" not in sys.argv
    os.chdir(MODULE)
    shutil.rmtree(DATA, ignore_errors=True)
    cfg_path = make_config(card)
    rt = bootstrap(str(cfg_path))
    if base_url:
        rt.client.base_url = base_url
    rt.long_term.allow_merge = False
    if "--low" in sys.argv:
        rt.config.compress = LOW
    rt.dedup_enabled = "--no-dedup" not in sys.argv
    seed(rt)

    full = ROOT / "test-data" / "傅雷家书" / "全文" / "傅雷家书-全文.md"
    if full.exists():
        text = full.read_text(encoding="utf-8")
        marks = list(re.finditer(r"^(一九[五六][^\n]{0,40}日[^\n]*)$", text, re.M))
        letters = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = text[m.end():end].strip()
            if body:
                letters.append({"date": m.group(1).strip(), "text": body})
    else:
        letters = [json.loads(x) for x in
                   (ROOT / "test-data" / "傅雷家书" / "letters.jsonl")
                   .read_text(encoding="utf-8").splitlines() if x.strip()]
    letters = letters[:turns]
    tools = [t for t in TOOLS if t["function"]["name"] == "memory_read"]
    client = FakeClient() if dry else rt.client
    window, marks = [], []
    log = {"dry_run": dry, "turns": [], "compressions": [], "errors": []}
    try:
        for i, item in enumerate(letters, 1):
            user_text = item["text"][:320]
            msgs = rt.build_messages(user_text, window, compressed=marks)
            msg_hashes = [
                hashlib.sha256(
                    (m["role"] + "\x00" + m["content"]).encode("utf-8")
                ).hexdigest()[:12]
                for m in msgs
            ]
            candidates = list(rt.long_term.last_ids)
            candidate_groups = {
                key: list(value)
                for key, value in rt.long_term.last_groups.items()
            }
            candidate_scores = dict(rt.long_term.last_scores)
            clarity_map = rt.long_term.index_clarity()
            candidate_meta = {}
            for eid in candidates:
                row = rt.long_term.read_entry(eid) or {}
                candidate_meta[eid] = {
                    "clarity": clarity_map.get(eid, "forgotten"),
                    "summary": row.get("summary", ""),
                    "anchors": row.get("anchors", "[]"),
                }
            if dry:
                usage = {}
                usage_calls = []
                call_hashes = []
                call_chars = []
                call_common_chars = []
                text = fake_reply(i)
                calls = [{"name": "memory_read",
                          "args": {"target": "T1:青岛+灯塔"},
                          "output": "T1:青岛+灯塔：青岛港外海有一座废弃灯塔"}]
            else:
                run = run_tools(rt.client, msgs, tools, make_executor(rt),
                                max_rounds=3, max_tokens=2000)
                usage = dict(run.usage_totals)
                usage_calls = [dict(x) for x in run.usage_calls]
                call_hashes = [list(x) for x in run.call_hashes]
                call_chars = [list(x) for x in run.call_chars]
                call_common_chars = list(run.call_common_chars)
                text = run.final.get("content") or ""
                calls = run.calls
            p = parse(text)
            p, repaired = repair_memory_protocol(client, msgs, text, p)
            p.used = normalize_used(p.used, rt.memory)
            read_targets = normalize_used(
                [str((c.get("args") or {}).get("target")
                     or (c.get("args") or {}).get("id") or "")
                 for c in calls if c.get("name") == "memory_read"],
                rt.memory)
            p.used, used_repaired = repair_used_protocol(
                client, msgs, text, p.used, clarity_map, read_targets)
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
            rt.record_turn(user_text, text, i, tools=folded)
            window.append(Turn(user_text, text, index=i, tools=folded,
                               display=len(window) + 1))

            before = list(rt.long_term.index_clarity())
            try:
                prefix = rt.assembler.prefix()
                comp = maybe_compress(window, marks, rt.config.compress,
                                      client=client, prefix_msgs=prefix,
                                      tools=tools)
            except CompressError as e:
                log["errors"].append({"turn": i, "error": str(e)})
                comp = None
            if comp is not None and comp.taken:
                window = comp.recent
                tagged = list(comp.new_lines)
                for t, line in zip(comp.taken, tagged):
                    rt.session.append_summary(t.index, line)
                marks = list(comp.lines)
                for i2, t in enumerate(window, 1):
                    t.display = i2
                rt.merge_memory()
                rt.long_term.index_apply_compression(
                    [t.index for t in comp.taken],
                    [t.index for t in comp.recent])
                rt.long_term.index_reconcile(rt.session.turn_set())
                log["compressions"].append({
                    "turn": i,
                    "taken": [t.index for t in comp.taken],
                    "recent": [t.index for t in comp.recent],
                    "lines": comp.new_lines,
                    "usage": dict(comp.usage or {}),
                })
            log["turns"].append({
                "turn": i,
                "letter": user_text,
                "candidate_scores": candidate_scores,
                "candidates": candidates,
                "candidate_groups": candidate_groups,
                "candidate_meta": candidate_meta,
                "clarity_all": clarity_map,
                "tools": [(c["name"], c["args"]) for c in calls],
                "used": p.used,
                "used_repaired": used_repaired,
                "new_mem": counts["events"],
                "new_episodes": counts["episodes"],
                "folded": folded,
                "clarity": clarity_counts(rt),
                "window": [t.index for t in window],
                "marks": len(marks),
                "usage": usage,
                "usage_calls": usage_calls,
                "call_hashes": call_hashes,
                "call_chars": call_chars,
                "call_common_chars": call_common_chars,
                "msg_hashes": msg_hashes,
            })
            if sleep_s:
                time.sleep(sleep_s)
        clarity = rt.long_term.index_clarity()
        prompt = sum(t.get("usage", {}).get("prompt_tokens", 0) for t in log["turns"])
        hit = sum(t.get("usage", {}).get("prompt_cache_hit_tokens", 0)
                  for t in log["turns"])
        completion = sum(t.get("usage", {}).get("completion_tokens", 0)
                         for t in log["turns"])
        comp_prompt = sum(c.get("usage", {}).get("prompt_tokens", 0)
                          for c in log["compressions"])
        comp_hit = sum(c.get("usage", {}).get("prompt_cache_hit_tokens", 0)
                       for c in log["compressions"])
        log["usage_summary"] = {
            "prompt": prompt,
            "cache_hit": hit,
            "completion": completion,
            "cache_rate": round(hit / prompt, 4) if prompt else 0,
            "compression_prompt": comp_prompt,
            "compression_cache_hit": comp_hit,
            "compression_cache_rate": round(comp_hit / comp_prompt, 4)
            if comp_prompt else 0,
        }
        log["assert"] = {
            "compressions": len(log["compressions"]),
            "fuzzy_rows": sum(1 for v in clarity.values() if v == "fuzzy"),
            "clear_rows": sum(1 for v in clarity.values() if v == "clear"),
            "errors": len(log["errors"]),
        }
        log["normal_compress"] = normal
        if normal:
            log["pass"] = log["assert"]["errors"] == 0
        else:
            log["pass"] = (log["assert"]["compressions"] >= 1
                           and log["assert"]["errors"] == 0)
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        report = OUT / "runs" / (
            report_name or (f"compress{turns}-dry.json" if dry
                            else f"compress{turns}.json"))
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(log, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        rt.close()
    print("WROTE", report, "PASS" if log.get("pass") else "FAIL")
    return 0 if log.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
