from __future__ import annotations
from html import escape
from .models import Status


def render_report(metrics, scope_label: str) -> str:
    m = metrics
    def pct(x): return "n/a" if x is None else f"{x:.2f}"
    lines = [f"# Evidence trust-core eval — {scope_label}", "",
             f"- items: {m.total}  (green {m.green} / flagged {m.flagged} / dropped {m.dropped})",
             f"- leads (chase-the-primary): {m.leads}",
             f"- verbatim pass rate: {pct(m.verbatim_pass_rate)}",
             f"- primary-source rate (of green): {pct(m.primary_source_rate)}",
             f"- precision (vs gold): {pct(m.precision)}",
             f"- recall (vs gold sample): {pct(m.recall)}", "",
             "## Per-domain green yield"]
    for d, n in sorted(m.per_domain_yield.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {d}: {n}")
    return "\n".join(lines) + "\n"


def _card(it) -> str:
    return (f'<div class="card {escape(it.status)}">'
            f'<div class="q">{escape(it.verbatim_text)}</div>'
            f'<div class="meta">{escape(it.issue)} · {escape(str(it.source_type))} · '
            f'{escape(", ".join(it.status_reasons))}</div>'
            f'<div class="ctx">{escape(it.context)}</div>'
            f'<a href="{escape(it.deep_link)}" target="_blank">source</a></div>')


def render_review_html(items, leads, scope_label: str) -> str:
    buckets = {Status.GREEN.value: [], Status.FLAGGED.value: [], Status.DROPPED.value: []}
    for it in items:
        buckets.setdefault(it.status, []).append(it)
    body = [f"<h1>Evidence review — {escape(scope_label)}</h1>"]
    for st in (Status.GREEN.value, Status.FLAGGED.value, Status.DROPPED.value):
        body.append(f"<h2>{st} ({len(buckets.get(st, []))})</h2>")
        body += [_card(it) for it in buckets.get(st, [])]
    body.append(f"<h2>Leads ({len(leads)})</h2>")
    for ld in leads:
        body.append(f'<div class="lead"><b>{escape(ld.issue)}</b> — '
                    f'{escape(ld.reported_text)}<br><i>{escape(ld.note)}</i><br>'
                    f'event: {escape(ld.event)} · '
                    f'<a href="{escape(ld.secondary_url)}" target="_blank">secondary</a></div>')
    style = ("<style>body{font:14px system-ui;margin:2rem;max-width:52rem}"
             ".card,.lead{border:1px solid #ddd;border-radius:8px;padding:.6rem;margin:.5rem 0}"
             ".green{border-left:5px solid #2e7d32}.flagged{border-left:5px solid #ed6c02}"
             ".dropped{border-left:5px solid #c62828;opacity:.7}.q{font-weight:600}"
             ".meta{color:#666;font-size:12px;margin:.3rem 0}.ctx{color:#333;font-size:13px}</style>")
    return f"<!doctype html><meta charset=utf-8>{style}" + "\n".join(body)
