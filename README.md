# shi_harness

> **状态（2026-10-31）**：内核完整，可对话；记忆系统闭环；缓存命中与理论一致。
> 入口：`python -m harness.cli`（先复制 `config.example.json` 为 `config.json`，在 `key.json` 或 `DEEPSEEK_KEY` 提供密钥）。
> 验收：`python -m unittest discover -s tests -p "test_*.py"`（62 项）。
> Python >= 3.10；无第三方 pip 依赖；数据库使用标准库 `sqlite3`。使用与调参见 [HOWTOPLAY.md](HOWTOPLAY.md)，待办见 [TODO.md](TODO.md)。

祀的角色扮演运行时。负责上下文组装、状态机、会话记忆、资料库、压缩、CLI；长期记忆是子模块 `long_term_memory`，负责客观记忆与情感记忆。

## 目录

```text
shi_harness/
├── README.md
├── HOWTOPLAY.md
├── TODO.md
├── pyproject.toml
├── requirements.txt
├── reports/                    参考报告（最近 6 份）
├── config.example.json
├── assets/
│   ├── 01-人格卡-祀.md          默认短人格卡
│   ├── 祀-V1.2-提示词.md        完整提示词（可选）
│   ├── 祀-V1.2-索引.json        完整索引
│   ├── docs.json               资料正文
│   ├── index.example.json      示例索引
│   └── 测试-*                   测试角色资产
├── docs/
│   ├── 01-人格卡-祀.md
│   ├── 02-状态机协议.md
│   └── 06-期望效果.md
└── harness/
    ├── assembler.py      上下文组装
    ├── parser.py         状态行 / 记忆行解析
    ├── state.py          状态机
    ├── memory.py         旧版内存记忆（运行时不用；基线是 store/memory.py）
    ├── long_term.py      情感卡 + 召回 + 索引
    ├── affect.py         Δ 结构候选
    ├── lt_cli.py         长期记忆查看 / 删除命令行
    ├── docindex.py       资料索引与查询
    ├── compressor.py     压缩与摘要
    ├── client.py         DeepSeek 客户端
    ├── tools.py          工具循环
    ├── validate.py       协议补行
    ├── bootstrap.py      启动包
    ├── config.py         配置
    ├── pricing.py        计价
    ├── term.py           终端配色
    ├── scene.py          场景状态
    ├── guard.py          输入守卫
    ├── cli.py            交互式 CLI
    ├── store/            SQLite 存储（消息、记忆、快照）
    └── ../long_term_memory/  长期记忆子模块（文档）
```

## 依赖

Python >= 3.10。无第三方 pip 依赖；数据库使用 Python 内置的 `sqlite3`，不需要安装或运行外部数据库服务。

## 配置

```powershell
Copy-Item config.example.json config.json
```

密钥两种来源，优先级从高到低：

1. `key_path` 指向的文件（默认 `key.json`）。仓库自带一份占位假值，直接替换：

```json
{"api_key": "sk-..."}
```

2. 环境变量 `DEEPSEEK_KEY`。

`config.json` 不再接受 `api_key` 字段；旧配置里的该字段会被忽略。仓库里的 `key.json` 只是占位/防爬，不是有效密钥。

`config.json` 里的路径都相对本模块目录。默认 `card_path` 是短人格卡；完整提示词在 `assets/祀-V1.2-提示词.md`，不默认加载。`l1_path` 默认为空（不注入破甲提示词）。

## 启动

```powershell
cd modules\shi_harness
python -m harness.cli
```

使用其他配置：

```powershell
$env:HARNESS_CONFIG = "config.test.json"
python -m harness.cli
```

参数、提示词切换、代码内候选配额、数据库路径见 [HOWTOPLAY.md](HOWTOPLAY.md)。

## 记忆与打分

- 设计：[06-记忆与索引设计](long_term_memory/docs/06-记忆与索引设计.md)（子模块）
- 剧情打分基线：[runs/2026-10-29_flash_disabled_v2.jsonl](../../test-data/祀-剧情/runs/2026-10-29_flash_disabled_v2.jsonl)
- 当前量表：`v/a/agency/self_focus` + `summary` + `diary`
- 旧 v/a/b/i 基线：[runs/2026-10-29_flash_disabled.jsonl](../../test-data/祀-剧情/runs/2026-10-29_flash_disabled.jsonl)（历史）
- 当前参数：`deepseek-flash`、`thinking: disabled`、`temperature 0`

## 长期记忆运行时

- 实现：`harness/long_term.py`，归属于 `long_term_memory` 子模块
- 每轮协议：`[[EVENT {...}]]` / `[[EPISODE {...}]]` / `[[USER_STATE ...]]` / `[[USED ...]]`
- 情感候选：0 条全文 + 最多 10 条索引（Δ 3 + tag 命中 ≤5 + 随机 2；命中不足不补）；`affect_full_count=3` 可回到 3+7
- 客观候选：严格 tag 命中，最多 10 条索引，命中不足不补；不做预展开
- 0+10 是当前默认；`index_only_test.py` 仍显式设 `affect_full_count=0`
- 验收脚本：[tests/acceptance/README.md](tests/acceptance/README.md)
- 工具：`memory_read(target=ep_… / mem:id / trigger)` 展开候选正文
- 无 anchor / 格式错误：下轮提示补交 `[[ANCHOR …]]`；仍未补上才发一次补行请求；再失败写 `memory_faults`，用 `python -m harness.lt_cli faults --state failed` 处理（`set-anchors` 补全 / `delete` 丢弃）
- `USER_STATE`：只写入 `user_state` 表作日志，不进入上下文，不参与检索/权重/USED
- `/lt`：查看事件数、情感卡数、USED 数、最近卡与当前候选
- 测试：`tests/test_long_term.py`、`tests/test_memory_store.py`、`tests/test_runtime_integration.py`

## 指令

```text
/mod 0|1|2|3   0 日常 / 1 亲密 / 2 情色 / 3 退出中
/unlock 0|1    L1 收紧 / 放开
/state         状态机
/mem           记忆索引
/lt            长期记忆统计与最近卡
/reload [n]    从数据库恢复最近 n 轮
/compress      手动压缩
/price         当前计价
/balance       查询余额
/help          帮助
/quit          退出
```

## 作为库调用

```python
from harness.bootstrap import bootstrap

rt = bootstrap("config.json")
msgs = rt.build_messages("你好", window=[])
res = rt.client.chat(msgs, max_tokens=3000)
rt.record_turn("你好", res.message["content"], turn=1)
rt.close()
```

## 与 bili_app_control 的关系

本模块不依赖 B 站控制层。接入时由上层把私信文本作为 `user_msg` 传入，把模型回复发回即可。

## 注意

- 不含 `key.json`、`config.json`、`data/`；
- 压缩失败**不本地回退**：抛 `CompressError`，CLI 报错并提示用户用 `/compress` 手动重试；
- 所有会话默认共用一个 `session_id`（`cli`），需要隔离时改配置。

## 心念层

每轮正文前写 `【心念】`，30 字以内，第一人称碎片，写动机、顾虑或身体反应。

- CLI 不展示这一段，但它随 `assistant` 消息进入上下文，下一轮模型能看到；
- 可以跑题、想错、自欺、重复，不必每轮都有新结论；
- 解析器按 `【心念】` 到 `【正文】` 或空行截取，并从正文剥离；
- 缺失策略：允许空 1 轮，连续 2 轮才补行，补行时提示数值平滑。
## 期望效果

跑起来应该看到什么、什么算不达标、交接验收清单，见 [docs/06-期望效果.md](docs/06-期望效果.md)。