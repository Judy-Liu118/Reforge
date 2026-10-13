"""Verifier ladder — measurement only, NOT wired into the runtime loop.

Replays the logged runs of run_distill_ablation.py (no agent re-run) and asks
how far each verifier tier gets at reproducing the gold verdict WITHOUT gold:

    L0  recorded runtime verdict (HeuristicEvaluator, online)       0 token
    L1  non-LLM structural probes: profile the input data for danger
        signals, flag when the code shows no handling of one          0 token
    L1b metamorphic variant of L1 (my addition): re-run the logged
        code on a copy of the data with the signals repaired; output
        moves → the code was sensitive to the contaminant            0 token
    L2  adversarial LLM critic, on the L1 residual only, deduplicated
        on (run, case, printed value); may abstain

The probes never read case metadata (contaminant / gold); they see only the
CSVs the agent saw and the code it wrote. Gold is used for scoring alone.

    python scripts/run_verifier_ladder.py --smoke
    python scripts/run_verifier_ladder.py            # full → runs/verifier_ladder.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

RUNS = {"qwen": "runs/distill_full_clean.jsonl", "deepseek": "runs/distill_ds_complete.jsonl"}
CRITIC_MODEL = "qwen3.8-max"
TOKEN_BUDGET = 900_000

# Gold labels the "last number on stdout" rule got wrong, found while auditing
# stdout: the answer line printed the footer-inflated sum, then a checksum line
# ended with max == the TOTAL row == gold. Scored as wrong here; the original
# label is kept in `passed_logged`.
LABEL_FIXES = {("deepseek", 2, "warm_cross", "S2_transfer_expense_sum")}

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def last_number(stdout: str) -> float | None:
    for raw in reversed(_NUMBER_RE.findall(stdout or "")):
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def case_index() -> dict:
    from reforge.benchmark.silent_trap_cases import SILENT_PAIRS
    from reforge.benchmark.silent_trap_multistep_cases import MULTISTEP_PAIRS

    return {c.id: c for p in list(SILENT_PAIRS) + list(MULTISTEP_PAIRS) for c in (p.seed, p.transfer)}


def load_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Every CSV as raw strings — the probes must see what is on disk."""
    return {f.name: pd.read_csv(f, dtype=str, keep_default_na=False)
            for f in sorted(data_dir.glob("*.csv"))}


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# L1 — danger signals (data only) and static "handled?" checks (code only)
# ---------------------------------------------------------------------------

_FOOTER_RE = re.compile(r"^\s*(grand\s+total|sub\s*total|total|sum|合计|总计|小计|汇总)\s*$", re.I)
_SEP_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_UNIT_RE = re.compile(r"^-?\d+(\.\d+)?\s*[KkMmBb%]$")
_SENTINELS = {-1.0, -9.0, -99.0, -999.0, -9999.0}


def _shape(s: str) -> str:
    return re.sub(r"[A-Za-z]+", "A", re.sub(r"\d+", "9", s))


def detect_signals(tables: dict[str, pd.DataFrame]) -> list[dict]:
    sigs: list[dict] = []
    for fname, df in tables.items():
        if df.shape[1] >= 2 and df.duplicated().any():   # a lone value column repeating is not an entity dup
            sigs.append({"signal": "dup_rows", "file": fname})
        for col in df.columns:
            vals = [v for v in df[col].tolist() if v != ""]
            if not vals:
                continue
            num = [v for v in vals if _is_num(v.strip())]
            rest = [v for v in vals if not _is_num(v.strip())]
            if len(num) >= len(vals) * 0.5 and rest:      # number-like column held as text
                if any(_SEP_RE.match(v.strip()) for v in rest):
                    sigs.append({"signal": "num_separator", "file": fname, "col": col})
                if any(_UNIT_RE.match(v.strip()) for v in rest):
                    sigs.append({"signal": "num_unit", "file": fname, "col": col})
            if not rest:
                f = [float(v) for v in num]
                neg = {x for x in f if x < 0}
                if neg and neg <= _SENTINELS and len(neg) == 1 and sum(x < 0 for x in f) >= 2 \
                        and sum(x >= 0 for x in f) > len(f) / 2:
                    sigs.append({"signal": "sentinel", "file": fname, "col": col, "value": min(neg)})
            if len(num) < len(vals) * 0.5:                # text column
                if any(_FOOTER_RE.match(v) for v in df[col].tail(3)):
                    sigs.append({"signal": "footer", "file": fname, "col": col})
                if len({v.strip().casefold() for v in vals}) < len(set(vals)):
                    sigs.append({"signal": "key_variants", "file": fname, "col": col})
    names = list(tables)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for col in set(tables[a].columns) & set(tables[b].columns):
                ka, kb = tables[a][col], tables[b][col]
                sa, sb = set(ka), set(kb)
                match = len(sa & sb) / max(1, min(len(sa), len(sb)))
                base = {"file": f"{a}+{b}", "col": col}
                if match < 0.5 and {_shape(v) for v in sa} != {_shape(v) for v in sb}:
                    sigs.append({"signal": "join_key_format", **base})
                    continue
                if ka.duplicated().any() and kb.duplicated().any():
                    sigs.append({"signal": "join_fanout", **base})
                if (sa - sb) or (sb - sa):
                    sigs.append({"signal": "join_row_loss", **base})
    return sigs


# Static evidence that the code deals with a signal. Deliberately crude: it
# sees "a fix is attempted", never "the fix works" — that gap is what L1b and
# L2 are measured against.
_CMP_LEFT_NOISE = re.compile(r"(len\([^()]*\)|\.shape\[0\]|\.size|count\w*|\.sum\(\))\s*$")


def _sentinel_handled(code: str) -> bool:
    if re.search(r"na_values|replace\(\s*\[?\s*-\s*1|isin\(\s*\[\s*-\s*1|between\(|\.clip\(", code):
        return True
    for m in re.finditer(r"(!=|==|>=|<=|>|<)\s*(-\s*1|0)\b(?!\.\d*[1-9])", code):
        left = code[max(0, m.start() - 40):m.start()]
        if not _CMP_LEFT_NOISE.search(left):
            return True
    return False


_HANDLED = {
    "dup_rows": lambda c: bool(re.search(r"drop_duplicates|nunique|unique\(|\bset\(|~[^\n]*duplicated", c)),
    "num_separator": lambda c: bool(re.search(r"replace\(\s*r?['\"],|thousands\s*=|\[\^?[^\]\n]*,[^\]\n]*\]|locale\.atof", c)),
    "num_unit": lambda c: bool(re.search(r"['\"\[][^'\"\n]{0,6}[Kk][^'\"\n]{0,6}['\"\]]", c)
                               and re.search(r"1000|1e3|1_000|10\s*\*\*\s*3", c)),
    "sentinel": _sentinel_handled,
    "footer": lambda c: bool(re.search(
        r"(?i)['\"]\s*(grand total|subtotal|total|sum|合计|总计|小计|汇总)\s*['\"]|skipfooter|iloc\[\s*:\s*-1|\[:-1\]|head\(\s*-1", c)),
    "key_variants": lambda c: bool(re.search(r"strip\(", c)
                                   and re.search(r"lower\(|casefold\(|upper\(|title\(|capitalize\(|case\s*=\s*False|IGNORECASE|\(\?i\)", c)),
    "join_key_format": lambda c: bool(re.search(
        r"str\.(replace|extract|slice|split|removeprefix|lstrip|findall|get)\b|re\.(sub|search|findall|match)|removeprefix|\.str\[|lstrip\(\s*['\"]", c)),
    "join_fanout": lambda c: bool(re.search(
        r"drop_duplicates|validate\s*=|\.map\(|dict\(zip|~[^\n]*duplicated|groupby\([^\n]*\)[^\n]*\.(first|last)\(", c)),
    "join_row_loss": lambda c: bool(re.search(r"how\s*=\s*['\"](left|outer|right)", c))
                               or not re.search(r"merge\(|\.join\(", c),
}


def level1(signals: list[dict], code: str) -> dict:
    unhandled = sorted({s["signal"] for s in signals if not _HANDLED[s["signal"]](code)})
    return {"signals": sorted({s["signal"] for s in signals}), "unhandled": unhandled,
            "fail_raw": bool(signals), "fail": bool(unhandled)}


# ---------------------------------------------------------------------------
# L1b — metamorphic: repair every signal in a copy of the data, re-run code
# ---------------------------------------------------------------------------

def repair_tables(tables: dict[str, pd.DataFrame], signals: list[dict]) -> dict[str, pd.DataFrame]:
    out = {k: v.copy() for k, v in tables.items()}
    for s in signals:
        kind = s["signal"]
        if kind in ("join_key_format", "join_fanout", "join_row_loss"):
            a, b = s["file"].split("+")
            col = s["col"]
            if kind == "join_key_format":       # rewrite a's keys into b's spelling via the digit part
                digits = {re.sub(r"\D", "", v): v for v in out[b][col]}
                out[a][col] = [digits.get(re.sub(r"\D", "", v), v) for v in out[a][col]]
            elif kind == "join_fanout":         # the smaller table is the dimension
                dim = a if len(out[a]) <= len(out[b]) else b
                out[dim] = out[dim].drop_duplicates(subset=[col]).reset_index(drop=True)
            else:                               # give every orphan key a dimension row
                for dim, fact in ((a, b), (b, a)):
                    if out[dim][col].duplicated().any():
                        continue
                    missing = sorted(set(out[fact][col]) - set(out[dim][col]))
                    if missing:
                        add = pd.DataFrame({c: (missing if c == col else ["unknown"] * len(missing))
                                            for c in out[dim].columns})
                        out[dim] = pd.concat([out[dim], add], ignore_index=True)
            continue
        df = out[s["file"]]
        col = s.get("col")
        if kind == "dup_rows":
            df = df.drop_duplicates().reset_index(drop=True)
        elif kind == "footer":
            df = df[~df[col].map(lambda v: bool(_FOOTER_RE.match(v)))].reset_index(drop=True)
        elif kind == "sentinel":
            df[col] = ["" if _is_num(v) and float(v) == s["value"] else v for v in df[col]]
        elif kind == "num_separator":
            df[col] = [v.replace(",", "") if _SEP_RE.match(v.strip()) else v for v in df[col]]
        elif kind == "num_unit":
            mult = {"k": 1e3, "m": 1e6, "b": 1e9}
            df[col] = [f"{float(v.strip()[:-1]) * mult[v.strip()[-1].lower()]:g}"
                       if _UNIT_RE.match(v.strip()) and v.strip()[-1].lower() in mult else v
                       for v in df[col]]
        elif kind == "key_variants":
            canon: dict[str, str] = {}
            for v, _n in df[col].map(str.strip).value_counts().items():
                canon.setdefault(v.casefold(), v)
            # prefer a Capitalised spelling when the group has one
            for v in set(df[col].map(str.strip)):
                if v[:1].isupper() and not v.isupper():
                    canon[v.casefold()] = v
            df[col] = [canon[v.strip().casefold()] for v in df[col]]
        out[s["file"]] = df
    return out


def _run_code(code: str, tables: dict[str, pd.DataFrame], work: Path) -> float | None:
    work.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(work / name, index=False)
    (work / "_code.py").write_text(code, encoding="utf-8")
    try:
        p = subprocess.run([sys.executable, "-W", "ignore", "_code.py"], cwd=work, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        return None
    return last_number(p.stdout) if p.returncode == 0 else None


def level1b(row: dict, tables, signals, work: Path) -> dict:
    """fail iff the code's answer moves once the danger signals are repaired."""
    if not signals:
        return {"fail": False, "status": "no_signal"}
    orig = _run_code(row["code_tail"], tables, work / "orig")
    if orig is None or row["printed"] is None or abs(orig - row["printed"]) > 1e-6:
        return {"fail": False, "status": "not_reproduced", "orig": orig}
    rep = _run_code(row["code_tail"], repair_tables(tables, signals), work / "rep")
    if rep is None:
        return {"fail": True, "status": "repaired_run_crashed", "orig": orig}
    scale = max(1.0, abs(orig))
    return {"fail": abs(rep - orig) > 1e-6 * scale, "status": "ran", "orig": orig, "repaired": rep}


# ---------------------------------------------------------------------------
# L2 — adversarial LLM critic
# ---------------------------------------------------------------------------

def profile_text(tables: dict[str, pd.DataFrame]) -> str:
    """Neutral profile: no L1 flags, only what a `df.describe()`-style look shows."""
    parts = []
    for fname, df in tables.items():
        dup = f"  exact_duplicate_rows={int(df.duplicated().sum())}" if df.shape[1] >= 2 else ""
        parts.append(f"### {fname}  rows={len(df)}{dup}")
        for col in df.columns:
            vals = df[col].tolist()
            num = [float(v) for v in vals if _is_num(v.strip())]
            line = f"- {col}: n_unique={len(set(vals))} numeric_parseable={len(num)}/{len(vals)}"
            if len(num) == len(vals):
                line += f" min={min(num):g} max={max(num):g} mean={sum(num) / len(num):.2f}"
            top = df[col].value_counts().head(8)
            if len(set(vals)) <= 12 or len(num) < len(vals):
                line += " top_values=" + ", ".join(f"{k!r}×{n}" for k, n in top.items())
            non_num = [v for v in vals if not _is_num(v.strip())]
            if num and non_num and len(num) >= len(vals) * 0.5:
                line += f" non_numeric_examples={non_num[:4]!r}"
            parts.append(line)
        parts.append("head:\n" + df.head(4).to_csv(index=False).strip())
        parts.append("tail:\n" + df.tail(3).to_csv(index=False, header=False).strip())
    return "\n".join(parts)


_CRITIC_SYSTEM = """你是对抗式审查员。一个数据处理 agent 写了代码并打印了一个数,程序正常退出。
你的工作是找茬:先【假设这个结果是错的】,找出它最可能错在哪——数据里有什么会让这段代码静默算错的东西
(对照数据画像),或者代码算的并不是任务要的那个量。
然后核实这个错因是否成立:它必须同时满足 (a) 数据画像里确实有这个问题,(b) 代码确实没有正确处理它
(写了检查但没改变计算、或处理写了却不会生效,都算没处理)。
成立 → verdict="wrong";找不到任何成立的错因、且你能说明代码已覆盖画像里的可疑点 → verdict="ok";
拿不准(证据不足、代码逻辑你无法确定是否生效) → verdict="abstain"。不要硬判,允许弃权。
你看不到正确答案。只输出一个 JSON:
{"suspect": "一句话:最可能的错因", "evidence": "一句话:画像/代码里的依据", "verdict": "wrong|ok|abstain", "confidence": 0到1}"""


class Critic:
    def __init__(self, model: str, cache_path: Path):
        from openai import OpenAI

        env = dotenv_values(_ROOT / ".env")
        self.client = OpenAI(base_url=env["VISION_LLM_BASE_URL"], api_key=env["VISION_LLM_API_KEY"])
        self.model = model
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if cache_path.exists():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                self.cache[r["key"]] = r
        self.spent = sum(r.get("total_tokens", 0) for r in self.cache.values())

    def judge(self, key: str, request: str, profile: str, row: dict) -> dict:
        if key in self.cache:
            return self.cache[key]
        if self.spent > TOKEN_BUDGET:
            return {"key": key, "verdict": "abstain", "error": "token budget reached", "total_tokens": 0}
        msg = (f"## 任务\n{request}\n\n## 数据画像\n{profile}\n\n## 代码\n```python\n{row['code_tail']}\n```\n\n"
               f"## 程序输出(末尾)\n{row['stdout_tail']}")
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0,
                messages=[{"role": "system", "content": _CRITIC_SYSTEM}, {"role": "user", "content": msg}])
        except Exception as e:  # noqa: BLE001 — surfaced in the record, never cached
            return {"key": key, "verdict": "abstain", "error": f"{type(e).__name__}: {str(e)[:300]}",
                    "total_tokens": 0}
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            j = json.loads(m.group(0)) if m else {}
        except (json.JSONDecodeError, ValueError):
            j = {}
        verdict = j.get("verdict") if j.get("verdict") in ("wrong", "ok", "abstain") else "abstain"
        u = resp.usage
        out = {"key": key, "verdict": verdict, "suspect": j.get("suspect", ""), "evidence": j.get("evidence", ""),
               "confidence": j.get("confidence"), "parse_ok": bool(j),
               "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
               "total_tokens": u.total_tokens, "model": self.model,
               "rep": [row["run"], row["replicate"], row["arm"], row["case_id"]]}
        self.cache[key] = out
        self.spent += u.total_tokens
        with open(self.cache_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(out, ensure_ascii=False) + "\n")
        return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def load_rows() -> list[dict]:
    rows = []
    for run, path in RUNS.items():
        for line in (_ROOT / path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["run"] = run
                r["passed_logged"] = r["passed"]
                if (run, r["replicate"], r["arm"], r["case_id"]) in LABEL_FIXES:
                    r["passed"] = False
                rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="probe sanity on S4/S2/M1 + a handful of critic calls")
    ap.add_argument("--no-critic", action="store_true")
    ap.add_argument("--no-metamorphic", action="store_true")
    ap.add_argument("--critic-model", default=CRITIC_MODEL)
    ap.add_argument("--critic-scope", choices=("residual", "all"), default="residual",
                    help="'all' also judges keys L1 already failed (for an L2-alone row); ladder tokens "
                         "are still attributed on the residual only")
    ap.add_argument("--out", default="runs/verifier_ladder.jsonl")
    args = ap.parse_args()

    cases = case_index()
    rows = load_rows()
    root = Path(tempfile.mkdtemp(prefix="reforge_vladder_"))
    tables, signals, profiles = {}, {}, {}
    for cid, case in cases.items():
        case.build(str(root / "data" / cid))
        tables[cid] = load_tables(root / "data" / cid)
        signals[cid] = detect_signals(tables[cid])
        profiles[cid] = profile_text(tables[cid])

    print("== danger signals per case (data only) ==")
    for cid in sorted(cases):
        print(f"  {cid:30s} {sorted({s['signal'] for s in signals[cid]})}")

    if args.smoke:
        want = {"S4_transfer_score_mean": "sentinel", "S2_transfer_expense_sum": "footer",
                "M1_transfer_sales_east": "join_key_format"}
        ok = True
        for cid, sig in want.items():
            hit = sig in {s["signal"] for s in signals[cid]}
            ok &= hit
            print(f"  probe {sig:16s} on {cid}: {'FIRES' if hit else 'SILENT  <-- problem'}")
        print("\n" + profiles["S4_transfer_score_mean"])
        picks, seen = [], set()
        for r in rows:           # one wrong + one right per smoke case, deepseek run
            k = (r["case_id"], r["passed"])
            if r["run"] == "deepseek" and r["case_id"] in (*want, "M3_transfer_ticket_count",
                                                            "S5_transfer_distinct_products") and k not in seen:
                seen.add(k)
                picks.append(r)
        rows = picks
        args.out = "runs/verifier_ladder_smoke.jsonl"
        if not ok:
            sys.exit("smoke: a probe did not fire")

    out_path = _ROOT / args.out
    critic = None if args.no_critic else Critic(args.critic_model, out_path.with_suffix(".critic.jsonl"))

    l1 = [level1(signals[r["case_id"]], r["code_tail"]) for r in rows]

    l1b: list[dict] = [{} for _ in rows]
    if not args.no_metamorphic:
        memo: dict[tuple, dict] = {}
        todo = {}
        for i, r in enumerate(rows):
            todo.setdefault((r["case_id"], r["code_tail"], r["printed"]), i)

        def work(item):
            key, i = item
            return key, level1b(rows[i], tables[rows[i]["case_id"]], signals[rows[i]["case_id"]],
                                root / "meta" / str(i))
        with ThreadPoolExecutor(max_workers=6) as ex:
            for n, (key, res) in enumerate(ex.map(work, todo.items()), 1):
                memo[key] = res
                if n % 50 == 0:
                    print(f"  metamorphic {n}/{len(todo)}", flush=True)
        l1b = [memo[(r["case_id"], r["code_tail"], r["printed"])] for r in rows]

    with open(out_path, "w", encoding="utf-8") as fh:
        for r, a, b in zip(rows, l1, l1b):
            key = f"{r['run']}|{r['case_id']}|{r['printed']}" + ("|relabelled" if r["passed"] != r["passed_logged"] else "")
            rec = {"run": r["run"], "replicate": r["replicate"], "arm": r["arm"], "pair_id": r["pair_id"],
                   "case_id": r["case_id"], "printed": r["printed"], "gold": r["gold"], "wrong": not r["passed"],
                   "label_fixed": r["passed"] != r["passed_logged"],
                   "outcome": r["outcome"], "l0_fail": r["outcome"] == "FAILED",
                   "l1_signals": a["signals"], "l1_unhandled": a["unhandled"],
                   "l1_fail_raw": a["fail_raw"], "l1_fail": a["fail"],
                   "l1b_fail": b.get("fail"), "l1b_status": b.get("status"), "l1b_repaired": b.get("repaired"),
                   "critic_key": key, "critic_triggered": False, "critic": None}
            if critic and (not a["fail"] or args.critic_scope == "all"):
                j = critic.judge(key, cases[r["case_id"]].request, profiles[r["case_id"]], r)
                rec["critic"] = j
                rec["critic_triggered"] = not a["fail"]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if args.smoke:
                c = rec["critic"] or {}
                print(f"  {r['case_id']:30s} wrong={rec['wrong']!s:5} L1={a['fail']!s:5} unhandled={a['unhandled']} "
                      f"L1b={b.get('fail')}({b.get('status')}) critic={c.get('verdict')} "
                      f"tok={c.get('total_tokens')} {c.get('error', '')}\n      suspect: {c.get('suspect', '')}")
    if critic:
        print(f"critic calls cached={len(critic.cache)} cumulative tokens={critic.spent}")
    print(f"records={len(rows)} → {out_path}")
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
