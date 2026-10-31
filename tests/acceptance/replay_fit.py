"""离线评估推荐器：候选前 3 与当前信件语义的契合度。

不调 API；读真实报告 compress10.json。
用法（仓库根）：
  python modules/shi_harness/tests/acceptance/replay_fit.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
name = "compress10-dry.json" if "--dry" in sys.argv else "compress10.json"
if "--report" in sys.argv:
    name = sys.argv[sys.argv.index("--report") + 1]
REPORT = ROOT / "test-data" / "api-smoke" / "runs" / name
OUT = ROOT / "test-data" / "api-smoke" / "runs" / "replay-fit.json"


def relevant(meta: dict, letter: str) -> bool:
    user = re.sub(r"[我你]", lambda m: {"我": "傅雷", "你": "傅聪"}[m.group()], letter)
    tokens = []
    for part in re.findall(r"T[1-7]:([^\"\]]+)", str(meta.get("anchors") or "")):
        tokens += [x for x in re.split(r"[+|]", part) if len(x) >= 2]
    return any(t in user for t in tokens)


def main() -> int:
    log = json.loads(REPORT.read_text(encoding="utf-8"))
    out = {"turns": [], "summary": {}}
    top3_total = top3_rel = 0
    sem_total = sem_rel = rand_total = 0
    for turn in log["turns"]:
        cands = list(turn["candidates"])
        metas = turn.get("candidate_meta", {})
        letter = turn.get("letter", "")
        rel = {eid: relevant(metas.get(eid, {}), letter) for eid in cands}
        groups = turn.get("candidate_groups") or {
            "delta": cands[:3], "semantic": cands[3:8], "random": cands[8:10]}
        top3 = groups.get("delta", [])[:3]
        sem = groups.get("semantic", [])
        rand = groups.get("random", [])
        top3_total += len(top3)
        top3_rel += sum(1 for eid in top3 if rel.get(eid))
        sem_total += len(sem)
        sem_rel += sum(1 for eid in sem if rel.get(eid))
        rand_total += len(rand)
        out["turns"].append({
            "turn": turn["turn"],
            "letter": letter[:80],
            "delta": [{"id": eid, "relevant": rel.get(eid),
                       "summary": metas.get(eid, {}).get("summary", "")}
                      for eid in top3],
            "semantic": [{"id": eid, "relevant": rel.get(eid),
                          "summary": metas.get(eid, {}).get("summary", "")}
                         for eid in sem],
            "random": rand,
        })
    out["summary"] = {
        "turns": len(out["turns"]),
        "has_groups": all("candidate_groups" in t for t in log["turns"]),
        "delta_slots": top3_total,
        "delta_relevant": top3_rel,
        "semantic_slots": sem_total,
        "semantic_relevant": sem_rel,
        "semantic_fit_rate": round(sem_rel / sem_total, 3) if sem_total else 0,
        "random_slots": rand_total,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("WROTE", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
