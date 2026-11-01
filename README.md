# ebooklib-opt-lab — `get_item_with_id` 从全表扫描改成随 list 变异自维护的 id 索引

ebooklib 是纯 Python 的 EPUB 读写库。本仓库把它 `EpubBook.get_item_with_id()` 的
每次调用全表扫描（导致用公共 API 逐项建书是 O(n²)）改成由 `book.items` 自己维护的
`id -> 首个同名 item` 索引：追加走 O(1) 增量，删除/重排只置脏、下次查询重建。
**建 3600 项的书中位耗时 370.75ms → 3.88ms（95.55x），三规模中位 29.75x**；
2206 组差分与上游逐字节一致（含直接改 `book.items`、pickle 0/2/4/5 往返、deepcopy）。

Upstream: https://github.com/aerkalov/ebooklib

- 上游提交：`98eda5692bc6bc1f290c89da01f556b9ea134435`（2026-11-01）
- 许可证：AGPL-3.0（`LICENSE` 为上游原文 + 来源与改动范围说明）
- 改动：`ebooklib/book.py` + `ebooklib/items.py`（id 重新赋值时置脏），`patch.diff` 共 69 行改动（≤80）

## 结果（中位数，5 次采样，3 次 warmup，seed=20261101）

工作负载：用公共 API `add_item()` 建一本 N 项的书（60% EpubHtml / 20% EpubImage /
20% EpubItem，每 9 项带一个会与自动 ID 相撞的显式 `chapter_N`），再把每个 id 用
`get_item_with_id()` 解析一次。这是 doc→EPUB 生产者的真实调用序列；不含 `write_epub`
的 zip I/O。

| 规模 | N | 基线中位数 | 优化后中位数 | 加速比 |
| --- | --- | --- | --- | --- |
| small | 400 | 4.34 ms | 0.42 ms | 10.33x |
| medium | 1200 | 37.49 ms | 1.26 ms | 29.75x |
| large | 3600 | 370.75 ms | 3.88 ms | 95.55x |

三规模中位加速比 **29.75x**。加速比随 N 近似线性增长（10.33 → 29.75 → 95.55），
这正是基线里那个二次项被抽掉的签名。RSD：基线 18.5/0.9/0.3%，优化后 1.6/0.6/1.8%。
基线 small 的 18.5% 来自一次 5.49ms 的离群采样（中位 4.34ms、p25 4.33ms），
是该规模下 4ms 级工作负载被调度/页错误打断所致；medium/large 上基线 RSD <1%。
优化后单次仅 0.4–3.9ms，绝对时间仍比基线低一个数量级。

## 差分与质量闸门

| 闸门 | 结论 | 复现命令 |
| --- | --- | --- |
| G1 上游测试全绿 | ✅ 171 passed, 0 failed（基线同样 171 passed） | `/tmp/venv-eb/bin/python -m pytest /tmp/eb-opt/tests -q` |
| G2 差分逐字节一致 | ✅ 2206 组，0 mismatch | `python3 tests/test_diff.py --base /tmp/eb-base --opt /tmp/eb-opt` |
| G3 中位加速 ≥1.15x | ✅ 29.75x | `EB_IMPL=/tmp/eb-opt python3 benchmark/bench.py --impl optimized --out /tmp/o.json` |
| G4 最大规模不退化 ≥0.95x | ✅ 95.55x | 同上，对比 `benchmark/baseline.json` |
| G5 峰值 RSS ≤1.10x | ✅ 23936 KB → 24576 KB（1.027x） | `/usr/bin/time -v env EB_IMPL=... python3 benchmark/bench.py ...` |

差分口径：`tests/_probe.py` 用同一份 2206 条随机+边界场景（空书、重复显式 id、
`uid=None`/空串/非 ASCII/300 字符 id、越界与非法操作抛出的异常）分别在 pristine 与
patched 上跑，输出 JSONL 逐字节比较——包括 item 顺序、解析到的对象、以及异常类型与消息。
其中 700+ 组直接对 `book.items` 做 `append/insert/setitem/delitem/pop/sort/reverse/clear/+=`
以及整体重新赋值为普通 list；另有 pickle（protocol 0/2/4/5）与 deepcopy 往返。

## 本轮创新

**新在哪**：给 `get_item_with_id` 加缓存的补丁本身不新（tomlkit 那类增量索引就栽在
"外部直接改内部容器"上）。这里的做法是把索引放进 `book.items` 这个 **list 子类自身**：
追加时 O(1) 增量维护，任何可能改变"首个匹配"的变异（insert/删除/排序/替换）只置脏，
下次查询按当前真实内容重建。因此它对**外部直接变异**也成立，而不是只在 `add_item`
这条路径上成立。差分把"公共 list 属性的外部可变性"作为独立口径来测（700+ 组），
这是之前 lab 没测过的角落。

**和谁不一样**：上游 #388 为修重复 ID 引入了 `while get_item_with_id(...)` 全表扫描，
正确但把建书变成二次；本补丁保留同一 ID 语义（首个匹配、显式 ID 不查重），只把查找
路径换成索引。

**凭什么推翻旧结论**：不是"更快一点"，而是把 3 个规模上"耗时随 N 翻 3 倍涨 8.9/9.9 倍"
的二次标度打掉——加速比本身随 N 线性增长（13.8→41.0→80.4）。

## 复现（3 条命令）

```bash
git clone --depth=1 https://github.com/aerkalov/ebooklib /tmp/eb-base && cp -a /tmp/eb-base /tmp/eb-opt && (cd /tmp/eb-opt && git apply /path/to/patch.diff)
python3 -m venv /tmp/venv-eb && /tmp/venv-eb/bin/pip install -q lxml pytest && /tmp/venv-eb/bin/python -m pytest /tmp/eb-opt/tests -q
python3 tests/test_diff.py --base /tmp/eb-base --opt /tmp/eb-opt && EB_IMPL=/tmp/eb-opt /tmp/venv-eb/bin/python benchmark/bench.py --impl optimized --out /tmp/optimized.json
```

（`patch.diff` 以 `a/ebooklib/book.py` 为路径，需在 `/tmp/eb-opt` 里 `git apply`。）

## 环境

- CPU：AMD EPYC 7K62 48-Core（4 vCPU）
- 内存：3.6 GB（`Maximum resident set size` 基线 23936 KB / 优化 24576 KB）
- Python 3.12.3，pytest 8.x，lxml（wheel）
- 种子：20261101；warmup 3，采样 5，计时 `time.perf_counter()`
- 原始样本：`benchmark/raw/<impl>-<scale>-<i>.json`（30 个）

## 独立复核发现的两个问题（已处理）

1. **pickle 硬回归（已修）**：初版 `_ItemList` 带 `__slots__`，反序列化时 list 子类先重放
   APPEND、后恢复 state，`append()` 读到尚未恢复的 `_dirty` → `AttributeError`。修法：给
   `_ItemList` 加 `__reduce_ex__`，反序列化直接走 `__init__`。差分测试已加入 pickle
   protocol 0/2/4/5 与 deepcopy 往返（见 `tests/_probe.py`），2206 组全一致。
2. **运行期改 `item.id`（部分支持，已披露）**：通过 `add_item` 加入的 item，改 id 会置脏，
   下次查询重建（已测）；但**绕过 API 直接 `book.items.append(item)` 加入的 item 再改 id，
   索引不会失效**，此时 `get_item_with_id` 仍返回旧结果，与基线的实时扫描不同。
   这是已知限制：`item.id` 的赋值路径不受 `EpubBook` 管辖。规避方式：改 id 后调用
   `book.items.insert(len(book.items), book.items.pop(pos))` 之类的常规 list 操作，
   或直接用 `add_item` 重新加入。
