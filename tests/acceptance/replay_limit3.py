"""离线重放：候选 3 条，对比去重开/关。

不调 API；读 compress10 报告与 data-10/long_term.db。

用法（仓库根）：
  python modules/shi_harness/tests/acceptance/replay_limit3.py
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MODULE = ROOT / "modules" / "shi_harness"
REPORT = ROOT / "test-data" / "api-smoke" / "runs" / (
    "compress10.json" if "--real" in sys.argv else "compress10-dry.json")
DB = ROOT / "test-data" / "api-smoke" / "data-10" / "long_term.db"
OUT = ROOT / "test-data" / "api-smoke" / "runs" / "replay-limit3.json"
sys.path.insert(0, str(MODULE))

from harness.affect import Candidate, select  # noqa: E402

AXES = ("v", "a", "agency", "self_focus")


def rebuild_clarity(log, ep_turn):
    clarity = {eid: "forgotten" for eid, t in ep_turn.items() if t == 0}
    for turn in log["turns"]:
        i = turn["turn"]
        for eid, t in ep_turn.items():
            if t == i:
                clarity[eid] = "clear"
        for block in turn.get("folded", []):
            match = re.match(r"\[memory_read (ep_[^\]]+)\]", block)
            if match:
                clarity[match.group(1)] = "clear"
        for comp in log.get("compressions", []):
            if comp["turn"] != i:
                continue
            for eid, t in ep_turn.items():
                if t in comp["taken"]:
                    clarity[eid] = "fuzzy"
                elif t in comp["recent"]:
                    clarity[eid] = "clear"
    return clarity


def main() -> int:
    log = json.loads(REPORT.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id,source_turn,v,a,agency,self_focus,summary,created_at,rowid "
        "FROM episodes ORDER BY created_at DESC, rowid DESC")]
    conn.close()
    ep_turn = {r["id"]: r["source_turn"] for r in rows}
    clarity = rebuild_clarity(log, ep_turn)
    by_turn = {r["id"]: r for r in rows}

    def cards(pool_ids):
        return [Candidate(id=eid, text="", v=by_turn[eid]["v"],
                          a=by_turn[eid]["a"],
                          agency=by_turn[eid]["agency"],
                          self_focus=by_turn[eid]["self_focus"],
                          scene="episode") for eid in pool_ids]

    out = {"turns": [], "summary": {}}
    off_clear = 0
    on_short = 0
    for turn in log["turns"]:
        i = turn["turn"]
        pool = [r["id"] for r in rows if (r["source_turn"] or 0) <= i][:50]
        if not pool:
            out["turns"].append({"turn": i, "on": [], "off": []})
            continue
        state = {axis: int(by_turn[pool[0]][axis]) for axis in AXES}
        all_cards = cards(pool)
        on = select(state, [c for c in all_cards
                            if clarity.get(c.id, "forgotten") != "clear"],
                    limit=3, scene="episode")
        off = select(state, all_cards, limit=3, scene="episode")
        on_ids = [x.candidate.id for x in on]
        off_ids = [x.candidate.id for x in off]
        off_clear += sum(1 for eid in off_ids
                         if clarity.get(eid, "forgotten") == "clear")
        if len(on_ids) < 3:
            on_short += 3 - len(on_ids)
        out["turns"].append({
            "turn": i,
            "on": [{"id": eid, "clarity": clarity.get(eid, "forgotten")}
                   for eid in on_ids],
            "off": [{"id": eid, "clarity": clarity.get(eid, "forgotten")}
                    for eid in off_ids],
        })
    out["summary"] = {
        "turns": len(out["turns"]),
        "dedup_off_clear_repeats": off_clear,
        "dedup_on_missing_slots": on_short,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("WROTE", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
