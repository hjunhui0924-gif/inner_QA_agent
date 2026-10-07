"""Run existing UI contracts across browser engines, with isolated evidence.

Start Vite on 5173 first. These are API-fixture tests, not real model or phone
acceptance. Browser installation: python -m playwright install firefox webkit.
"""
import argparse
import csv
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright
import e2e_product_experience as product
import e2e_workspace_design as workspace
import e2e_paper_citations as paper
import e2e_experience_edge_cases as edges


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--browsers", nargs="+", default=["chrome", "msedge", "firefox", "webkit"])
    parser.add_argument("--scenarios", nargs="+", help="Run only scenario names containing one of these strings")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    with sync_playwright() as p:
        for name in args.browsers:
            out = args.output_dir / name
            out.mkdir()
            (out / "work/paper-citations").mkdir(parents=True)
            product.OUT = edges.OUT = out
            workspace.ROOT = paper.ROOT = out
            try:
                browser = p.chromium.launch(channel=name) if name in {"chrome", "msedge"} else getattr(p, name).launch()
            except Exception as error:
                rows.append(dict(browser=name, scenario="launch", status="unavailable", error=str(error)))
                continue
            scenarios = [
                ("home-7-viewports-2-motions", product.check_home),
                ("task-state-ime", product.check_task_state),
                ("upload-100-records-pagination", product.check_upload_and_large_list),
                ("history-scroll", product.check_history_scroll),
                ("workspace-6-viewports-drawers", workspace.check_viewports),
                ("answer-copy-scroll-low-height", workspace.check_answer_interactions),
                ("paper-desktop", lambda b: paper.check(b, 1440, 900)),
                ("paper-mobile-viewport", lambda b: paper.check(b, 390, 844)),
            ]
            for motion in ["reduce", "no-preference"]:
                scenarios.append((f"touch-emulation-{motion}", lambda b, m=motion: edges.check_touch(b, m)))
                for width in [390, 1440]:
                    scenarios.append((f"failure-boundaries-{width}-{motion}", lambda b, w=width, m=motion: edges.check_failures(b, w, m)))
            try:
                for scenario, run in scenarios:
                    if args.scenarios and not any(part in scenario for part in args.scenarios):
                        continue
                    started = time.monotonic()
                    row = dict(browser=name, version=browser.version, scenario=scenario, status="passed", evidence=str(out))
                    try:
                        detail = run(browser)
                        if detail is not None:
                            row["diagnostics"] = detail
                    except Exception as error:
                        row.update(status="failed", error=str(error))
                    row["elapsed_seconds"] = round(time.monotonic() - started, 2)
                    rows.append(row)
                    (args.output_dir / "matrix.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(name, scenario, row["status"], flush=True)
            finally:
                browser.close()
    (args.output_dir / "matrix.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output_dir / "matrix.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["browser", "version", "scenario", "status", "elapsed_seconds", "evidence"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return int(any(row["status"] != "passed" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
