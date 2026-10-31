"""交互循环：普通输入走模型，/ 开头只执行指令。

本轮为调试阶段：原样打印模型输出（含协议行、思考），并每轮打印压缩状态。
"""
from __future__ import annotations

import atexit
import json
import os
import sys

from .bootstrap import bootstrap
from .config import ConfigError
from .compressor import CompressError, maybe_compress, tl, trim_summary
from .models import Turn
from .pricing import current_rates
from .term import C, enable_ansi
from .tools import run_tools
from .validate import (ensure_protocol, mem_present, repair_anchor_protocol,
                       repair_mem_protocol, repair_memory_protocol,
                       repair_used_protocol)
from .guard import guard_input
from .long_term import anchor_is_valid
from .parser import parse

MODES = {"0": "DAILY", "1": "INTIMATE", "2": "EROTIC", "3": "EXITING"}
SCENE = "【场景】帝江号·舷窗前；已相处约两小时"

TOOLS = [
    {"type": "function", "function": {
        "name": "lookup", "description": "查询世界观、剧情、人物设定资料",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "memory_read", "description": "读取情感卡或客观记忆正文；target=ep_… / mem:id / trigger",
        "parameters": {"type": "object", "properties": {"target": {"type": "string"}},
                       "required": ["target"]}}},
]


def make_executor(rt):
    def execute(name, args):
        if name == "lookup":
            return rt.docs.lookup(args.get("query", "")) or "未找到相关资料。"
        if name == "memory_read":
            target = str(args.get("target") or args.get("id") or "").strip()
            if not target:
                return "未找到"
            if target.startswith("ep_"):
                rec = rt.long_term.read_entry(target)
                rt.expanded_episodes.add(target)
                return json.dumps(rec, ensure_ascii=False) if rec else "未找到"
            if target.startswith("mem:"):
                mem_id = target.split(":", 1)[1].strip()
                rec = rt.memory.get_by_id(mem_id)
                if rec:
                    rt.expanded_objectives.add(rec["trigger"])
                return rt.memory.search_by_id(mem_id)
            if target.isdigit():
                rec = rt.memory.get_by_id(target)
                if rec:
                    rt.expanded_objectives.add(rec["trigger"])
                return rt.memory.search_by_id(target)
            out = rt.memory.search(target)
            if not out.startswith("未找到"):
                rt.expanded_objectives.add(target)
            return out
        return "未知工具"
    return execute


HELP = """指令（不进入对话、不调用模型）：
  /mod 0|1|2|3   切换状态：0 日常 / 1 亲密 / 2 情色 / 3 退出中
  /unlock 0|1    切换 L1 解锁状态（0 收紧 / 1 放开）
  /state         查看当前状态机
  /mem           查看记忆索引
  /lt            查看长期记忆（事件/情感卡/USED/候选）
  /reload [n]    从数据库恢复最近 n 轮（默认 20）
  /compress      手动触发一次压缩
  /help          显示本帮助
  /quit          退出
其他输入正常对话。"""


def handle_command(line: str, rt, window: list, marks: dict) -> bool:
    parts = line.split()
    cmd = parts[0].lower()

    if cmd == "/mod":
        if len(parts) < 2 or parts[1] not in MODES:
            print("用法：/mod 0|1|2|3")
        else:
            rt.state.set_mode(MODES[parts[1]])
            print(f"[状态] {rt.state.to_line()}")
    elif cmd == "/unlock":
        if len(parts) < 2 or parts[1] not in ("0", "1"):
            print("用法：/unlock 0|1")
        else:
            rt.set_unlocked(parts[1] == "1")
            print(f"[解锁] {'开' if rt.unlocked else '关'}")
    elif cmd == "/state":
        print(rt.state.to_line())
    elif cmd == "/reload":
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
        window[:] = rt.session.reload_window(n)
        rt.known_affect_ids.clear()
        marks["summaries"][:] = [r["summary"] for r in rt.session.summaries()]
        snap = rt.session.latest_snapshot()
        if snap:
            rt.state.debug(**snap["state"])
        rt.scene.minutes = 120 + 5 * rt.session.messages.max_turn()
        print(f"[恢复] {len(window)} 轮 | 摘要 {len(marks['summaries'])} 条")
    elif cmd == "/compress":
        prefix = rt.assembler.prefix()
        try:
            res = maybe_compress(window, marks["summaries"], rt.config.compress,
                                 client=rt.client, prefix_msgs=prefix,
                                 tools=TOOLS)
        except CompressError as e:
            print(f"[压缩失败] {e}")
        else:
            window[:] = res.recent
            tagged = list(res.new_lines)
            for t, line in zip(res.taken, tagged):
                rt.session.append_summary(t.index, line)
            marks["summaries"][:] = list(res.lines)
            for i, t in enumerate(window, 1):
                t.display = i
            if tagged:
                rt.merge_memory()
                rt.long_term.index_apply_compression(
                    [t.index for t in res.taken],
                    [t.index for t in res.recent])
                rt.long_term.index_reconcile(rt.session.turn_set())
            print(f"[压缩] 剩余 {len(window)} 轮 | 摘要 {len(marks['summaries'])} 条")
    elif cmd == "/mem":
        print(rt.memory.index_line() or "（空）")
    elif cmd == "/lt":
        print(rt.long_term.debug_line())
        for row in rt.long_term.recent_episodes(5):
            print(f"{row['id']} | v={row['v']} a={row['a']} "
                  f"agency={row['agency']} self_focus={row['self_focus']} | "
                  f"{row['summary']}")
        if rt.long_term.last_ids:
            print("[候选] " + ", ".join(rt.long_term.last_ids))
    elif cmd == "/price":
        hp, mp, op, label = current_rates(rt.config.pricing)
        print(C.paint(f"[计价] {label} | 命中 {hp} / 未命中 {mp} / 输出 {op} 元每百万 token", C.DEBUG))
    elif cmd == "/balance":
        b = rt.client.balance()
        infos = b.get("balance_infos") or []
        if infos:
            i = infos[0]
            print(C.paint(f"[余额] {i.get('total_balance')} {i.get('currency')}", C.DEBUG))
        else:
            print(C.paint("[余额] 查询失败", C.ERROR))
    elif cmd == "/rewind":
        print("rewind 暂未启用")
    elif cmd in ("/quit", "/exit"):
        return False
    elif cmd == "/help":
        print(HELP)
    else:
        print("未知指令，/help 查看")
    return True


def normalize_used(values, memory) -> list[str]:
    """USED 里的 mem:id / 数字 id 归一化为 trigger。"""
    out: list[str] = []
    for item in values or ():
        item = str(item).strip()
        if not item:
            continue
        if item.startswith("mem:"):
            rec = memory.get_by_id(item.split(":", 1)[1])
            out.append(rec["trigger"] if rec else item)
        elif item.isdigit():
            rec = memory.get_by_id(item)
            out.append(rec["trigger"] if rec else item)
        else:
            out.append(item)
    return out


def fold_tool_blocks(calls, used, memory):
    """只折入 USED 采用的 memory_read 结果，带 target 标记。

    返回 (blocks, cleared)，cleared 已把 mem:id 归一化为 trigger。
    不设单轮折叠上限：长度交给压缩器双阈值与模型输出上限。
    """
    targets: set[str] = set()
    for item in used or ():
        item = str(item).strip()
        if not item:
            continue
        if item.startswith("mem:"):
            rec = memory.get_by_id(item.split(":", 1)[1])
            targets.add(rec["trigger"] if rec else item)
        elif item.isdigit():
            rec = memory.get_by_id(item)
            targets.add(rec["trigger"] if rec else item)
        else:
            targets.add(item)
    blocks: list[str] = []
    cleared: list[str] = []
    seen: set[str] = set()
    for call in calls or ():
        if call.get("name") != "memory_read":
            continue
        args = call.get("args") or {}
        target = str(args.get("target") or args.get("id") or "").strip()
        if not target:
            continue
        norm = target
        if target.startswith("mem:"):
            rec = memory.get_by_id(target.split(":", 1)[1])
            norm = rec["trigger"] if rec else target
        elif target.isdigit():
            rec = memory.get_by_id(target)
            norm = rec["trigger"] if rec else target
        if norm not in targets and target not in targets:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        blocks.append(f"[memory_read {target}] {call.get('output') or ''}".strip())
        cleared.append(norm)
    return blocks, cleared


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    cfg_path = os.environ.get("HARNESS_CONFIG", "config.json")
    if not os.path.exists(cfg_path):
        print(f"找不到配置：{cfg_path}")
        sys.exit(1)

    enable_ansi()
    sys.stdout.write(C.BG)
    atexit.register(lambda: sys.stdout.write(C.RESET))
    try:
        rt = bootstrap(cfg_path)
    except ConfigError as e:
        print(C.paint(f"[配置错误] {e}", C.ERROR))
        print('设置环境变量 DEEPSEEK_KEY，或在 key.json 写入 {"api_key": "sk-..."}')
        return
    cfg = rt.config.compress
    window: list[Turn] = []
    marks = {"summaries": []}
    compressed_turns = 0
    turn = rt.session.messages.max_turn()
    missing_streak = 0
    mem_missing_streak = 0
    window[:] = rt.session.reload_window(20)
    marks["summaries"][:] = [r["summary"] for r in rt.session.summaries()]
    snap = rt.session.latest_snapshot()
    if snap:
        rt.state.debug(**snap["state"])
    if turn:
        rt.scene.minutes = 120 + 5 * turn
    total_cost = 0.0
    b = rt.client.balance()
    infos = b.get("balance_infos") or []
    money = float(infos[0].get("total_balance") or 0) if infos else None

    print(C.paint(HELP, C.LABEL))
    print(C.paint(f"[会话] {rt.config.session_id} | 模型 {rt.config.model}", C.DEBUG))
    while True:
        try:
            line = input(C.USER + "你> " + C.RESET).strip()
            line, hits = guard_input(line)
            if hits:
                print(C.paint(f"[告警] 输入含状态机关键词，已屏蔽 {len(hits)} 处", C.ERROR))
        except EOFError:
            break
        if not line:
            continue
        if line.startswith("/"):
            if not handle_command(line, rt, window, marks):
                break
            continue

        turn += 1
        rt.scene.advance(line)
        notice = ""
        notices = []
        if missing_streak > 0:
            notices.append("【协议提醒】上一轮回复缺少状态行。本轮补上时，"
                           "增量按两轮合并计算，数值平滑过渡，不要一次跳太多。")
        if mem_missing_streak > 0:
            notices.append("【记忆补行】上一轮没有输出有效 MEM。本轮协议段补写两行："
                           "上一轮缺失的 MEM、本轮 MEM；确实没有就写 [[MEM none]]。")
        anchor_pending = rt.long_term.anchor_actions()["notice"]
        if anchor_pending:
            lines = [f"- {x['episode_id']}：{x['anchor'] or '（无 anchors）'}（{x['status']}）"
                     for x in anchor_pending]
            notices.insert(0, "【索引回退】以下情感卡缺客观锚点：\n" + "\n".join(lines) +
                           "\n本轮协议段用 [[ANCHOR ep_xxxxxxxx T1:实体+属性]] 补交，"
                           "一张卡一行；补不上就写 [[ANCHOR none]]。")
        if notices:
            notice = "\n".join(notices)
        msgs = rt.build_messages(line, window, compressed=marks["summaries"],
                                 notice=notice)
        mode = rt.state.state.mode
        phase = {"kind": None}
        out = {"last": ""}

        def emit(text: str) -> None:
            for ch in text:
                if ch == "\n" and out["last"] == "\n":
                    continue
                sys.stdout.write(ch)
                out["last"] = ch

        def on_delta(kind: str, chunk: str) -> None:
            if not chunk:
                return
            if kind != phase["kind"]:
                if phase["kind"]:
                    emit("\n")
                label = "--- 思考 ---" if kind == "reasoning" else "--- 正文 ---"
                print(C.paint(label, C.LABEL))
                out["last"] = "\n"
                sys.stdout.write(C.THINK if kind == "reasoning" else C.TEXT)
                phase["kind"] = kind
            emit(chunk)
            sys.stdout.flush()

        def on_tool(name: str, args: dict) -> None:
            if phase["kind"]:
                sys.stdout.write(C.RESET + "\n")
                phase["kind"] = None
            print(C.paint("--- 工具调用 ---", C.LABEL))
            print(C.paint(f"{name} {args}", C.DEBUG))

        run = run_tools(rt.client, msgs, TOOLS, make_executor(rt), max_rounds=3,
                        max_tokens=4000 if mode == "EROTIC" else 3000,
                        stream=True, on_delta=on_delta, on_tool=on_tool)
        if phase["kind"]:
            sys.stdout.write(C.RESET)
            emit("\n")
        text = run.final.get("content") or ""
        p0 = parse(text)
        missing_streak = missing_streak + 1 if p0.state_delta is None else 0
        vr = ensure_protocol(rt.client, msgs, text,
                             allow_missing=(missing_streak < 2))
        if vr.repaired:
            missing_streak = 0
        p = vr.parsed

        p, mem_repaired = repair_memory_protocol(rt.client, msgs, text, p)
        if mem_repaired:
            print(C.paint("[记忆补行] EVENT/EPISODE JSON 已修复", C.DEBUG))

        if mem_present(p, text):
            mem_missing_streak = 0
        elif mem_missing_streak >= 1:
            p, mem_repaired = repair_mem_protocol(rt.client, msgs, text, p)
            mem_missing_streak = 0
            if mem_repaired:
                print(C.paint("[记忆补行] MEM 缺失已补行", C.DEBUG))
        else:
            mem_missing_streak = 1

        # USED 只能引用 clear 或本轮已展开的条目；否则补一轮只修 USED
        p.used = normalize_used(p.used, rt.memory)
        read_targets = normalize_used(
            [str((c.get("args") or {}).get("target")
                 or (c.get("args") or {}).get("id") or "")
             for c in run.calls if c.get("name") == "memory_read"],
            rt.memory)
        p.used, used_repaired = repair_used_protocol(
            rt.client, msgs, text, p.used,
            rt.long_term.index_clarity(), read_targets)
        if used_repaired:
            print(C.paint("[记忆补行] USED 不可用项已修正", C.DEBUG))

        lt = rt.long_term.ingest(events=p.events, episodes=p.episodes,
                                 used=p.used, user_state=p.user_state, turn=turn)
        rt.state.apply(p.state_delta)
        if p.scene_loc:
            rt.scene.location = p.scene_loc
        if p.ack and rt.state.state.mode == "EXITING":
            rt.state.set_mode("DAILY")
        for op in p.mem_ops:
            rt.memory.upsert(op["trigger"], op["op"], op["body"], turn=turn)
        rt.sync_memory()
        current_triggers = [op["trigger"] for op in p.mem_ops]
        for op in p.mem_ops:
            rt.long_term.index_upsert(op["trigger"], "objective", turn)
        for eid in rt.long_term.last_episode_ids:
            rt.long_term.index_upsert(eid, "episode", turn)
        if current_triggers:
            rt.long_term.add_anchors(rt.long_term.last_episode_ids, current_triggers)
        rt.bind_affect_anchors()

        # 第一层：把本轮 [[ANCHOR …]] 补交应用到上一轮失败的卡
        applied = []
        for eid, anchors in (p.anchor_fixes or {}).items():
            valid_anchors = [a for a in anchors if anchor_is_valid(a)]
            if valid_anchors and rt.long_term.set_anchors(eid, valid_anchors):
                applied.append(eid)
        if applied:
            rt.bind_affect_anchors()

        clarity_now = rt.long_term.index_clarity()
        clear_tags = [m["trigger"] for m in rt.memory.all()
                      if clarity_now.get(m["trigger"], "forgotten") == "clear"]
        actions = rt.long_term.anchor_actions()
        for link in actions["fresh"]:
            rt.long_term.record_fault(
                link, turn=turn, state="pending", payload=text,
                detail="首次发现 anchor 缺失/格式错误，等下一轮补交",
                available_tags=clear_tags)
        if actions["repair"]:
            # 第二层：上一轮已经要求补交仍未补上，才新开一轮只修 anchors
            retry_ids = {x["episode_id"] for x in actions["repair"]}
            bad = [{"episode_id": x["episode_id"],
                    "anchor": x["anchor"] or "（无 anchors）",
                    "status": x["status"]} for x in actions["repair"]]
            fixes = repair_anchor_protocol(
                rt.client, msgs, text, bad,
                [m["trigger"] for m in rt.memory.all()],
            )
            for eid, anchors in fixes.items():
                if anchors:
                    rt.long_term.set_anchors(eid, anchors)
            if fixes:
                rt.bind_affect_anchors()
            for link in rt.long_term.pending_links():
                if link["episode_id"] in retry_ids:
                    rt.long_term.bump_fault(
                        link["episode_id"], turn=turn, state="failed",
                        payload=text, detail="补行请求后仍未绑定")
        for link in rt.long_term.anchor_actions()["failed"]:
            # 第三层：仍失败则只保留现场与错误日志，等人补全或丢弃
            rt.long_term.record_fault(
                link, turn=turn, state="failed", payload=text,
                detail="补行失败，等待人工补全或丢弃",
                available_tags=clear_tags)
        folded, folded_targets = fold_tool_blocks(
            run.calls, p.used, rt.memory)
        if folded_targets:
            rt.long_term.index_clear(folded_targets, turn)
        rt.turns_since_merge += 1
        rt.record_turn(line, text, turn, tools=folded)
        window.append(Turn(line, text, index=turn, tools=folded,
                           display=len(window) + 1))

        before = len(window)
        prefix = rt.assembler.prefix()
        try:
            cres = maybe_compress(window, marks["summaries"], cfg, client=rt.client,
                                  prefix_msgs=prefix, tools=TOOLS)
        except CompressError as e:
            print(C.paint(f"[压缩失败] {e}；请用 /compress 手动重试", C.ERROR))
            cres = None
        if cres is not None:
            window = cres.recent
            tagged = list(cres.new_lines)
            for t, line in zip(cres.taken, tagged):
                rt.session.append_summary(t.index, line)
            marks["summaries"] = list(cres.lines)
            for i, t in enumerate(window, 1):
                t.display = i
            if tagged:
                rt.merge_memory()
                rt.long_term.index_apply_compression(
                    [t.index for t in cres.taken],
                    [t.index for t in cres.recent])
                rt.long_term.index_reconcile(rt.session.turn_set())
        taken = sum(e.get("taken", 0) for e in cres.events) if cres is not None else 0
        compressed_turns += taken

        raw = sum(tl(t.user) + tl(t.assistant) for t in window)
        stok = sum(tl(s) for s in marks["summaries"])
        ctx = cfg.fixed_tokens + raw + stok

        if vr.repaired:
            d = vr.parsed.state_delta or {}
            u = vr.repair_usage or {}
            print(C.paint(f"[已补状态行] [[STATE affection={d.get('affection')} "
                          f"trust={d.get('trust')} mood={d.get('mood')} "
                          f"loc={vr.parsed.scene_loc or '-'}]] | "
                          f"补行请求 {u.get('prompt_tokens')} tok / "
                          f"{u.get('prompt_cache_hit_tokens')} hit", C.DEBUG))
        u = run.usage_totals
        ru = vr.repair_usage or {}
        repair_prompt = int(ru.get("prompt_tokens") or 0)
        prompt_tok = int(u.get("prompt_tokens") or 0) + repair_prompt
        hit_tok = int(u.get("prompt_cache_hit_tokens") or 0) + int(ru.get("prompt_cache_hit_tokens") or 0)
        out_tok = int(u.get("completion_tokens") or 0) + int(ru.get("completion_tokens") or 0)
        miss_tok = max(0, prompt_tok - hit_tok)
        hit_price, miss_price, out_price, period = current_rates(rt.config.pricing)
        cost = (hit_tok * hit_price + miss_tok * miss_price + out_tok * out_price) / 1_000_000
        total_cost += cost
        if money is not None:
            money -= cost
        rate = hit_tok / prompt_tok if prompt_tok else 0
        bal_text = f"¥{money:.2f}" if money is not None else "—"
        print(C.paint(f"[turn {turn} | {rt.state.to_line()} | finish={run.finish_reason} | "
                      f"请求 {prompt_tok} tok（补行 {repair_prompt}） | "
                      f"命中 {hit_tok}/{prompt_tok} ({rate:.0%}) | {period} | 本轮 ¥{cost:.4f} | "
                      f"累计 ¥{total_cost:.4f} | 余额 {bal_text}]", C.DEBUG))
        print(C.paint(f"[场景] {rt.scene.line()}", C.DEBUG))
        print(C.paint(f"[记忆] 已知 {len(rt.known_triggers)} 条 | 新增 {len(rt.memory_delta)} 条", C.DEBUG))
        print(C.paint(f"{rt.long_term.debug_line()} | 本轮新写 "
                      f"E{lt['events']}/P{lt['episodes']}/U{lt['used']} | "
                      f"候选 {','.join(rt.long_term.last_ids) or '-'} | "
                      f"工具 {','.join(c['name'] for c in run.calls) or '-'}", C.DEBUG))
        print(C.paint(f"[压缩] 上下文≈{ctx} tok | 未压缩 {len(window)} 轮 | 已压缩 {compressed_turns} 轮"
              f" | 摘要 {len(marks['summaries'])} 条/{stok} tok | 本轮压掉 {taken} 轮", C.DEBUG))
        cu = (cres.usage if cres is not None else None) or {}
        if cu:
            print(C.paint(f"[压缩请求] prompt={cu.get('prompt_tokens')} "
                          f"hit={cu.get('prompt_cache_hit_tokens')} "
                          f"out={cu.get('completion_tokens')}", C.DEBUG))
        if before != len(window):
            print(C.paint(f"        （窗口 {before} → {len(window)} 轮）", C.DEBUG))

    rt.close()
    sys.stdout.write(C.RESET)
    print("bye")


if __name__ == "__main__":
    main()

