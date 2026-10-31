"""离线重放 compress10 报告：判断 USED 里“没读也没 clear”的条目。

用法（仓库根）：
  python modules/shi_harness/tests/acceptance/replay_clarity.py
"""
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REPORT = ROOT / "test-data" / "api-smoke" / "runs" / "compress10.json"
DB = ROOT / "test-data" / "api-smoke" / "data-10" / "long_term.db"


def main() -> int:
    log = json.loads(REPORT.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB)
    ep_turn = {r[0]: r[1] for r in
               conn.execute("SELECT id, source_turn FROM episodes")}
    conn.close()

    clarity = {eid: "forgotten" for eid, t in ep_turn.items() if t == 0}
    out = {"used_unread_allowed": 0, "used_unread_bad": 0,
           "repaired_turns": [], "details": []}
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
        reads = [str(args.get("target") or "")
                 for name, args in turn["tools"] if name == "memory_read"]
        if turn.get("used_repaired"):
            out["repaired_turns"].append(i)
        for item in turn["used"]:
            if item in reads:
                continue
            state = clarity.get(item, "forgotten")
            allowed = state == "clear"
            out["used_unread_allowed" if allowed else "used_unread_bad"] += 1
            out["details"].append({"turn": i, "id": item,
                                   "state": state, "allowed": allowed})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
