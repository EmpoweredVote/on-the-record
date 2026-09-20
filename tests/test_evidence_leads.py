from src.evidence.models import QuoteCandidate
from src.evidence.leads import to_lead

def test_to_lead_carries_reported_text_and_marker():
    cand = QuoteCandidate(text="I will end street homelessness.", context="…",
                          issue="homelessness", date="2026-05-01",
                          is_own_words=True, is_primary_venue=False,
                          reported_event="LAist debate, 2026-05-01",
                          primary_handle="https://youtu.be/xyz")
    lead = to_lead(cand, politician_id="p1", secondary_url="https://laist.com/story")
    assert lead.reported_text == "I will end street homelessness."
    assert lead.event == "LAist debate, 2026-05-01"
    assert lead.primary_handle == "https://youtu.be/xyz"
    assert "NOT verified against a primary" in lead.note
