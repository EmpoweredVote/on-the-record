"""Pure: findings list -> consolidated markdown. No I/O."""
from collections import Counter, defaultdict
from scripts.models import SEVERITIES

def render(findings, scope_label: str) -> str:
    out = [f"# Quote Audit — {scope_label}", ""]
    if not findings:
        out.append("No findings. ✅")
        return "\n".join(out)
    sev = Counter(f.severity for f in findings)
    out.append(f"**{len(findings)} findings** — "
               + ", ".join(f"{sev.get(s,0)} {s}" for s in SEVERITIES))
    out.append("")
    by_race = defaultdict(list)
    for f in findings:
        by_race[f.race_id or "(no race)"].append(f)
    out.append("## Summary by race")
    for race, fs in sorted(by_race.items(), key=lambda kv: -len(kv[1])):
        c = Counter(x.severity for x in fs)
        out.append(f"- **race {race}** — {len(fs)} findings ({c.get('high',0)} high, {c.get('medium',0)} med, {c.get('low',0)} low)")
    out.append("")
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for race, fs in sorted(by_race.items()):
        out.append(f"## race {race}")
        for f in sorted(fs, key=lambda x: (order[x.severity], x.fix_class, x.topic_key or "")):
            tgt = f.quote_id or f.topic_key or race
            out.append(f"- `{f.severity}` · `{f.fix_class}` · **{f.check_id}** ({f.level}) "
                       f"— {f.candidate or ''} / {f.topic_key or ''} [{tgt}]")
            out.append(f"    - {f.what}")
            out.append(f"    - fix: {f.suggested_fix}")
        out.append("")
    return "\n".join(out)
