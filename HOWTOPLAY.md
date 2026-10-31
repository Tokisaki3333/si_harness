# 使用与调参

## 1. 启动

```powershell
cd finish_modules\modules\shi_harness
Copy-Item config.example.json config.json
# 填 key，见下一节
python -m harness.cli
```

Python 要求 **3.10+**。无第三方 pip 依赖；数据库使用 Python 内置的 `sqlite3`，不需要外部服务。`requirements.txt` 为空。

其他配置文件：

```powershell
$env:HARNESS_CONFIG = "config.test.json"
python -m harness.cli
```

`HARNESS_CONFIG` 是相对当前目录的路径。

## 2. 密钥

两种方式，优先级从高到低：

1. `config.json` 的 `key_path` 指向的文件，默认 `key.json`：

```json
{"api_key": "sk-..."}
```

2. 环境变量 `DEEPSEEK_KEY`。

`config.json` 不再接受 `api_key` 字段；旧配置里的该字段会被忽略。

仓库自带的 `key.json` 是占位假值，仅用于防爬；替换成真实 key 后再用，真实 key 不要提交。`config.json` 仍在 `.gitignore` 中。

## 3. 提示词

| 槽位 | 配置项 | 默认 | 作用 |
|---|---|---|---|
| 人格卡 | `card_path` | `assets/01-人格卡-祀.md` | 固定 user 消息，短版人物设定 |
| L1 / 破甲 | `l1_path` | 空 | 固定 system 前缀；空则不注入 |
| 解锁提示词 | `l1_key_path` | 空 | 当前只读取、不注入，见下 |
| 资料索引 | `index_path` | `assets/index.example.json` | `【可查条目】` 的来源 |
| 资料正文 | `docs_path` | `assets/docs.json` | `lookup` 工具返回的内容 |

默认人格卡是短版；完整提示词在 `assets/祀-V1.2-提示词.md`（约 91KB），随仓库附带但不在默认链路。要换完整版：

```json
"card_path": "assets/祀-V1.2-提示词.md"
```

注意它会作为一条 user 消息整段注入，token 消耗明显高于短卡。建议复制一份再改，保留原文件。

`l1_path` 留空表示不使用 L1/破甲提示词。仓库不附带这两类文本；需要时自行放置到 `assets/private/`（已 gitignore），再把 `l1_path` 指向该文件。

`l1_key_path` 目前**不会**进入 system：`bootstrap.py` 读取后存入 `Runtime.l1_key`，但没有拼接点；`/unlock` 只把 `unlock=<token>` 写进状态行。不要依赖该槽位做模式切换。

`index_path` 默认只有 3 条示例。完整索引是 `assets/祀-V1.2-索引.json`，可自行替换；`docs_path` 同理。

## 4. 配置参数

### 顶层

| 参数 | 默认 | 说明 |
|---|---|---|
| `key_path` | `key.json` | 密钥文件路径，见上 |
| `l1_path` / `l1_key_path` | 空 | 提示词槽位，见上 |
| `unlock_key` | `7F2K` | `/unlock 1` 时写进状态行的 token |
| `card_path` | 短人格卡 | 人格卡 |
| `index_path` | 示例索引 | 可查条目 |
| `docs_path` | `assets/docs.json` | 资料正文 |
| `data_root` | `data` | 数据库目录，相对启动目录 |
| `session_id` | `cli` | 消息与会话快照的目录名 |
| `model` | `deepseek-flash` | 模型名；官方常用 `deepseek-chat` |
| `base_url` | `https://api.deepseek.com/v1/chat/completions` | 接口地址 |
| `timeout` / `retries` | `300` / `3` | 非流式请求超时与重试；流式不重试 |
| `bucket_size` | `500` | 每个消息分桶文件的条数 |
| `allow_merge` | `false` | `false` 每轮新卡；`true` 按 `thread_id` 合并/封存 |
| `compress` | 见下 | 压缩参数 |
| `pricing` | 见 `config.py` | 只影响成本显示，不影响请求 |

### compress

| 参数 | 默认 | 说明 |
|---|---|---|
| `context_limit` | `400000` | 固定前缀 + 窗口 + 摘要超过它就压缩；应不大于模型真实窗口 |
| `recent_turns_min` | `8` | 至少保留的轮数 |
| `recent_tokens_max` | `8000` | 窗口 raw token 超过它就压缩 |
| `min_compress_turns` | `3` | 一次至少压几轮 |
| `fixed_tokens` | `6000` | 固定前缀的估算值 |
| `summary_max_tokens` | `3000` | 摘要 token 上限；当前运行时不执行 |
| `summary_min_items` | `16` | 摘要保底条数；当前运行时不执行 |
| `max_summary_items` | `12` | 摘要实际硬上限，按条数从最旧丢弃 |

`summary_max_tokens` 与 `summary_min_items` 已定义，但 `trim_summary()` 没有接入压缩流程；当前行为是“最多 12 条，超出丢最旧”。详见 `TODO.md`。

### 计价

`pricing` 的字段用于 `/price` 与每轮成本显示。填错只影响数字，不影响请求。

## 5. 代码里写死的参数

这些没有配置项，要改代码。

| 位置 | 值 | 作用 |
|---|---|---|
| `harness/bootstrap.py` `Runtime.affect_candidate_limit` | `10` | 情感候选总条数上限 |
| `harness/bootstrap.py` `Runtime.affect_full_count` | `0` | 预展开全文条数；`3` 可回到 3 全文 + 7 索引 |
| `harness/bootstrap.py` `Runtime.dedup_enabled` | `True` | 过滤 `clarity=clear` 的卡 |
| `harness/bootstrap.py` `objective_block` | `ranked[:10]` | 客观命中最多 10 条索引 |
| `harness/long_term.py` `CANDIDATE_POOL` | `5000` | 每轮参与评分的卡数上限 |
| `harness/long_term.py` `render_block` | Δ 3 + 语义 5 + 随机 2 | 情感候选组配额 |
| `harness/cli.py` `max_tokens` | 3000 / INTENSE 4000 | 回复长度上限 |

### 把情感候选调到 20

只改 `affect_candidate_limit` 不够：它只截断，不会让各组多取。需要同时改三处配额：

1. `harness/bootstrap.py`：`affect_candidate_limit: int = 20`；
2. `harness/long_term.py` `render_block`：
   - `select(..., limit=3)` 的 Δ 组配额；
   - `hits[:5]` 的语义组配额；
   - `picker.sample(rest2, min(2, len(rest2)))` 的随机组配额；
3. 三组之和不超过 `affect_candidate_limit`，且 `CANDIDATE_POOL` 足够大。

例如 6 + 10 + 4 = 20。改完跑测试，并确认 prompt 预算能承受。

## 6. 数据库

默认在 `data_root`（相对启动目录，`config.example.json` 是 `data`）：

```text
data/
├── memory.db                     客观记忆：memory(trigger, entries)
├── long_term.db                  情感卡：episodes / episode_anchors /
│                                 events / memory_index / used_log /
│                                 user_state / memory_faults
└── sessions/<session_id>/
    ├── meta.db                   meta 表 + state_snapshots
    └── bucket_0000.db             messages（按 bucket_size 分桶）
```

`long_term.db` 使用 WAL，旁边可能出现 `-wal` / `-shm` 文件。

### 改路径

```json
"data_root": "D:/shi-data"
```

绝对路径可用。`session_id` 只决定 `sessions/` 子目录；情感卡库按 `owner_id=public / scope=shared` 共享，不按 session 隔离。

### 备份与重置

- 备份：停止进程，复制整个 `data_root`（包含 `-wal` / `-shm`）。
- 重置：停止进程，删除 `data_root`；下次启动自动建库。
- 只删某个会话：删 `sessions/<session_id>/`，`memory.db` / `long_term.db` 不受影响。

### 常用查询

```powershell
python -m harness.lt_cli --db data\long_term.db stats
python -m harness.lt_cli --db data\long_term.db episodes --limit 20
python -m harness.lt_cli --db data\long_term.db faults --state failed
python -m harness.lt_cli --db data\long_term.db delete ep_xxx --soft
```

## 7. 已知限制

- 候选池 5000、摘要 12 条；超长会话会触顶。
- 第 6 步卡库维护未做，卡数会持续增长。
- 重启只恢复最近 20 轮未压缩轮。
- `EVENT` 只写不读。
- `stream_chat` 无重试。

完整清单见 `TODO.md`。
