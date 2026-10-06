"""Run the planner on every sample meeting and score it. Uses your free API quota (about 3k tokens per meeting).

    python -m evals.run_evals                         # all 4 samples, 1 trial each
    python -m evals.run_evals --trials 3              # agents are non-deterministic: measure reliability
    python -m evals.run_evals --only sprint_sync
    set BOARDBOT_MODEL=openai/gpt-oss-20b & python -m evals.run_evals     # compare a smaller, faster model

Each case: fresh database -> planner makes a plan -> grade it against known answers -> execute the plan against the real MCP
server and make sure nothing fails. Results are saved to evals/results/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from datetime import date
from pathlib import Path

from app import db
from app.config import ROOT, Settings, default_mode
from app.executor import execute_plan
from app.llm import LLMUnavailable, make_llm
from app.planner import Planner
from app.tools import MCPToolbox
from evals.grader import grade

SAMPLES = ROOT / "samples"
MEETING_DATE = date(2026, 10, 6)  # the sample meetings all take place on this Tuesday


async def run_case(name: str, case: dict, llm) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BOARDBOT_DB"] = str(Path(tmp) / "eval.db")  # the MCP subprocess inherits this
        db.seed()
        started = time.time()
        async with MCPToolbox() as toolbox:
            planner = Planner(llm, toolbox, mode=default_mode())
            result = await planner.create_plan((SAMPLES / f"{name}.txt").read_text(encoding="utf-8"), case["meeting_title"], MEETING_DATE)
            failures = grade(result.plan, case["expect"])
            exec_failed: list[str] = []
            if result.plan:
                outcomes = await execute_plan(toolbox, result.plan)
                exec_failed = [f"{o['kind']} {o['title']!r}: {o['detail'][:100]}" for o in outcomes if o["status"] == "failed"]
        failures += [f"plan could not be executed: {f}" for f in exec_failed]
        return {
            "case": name,
            "passed": not failures,
            "failures": failures,
            "unresolved_warnings": [w for w in (result.plan.warnings if result.plan else []) if w.startswith("Unresolved")],
            "usage": result.usage,
            "seconds": round(time.time() - started, 1),
            "plan": result.plan.model_dump() if result.plan else None,
        }


async def main_async(args: argparse.Namespace) -> int:
    s = Settings.from_env()
    llm = make_llm(s)
    cases = json.loads((SAMPLES / "cases.json").read_text(encoding="utf-8"))
    names = [n for n in cases if not args.only or n in args.only]
    rows = []
    for name in names:
        for trial in range(args.trials):
            try:
                row = await run_case(name, cases[name], llm)
            except LLMUnavailable as e:  # e.g. the free tier's rate limit: keep what was measured so far
                print(f"[STOPPED] {name} trial {trial + 1}: {e}")
                break
            row["trial"] = trial
            rows.append(row)
            print(f"[{'PASS' if row['passed'] else 'FAIL'}] {name} trial {trial + 1}/{args.trials} ({row['seconds']}s)")
            for f in row["failures"]:
                print(f"       - {f}")

    passed = sum(r["passed"] for r in rows)
    print(f"\n{passed}/{len(rows)} passed with {s.provider}/{s.model}")
    for name in names:
        mine = [r for r in rows if r["case"] == name]
        print(f"  {name:<18} {sum(r['passed'] for r in mine)}/{len(mine)}")

    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{s.model.replace(':', '_').replace('/', '_')}.json"
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    print(f"details saved to {out}")
    return 0 if passed == len(rows) else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--only", nargs="*")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
