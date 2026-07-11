"""audit-quotes CLI. Default: sweep all live quotes across all races.
Modes:
  (default)         resolve scope, run mechanical checks, write context bundles + mechanical report
Flags: --candidate NAME  --topic KEY  --ids id1,id2  --include-drafts  --out DIR  --scope-label LABEL
"""
import argparse, json, pathlib, datetime
from scripts.db import connect, fetch_rows, fetch_stance
from scripts.checks import run_mechanical
from scripts.verify_source import run_source_checks
from scripts.report import render

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate"); ap.add_argument("--topic"); ap.add_argument("--ids")
    ap.add_argument("--race", help="Scope to one race_id (uuid). Find race_ids in a default run's report.")
    ap.add_argument("--include-drafts", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--scope-label", default="all races")
    a = ap.parse_args()
    ids = [s.strip() for s in a.ids.split(",") if s.strip()] if a.ids else None

    conn = connect()
    rows = fetch_rows(conn, ids=ids, candidate=a.candidate, topic=a.topic, race=a.race, include_drafts=a.include_drafts)
    if not rows:
        print("No quotes matched scope."); return

    races = {r["race_id"] for r in rows}
    topics = {(r["race_id"], r["topic_key"]) for r in rows}
    print(f"SCOPE: {len(rows)} quotes | {len(races)} races | {len(topics)} race-topic groups | "
          f"drafts={'yes' if a.include_drafts else 'no'}")

    findings = run_mechanical(rows)
    findings += run_source_checks(conn, rows)
    print(f"MECHANICAL+SOURCE FINDINGS: {len(findings)}")

    # Default output dir resolves relative to this skill (cwd-independent), so it always lands
    # under audit-quotes/.runs/ (which .gitignore covers) no matter where the CLI is invoked.
    skill_root = pathlib.Path(__file__).resolve().parents[1]  # .../.claude/skills/audit-quotes
    run_dir = pathlib.Path(a.out) if a.out else skill_root / ".runs" / str(datetime.date.today())
    (run_dir / "context").mkdir(parents=True, exist_ok=True)
    by_race = {}
    for r in rows:
        by_race.setdefault(r["race_id"], []).append(r)
    stance_cache = {}
    for race, rrows in by_race.items():
        bundle = {"race_id": race, "topics": {}}
        for r in rrows:
            key = (r["candidate"], r["topic_key"])
            if key not in stance_cache:
                stance_cache[key] = fetch_stance(conn, r["candidate"], r["topic_key"])
            t = bundle["topics"].setdefault(r["topic_key"], {"topic_key": r["topic_key"], "quotes": []})
            t["quotes"].append({**r, "stance": stance_cache[key]})
        safe = str(race).replace("/", "_")
        (run_dir / "context" / f"{safe}.json").write_text(json.dumps(bundle, indent=2, default=str))

    (run_dir / "mechanical_findings.json").write_text(
        json.dumps([f.to_dict() for f in findings], indent=2, default=str))
    report_md = render(findings, scope_label=a.scope_label + " (mechanical only)")
    (run_dir / "mechanical_report.md").write_text(report_md)
    print(f"WROTE: {run_dir}/context/*.json, mechanical_findings.json, mechanical_report.md")
    print("NEXT: run the judgment pass (see SKILL.md), then merge findings and render the full report.")

if __name__ == "__main__":
    main()
