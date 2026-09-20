from src.evidence.eval import Metrics
from src.evidence.models import EvidenceItem, GateResults, SourceType, Status, Lead
from src.evidence.report import render_report, render_review_html

def test_render_report_has_headline_metrics():
    m = Metrics(total=3, green=1, flagged=1, dropped=1, leads=2,
                verbatim_pass_rate=0.66, primary_source_rate=1.0,
                per_domain_yield={"laist.com": 1}, precision=1.0, recall=0.5)
    md = render_report(m, "LA Mayor")
    assert "LA Mayor" in md and "green" in md.lower()
    assert "0.5" in md and "precision" in md.lower()

def test_render_review_html_escapes_and_groups():
    it = EvidenceItem(politician_id="p1", issue="housing", evidence_type="quote",
        verbatim_text="We will <build> 30k", source_url="https://a.com",
        cited_via=None, context="…", deep_link="https://a.com",
        source_type=SourceType.PRIMARY, gates=GateResults(verbatim=True),
        status=Status.GREEN.value, status_reasons=[], provenance={})
    html = render_review_html([it], [Lead("p1","reported","crime","debate",
        "https://n.com", None)], "LA Mayor")
    assert "&lt;build&gt;" in html          # escaped
    assert "green" in html.lower() and "leads" in html.lower()
