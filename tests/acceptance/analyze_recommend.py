"""简单检查推荐系统：读 api_compress_10 的报告。

用法：
  python modules/shi_harness/tests/acceptance/analyze_recommend.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REPORT = ROOT / "test-data" / "api-smoke" / "runs" / "compress10.json"


def relevant(meta: dict, letter: str) -> bool:
    raw = str(meta.get("anchors") or "") + " " + str(meta.get("summary") or "")
    tokens = []
    for part in re.findall(r"T[1-7]:([^\"\]]+)", raw):
        tokens += [x for x in re.split(r"[+|]", part) if len(x) >= 2]
    tokens += [x for x in re.split(r"[，。；、\s]+", str(meta.get("summary") or ""))
               if len(x) >= 2]
    return any(t in letter for t in tokens)


def main() -> int:
    log = json.loads(REPORT.read_text(encoding="utf-8"))
    total = {"candidates": 0, "candidate_read": 0, "ep_read": 0, "mem_read": 0,
             "read_unused": 0, "used_unread_allowed": 0, "used_unread_bad": 0}
    breakdown = {"forgotten_irrelevant": 0, "forgotten_relevant": 0,
                 "fuzzy_irrelevant": 0, "fuzzy_relevant": 0, "other": 0}
    unread_all = []
    rows = []
    for turn in log["turns"]:
        cands = set(turn["candidates"])
        reads = [str(args.get("target") or "")
                 for name, args in turn["tools"] if name == "memory_read"]
        reads = [r for r in reads if r]
        used = set(turn["used"])
        clarity = {k: v.get("clarity", "forgotten")
                   for k, v in turn.get("candidate_meta", {}).items()}
        clarity_all = turn.get("clarity_all") or clarity
        ep = sum(1 for r in reads if r.startswith("ep_"))
        mem = len(reads) - ep
        total["candidates"] += len(cands)
        total["candidate_read"] += len(cands & set(reads))
        total["ep_read"] += ep
        total["mem_read"] += mem
        total["read_unused"] += sum(1 for r in reads if r not in used)
        for item in used:
            if item in reads:
                continue
            if clarity_all.get(item) == "clear":
                total["used_unread_allowed"] += 1
            else:
                total["used_unread_bad"] += 1
        unread = []
        for eid in sorted(cands - set(reads)):
            meta = turn.get("candidate_meta", {}).get(eid, {})
            state = meta.get("clarity", "forgotten")
            rel = relevant(meta, turn.get("letter", ""))
            key = f"{state}_{'relevant' if rel else 'irrelevant'}"
            if key in breakdown:
                breakdown[key] += 1
            else:
                breakdown["other"] += 1
            unread.append({"id": eid, "clarity": state,
                           "relevant": rel, "summary": meta.get("summary", "")})
        unread_all.extend(unread)
        rows.append({
            "turn": turn["turn"],
            "candidates": sorted(cands),
            "read": reads,
            "used": sorted(used),
            "ep_read": ep,
            "mem_read": mem,
            "unread": unread,
        })
    hit = (total["candidate_read"] / total["candidates"]
           if total["candidates"] else 0)
    summary = {
        "turns": len(rows),
        "affect_candidates": total["candidates"],
        "affect_candidates_read": total["candidate_read"],
        "candidate_hit_rate": round(hit, 3),
        "episode_reads": total["ep_read"],
        "objective_reads": total["mem_read"],
        "read_but_unused": total["read_unused"],
        "used_unread_clear_allowed": total["used_unread_allowed"],
        "used_unread_non_clear": total["used_unread_bad"],
        "unread_breakdown": breakdown,
        "compressions": len(log.get("compressions", [])),
        "errors": len(log.get("errors", [])),
    }
    print(json.dumps({"summary": summary, "turns": rows},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
