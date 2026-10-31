# 待办

## P0 召回主干

- [ ] 候选池分页。`LongTermMemory._candidates` 目前一次 `recent_episodes(5000)` 读回，每轮解析全部 anchors；5000 张卡是硬上限。超过后需 SQL 分页 + 倒排/向量检索，或离线预排序。
- [ ] 客观召回全表扫描。`Runtime.objective_block` 调 `MemoryDB.all()`，无 `ORDER BY`、无 `LIMIT`，每轮 O(N) 扫描后再排序截断。需要索引 + 分页，或由维护工序预生成候选。
- [ ] 第 6 步：记忆卡总库维护。当前 no-merge 每轮新卡，同 tag / 同心境卡线性累积；缺单卡密度检查、同质卡合并、冲突卡处理。卡总数会持续增长并挤占召回预算。
- [ ] 短时去重接线。`Runtime.known_affect_ids` 从未 `add`，也未传入 `render_block(exclude_ids=...)`；`remember_selected` 已标弃用。按压缩 epoch 去重需在精调召回器里重做。

## P1 压缩与持久化

- [ ] 摘要契约。`trim_summary` 从未调用；`summary_max_tokens`、`summary_min_items=16` 运行时不生效；`max_summary_items=12` 只按条数 `pop(0)`。需要“按 token 上限丢弃 + 保底条数”。
- [ ] 每轮一条校验。压缩返回条数少于轮数时，无告警、无补行；缺摘要的轮次只能靠记忆卡兜底。
- [ ] 重启丢轮。`reload_window(20)` 只恢复最近 20 轮未压缩轮；压缩失败不回退时窗口继续增长，超过 20 轮再重启会丢中间轮。
- [ ] `rewind` 与 `index_reconcile`。`/rewind` 未启用；`index_reconcile` 只在压缩后调用，删除轮次的 `forgotten` 路径没有入口。
- [ ] `context_limit` 与模型窗口。默认 400000 高于常见 DeepSeek 窗口；`validate` 只校验“压缩后 < 触发点”，不校验模型真实上限。

## P2 召回精调

- [ ] 客观二级索引。未命中项折叠为“地理知识 / 人文知识”这类二级入口，由记忆卡维护工序分配；当前未接入，未命中项完全不进上下文。
- [ ] 动态索引长度。命中超过 10 条时的截断策略、按分数/预算动态伸缩。
- [ ] 情感语义匹配。只用 anchors token 做字符串包含，无分词、同义词、向量；命中不足时语义组为空（已按设计改）。
- [ ] `ROLE_ALIASES` 去硬编码。当前是全局默认（傅雷/傅聪），应从角色卡/配置注入，或生产调用传 `aliases=None`。
- [ ] `memory_read(ep_)` 输出粒度。返回整行 JSON（含 `owner_id/scope/status/revision/created_at` 等内部字段），应只给卡正文与必要元数据。
- [ ] `EVENT` 只写不读。每轮要求模型输出 `[[EVENT]]`，热路径不召回 `events` 表；接入召回或移除输出要求。
- [ ] `USER_STATE` 接线。目前只写 `user_state` 表作日志，不参与检索/权重/USED；需定是否作为召回输入。
- [ ] `current_state()` 口径。当前用最近一张卡的四维作为“当前情感”，是否改接状态机或用户状态，需定。
- [ ] `apply_selected` / `remember_selected`。已标弃用，待删除或迁入精调召回器。
- [ ] 候选开关接配置。`affect_full_count`、`affect_candidate_limit`、`dedup_enabled` 写死在 `Runtime`，配置文件不读。
- [ ] `stream_chat` 重试。非流式 `chat()` 有重试，流式主链路没有。
- [ ] 动态块顺序注释。`Assembler` 注释说场景/状态/候选在本轮输入之前，实际在之后；注释未同步。
