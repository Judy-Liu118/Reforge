# Reforge 记忆子系统

[English](README.md) | 简体中文

Reforge 持久化了四种彼此不同的经验。它们在磁盘上长得很像（JSON / JSONL 追加日志），
但在运行时里扮演的角色完全不同。扩展记忆子系统时，最常见的困惑就来自把它们搞混 ——
建议把下面这张对照表放在手边。

## 四种记录类型

| 类型 | 存储位置 | 归属 | 用途 |
|------|---------|-------|---------|
| `MemoryRecord` | `data/memory/{recovery,failures,success_patterns}.json` | `MemoryStore` + `CompositeMemorySubstrate` | 跨会话的**失败 / 恢复 / 成功模式**，由 planner 和 reflection 节点通过 `MemorySubstrate` Protocol 消费。 |
| `ExecutionRecord` | `data/execution_memory.jsonl` | `ExecutionMemory` | 逐次尝试的**执行学习**（failure_mode、problem_signature），供 `ClassifyStage` 识别重复失败并据此调整重试策略。 |
| `TrajectoryRecord` | `data/trajectories.jsonl` + `data/multistep_trajectories.jsonl` | `TrajectoryStore` | 单个会话的**完整执行弧线回放**（每一次尝试、每一个评估分数）。被 `PlannerMemoryContext` 用于相似会话召回，也被 CLI 的 `--replay` 参数使用。 |
| `ResearchResult` | `data/research.jsonl` | `ResearchStore` + `ResearchMemory` | **研究会话产物**（假设 → 验证 → 结论）。供 `ResearchPlanner` 避免重复验证已有定论的模式。 |

## 读取路径

- **规划**（`graph/nodes/planner.py`）
  → `PlannerMemoryContext` → `MemorySubstrate.recall_for_planning()`（只返回 `MemoryRecord`）。
  当 `TrajectoryStore` 被接入时，同一个上下文还会调用 `find_similar()` 查询历史弧线。

- **反思**（`graph/nodes/reflection.py`）
  → `MemorySubstrate.recall()`（只返回 `MemoryRecord`）—— 找出与当前错误匹配的既往恢复经验。

- **重试分类**（`governor/classify_stage.py`）
  → `ExecutionMemory.recall_similar()`（返回 `ExecutionRecord`）—— 把失败模式信号送进
  governor 流水线。

- **研究规划**（`runtime/research/planner.py`）
  → `ResearchMemory.recall_patterns()`（在 `ResearchResult` 上查询 `ResearchStore`）——
  把跨会话的研究模式注入到假设生成中。

## 写入路径

- `MemoryStore.save(record)` —— 从图节点之外调用（目前是 CLI / 测试）。节点从不直接写
  `MemoryRecord`。
- `TrajectoryStore.save(record)` —— 在 `final_response` 节点触发时，由
  `RuntimeRunner.stream()` 调用。
- `ExecutionMemory.write(record)` —— 目前也是在保存轨迹的同一时机调用。
- `ResearchStore.save(result)` —— 由 `ResearchSession.run()` 和 `cli/research.py` 调用。

## 基座 Protocol

`MemorySubstrate`（定义在 `reforge/memory/substrate.py`）是图节点读取记忆的唯一接口。
默认实现 `CompositeMemorySubstrate` 包装了 `MemoryStore` + `MemoryRetriever`，但测试和
未来的部署可以在不碰节点代码的前提下换成别的后端（比如 SQLite、向量库）。

```python
from reforge.runtime.engine.runner import RuntimeRunner
from my_app.vector_substrate import VectorMemorySubstrate

runner = RuntimeRunner(memory_substrate=VectorMemorySubstrate(url="..."))
```

planner 和 reflection 两个节点都通过 `build_graph(memory_substrate=...)` 的构造器注入
拿到这个基座。

## 路径迁移

在 P-R 之前，`MemoryRecord` 的 JSON 文件放在项目根目录（`memory/`）。P-R 把所有持久化
统一收敛到了 `data/` 之下。`MemoryStore.__init__` 里有一段一次性的兜底逻辑，会把遗留
文件迁移到 `data/memory/` 并删除旧目录；每次启动都调用它是安全的。
