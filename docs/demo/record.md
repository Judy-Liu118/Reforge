# How to record the README demo

Reforge demos best as a self-healing chain: failure -> reflection -> recovery.
Below is the exact scripted scenario and the one-line command to record it.

## What the demo shows

A single `reforge "..."` invocation that:

1. plans + generates Python against `sales.csv`
2. **fails on the first attempt** (wrong column name)
3. governor classifies failure, decides RETRY
4. reflection node consults memory, rewrites the code
5. **second attempt succeeds**; final answer printed

The whole loop is governed and event-logged — viewer should see the
`EXECUTION_FAILED` and `RECOVERY_ATTEMPTED` events flicker past before
the green `EXECUTION_SUCCEEDED`.

## Prereqs

```powershell
# Once, anywhere on PATH
pip install asciinema     # records the terminal as a .cast file
cargo install --git https://github.com/asciinema/agg   # renders .cast -> .gif
```

`agg` needs Rust. If you don't have it, the `.cast` file alone is
embeddable on asciinema.org and renders fine in a markdown viewer.

## Record

From the repo root:

```powershell
asciinema rec docs/demo/demo.cast --command "python -m reforge.cli.main 'read sales.csv, compute average revenue per region'"
```

When the run finishes, Ctrl-D exits the recorder. The cast is now in
`docs/demo/demo.cast` (~50 KB).

## Render to GIF (optional)

```powershell
agg docs/demo/demo.cast docs/demo/demo.gif --theme monokai --speed 1.5
```

## Embed in README

```markdown
![Self-healing demo](docs/demo/demo.gif)
```

or, for asciinema-hosted playback:

```markdown
[![asciicast](https://asciinema.org/a/XXXX.svg)](https://asciinema.org/a/XXXX)
```
