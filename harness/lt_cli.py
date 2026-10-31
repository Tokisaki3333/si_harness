"""长期记忆查看 / 修补 / 删除小工具。

用法（在 modules/shi_harness 下）：
  python -m harness.lt_cli --db data/test-e2e/long_term.db stats
  python -m harness.lt_cli --db data/test-e2e/long_term.db episodes
  python -m harness.lt_cli --db data/test-e2e/long_term.db read ep_xxx
  python -m harness.lt_cli --db data/test-e2e/long_term.db faults --state failed
  python -m harness.lt_cli --db data/test-e2e/long_term.db set-anchors ep_xxx "T1:小明+头发"
  python -m harness.lt_cli --db data/test-e2e/long_term.db delete ep_xxx --soft
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .long_term import LongTermMemory


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="长期记忆查看 / 修补 / 删除")
    parser.add_argument("--db", default="data/long_term.db",
                        help="long_term.db 路径")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats")
    sub.add_parser("folders")
    p = sub.add_parser("entries")
    p.add_argument("folder")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("read")
    p.add_argument("id")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("episodes")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("events")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("faults")
    p.add_argument("--state", default=None,
                   help="pending / failed / resolved；默认全部")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("set-anchors")
    p.add_argument("id")
    p.add_argument("anchors", nargs="+")
    p = sub.add_parser("resolve")
    p.add_argument("id")
    p = sub.add_parser("delete")
    p.add_argument("id")
    p.add_argument("--hard", action="store_true")
    p.add_argument("--quarantine", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.db).parent
    ltm = LongTermMemory(root, "cli")
    try:
        if args.cmd == "stats":
            _print({"line": ltm.debug_line(),
                    "events": len(ltm.all_events(1000)),
                    "episodes": len(ltm.all_episodes(1000)),
                    "pending": len(ltm.pending_links()),
                    "failed": len(ltm.faults("failed"))})
        elif args.cmd == "folders":
            _print(ltm.list_folders())
        elif args.cmd == "entries":
            _print(ltm.list_folder(args.folder, args.limit))
        elif args.cmd == "read":
            _print(ltm.read_entry(args.id))
        elif args.cmd == "search":
            _print(ltm.search(args.query, args.limit))
        elif args.cmd == "episodes":
            _print(ltm.all_episodes(args.limit))
        elif args.cmd == "events":
            _print(ltm.all_events(args.limit))
        elif args.cmd == "faults":
            _print(ltm.faults(args.state, args.limit))
        elif args.cmd == "set-anchors":
            ok = ltm.set_anchors(args.id, args.anchors)
            resolved = ltm.resolve_faults([args.id])
            _print({"id": args.id, "updated": ok,
                    "anchors": args.anchors, "resolved_faults": resolved})
        elif args.cmd == "resolve":
            _print({"id": args.id, "resolved_faults": ltm.resolve_faults([args.id])})
        elif args.cmd == "delete":
            mode = "hard" if args.hard else "quarantine" if args.quarantine else "soft"
            _print(ltm.delete_memory(args.id, mode))
    finally:
        ltm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
