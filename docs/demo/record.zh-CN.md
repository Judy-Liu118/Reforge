# 如何录制 README 里的 demo

[English](record.md) | 简体中文

Reforge 最好的演示方式是展示一条自愈链路：失败 → 反思 → 恢复。下面给出完整的脚本化场景，
以及录制它所需的那一行命令。

## 这个 demo 展示了什么

一次 `reforge "..."` 调用，其中：

1. 针对 `sales.csv` 做规划并生成 Python 代码
2. **第一次尝试失败**（列名写错）
3. governor 对失败做分类，决定 RETRY
4. 反思节点查询记忆，重写代码
5. **第二次尝试成功**；打印出最终答案

整个循环都受治理并有事件日志记录 —— 观众应当能看到 `EXECUTION_FAILED` 和
`RECOVERY_ATTEMPTED` 事件一闪而过，然后才是绿色的 `EXECUTION_SUCCEEDED`。

## 前置条件

```powershell
# 一次性安装，装到 PATH 上的任意位置即可
pip install asciinema     # 把终端录成 .cast 文件
cargo install --git https://github.com/asciinema/agg   # 把 .cast 渲染成 .gif
```

`agg` 需要 Rust 环境。如果你没装，光有 `.cast` 文件也够用 —— 它可以嵌到 asciinema.org 上，
在 markdown 阅读器里也能正常播放。

## 录制

在仓库根目录下执行：

```powershell
asciinema rec docs/demo/demo.cast --command "python -m reforge.cli.main 'read sales.csv, compute average revenue per region'"
```

运行结束后按 Ctrl-D 退出录制器。录像会保存在 `docs/demo/demo.cast`（约 50 KB）。

## 渲染成 GIF（可选）

```powershell
agg docs/demo/demo.cast docs/demo/demo.gif --theme monokai --speed 1.5
```

## 嵌入 README

```markdown
![Self-healing demo](docs/demo/demo.gif)
```

或者用 asciinema 托管播放：

```markdown
[![asciicast](https://asciinema.org/a/XXXX.svg)](https://asciinema.org/a/XXXX)
```
