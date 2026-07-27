"""Tests for src/agenda_interpret.py — LLM interpretation behind a groundedness gate.

The gate's failure philosophy: reject wholesale (abstain), never repair. Any
number or legislation ref the model emits must be literally present in the
source text (title + provided source), or the whole output is dropped.
"""
import json

from src import config
from src.agenda_interpret import (
    build_interpret_prompt,
    interpret_item,
    ungrounded_tokens,
)
from src.agenda_parse import ParsedItem


def _item(title="Ordinance 2026-16 – To Amend an Ordinance Fixing Salaries",
          ref="Ordinance 2026-16"):
    return ParsedItem(position=6, item_number="6A",
                      section="Legislation for First Readings", section_number=6,
                      title_raw=title, legislation_ref=ref)


SOURCE = (
    "Ordinance 2026-16 – To Amend an Ordinance Fixing the Salaries of Officers "
    "and Employees of the Police and Fire Departments for 2027. Increases base "
    "pay by 4 percent effective January 1, 2027."
)


class FakeClient:
    """Minimal anthropic-shaped client: client.messages.create(**kw) ->
    object with .content[0].text, capturing kwargs for assertion."""

    class _Block:
        def __init__(self, text):
            self.text = text

    class _Response:
        def __init__(self, text):
            self.content = [FakeClient._Block(text)]

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.last_kwargs = kwargs
            return FakeClient._Response(self._outer._reply)

    def __init__(self, reply: str):
        self._reply = reply
        self.last_kwargs = None
        self.messages = FakeClient._Messages(self)


def _reply(summary, decision):
    return json.dumps({"summary_plain": summary, "decision_plain": decision})


# --- prompt builder -----------------------------------------------------


def test_prompt_contains_title_and_source():
    item = _item()
    prompt = build_interpret_prompt(item, SOURCE)
    assert item.title_raw in prompt
    assert item.section in prompt
    assert SOURCE in prompt


# --- grounded output kept ----------------------------------------------


def test_grounded_output_kept():
    reply = _reply(
        "This updates the pay schedule for Bloomington police officers and "
        "firefighters, raising base pay by 4 percent starting January 1, 2027.",
        "The council is deciding whether to approve Ordinance 2026-16.",
    )
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.rejected_reason is None
    assert "4 percent" in result.summary_plain
    assert "Ordinance 2026-16" in result.decision_plain


# --- ungrounded number rejected wholesale -------------------------------


def test_ungrounded_number_rejected():
    # Precondition for the test to be meaningful: '12' and '500' must be
    # genuinely absent as substrings of the source (incl. title).
    combined_source = _item().title_raw + "\n" + SOURCE
    assert "12" not in combined_source
    assert "500" not in combined_source

    reply = _reply(
        "This raises pay by 12 percent for 500 employees.",
        "The council is deciding whether to approve Ordinance 2026-16.",
    )
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.summary_plain is None
    assert result.decision_plain is None
    assert "ungrounded" in result.rejected_reason
    assert "12" in result.rejected_reason
    assert "500" in result.rejected_reason


def test_lowercase_invented_ref_rejected():
    # Case must not let a ref slip the gate: SOURCE has only Ordinance 2026-16,
    # so "resolution 2026-16" (any casing) is an invented ref.
    assert "resolution" not in (_item().title_raw + SOURCE).lower()
    reply = _reply(
        "This updates pay for police and fire employees.",
        "The council is deciding whether to approve resolution 2026-16.",
    )
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.summary_plain is None
    assert result.decision_plain is None
    assert "resolution 2026-16" in result.rejected_reason


def test_invented_legislation_ref_rejected():
    assert "2026-99" not in _item().title_raw + SOURCE
    reply = _reply(
        "This updates pay for police and fire employees.",
        "The council is deciding whether to approve Ordinance 2026-99.",
    )
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.summary_plain is None
    assert result.decision_plain is None
    assert "Ordinance 2026-99" in result.rejected_reason


# --- abstain on unusable replies ----------------------------------------


def test_malformed_json_abstains_with_reason():
    result = interpret_item(FakeClient('{"summary_plain": }'), _item(), SOURCE)
    assert result.summary_plain is None
    assert result.decision_plain is None
    assert result.rejected_reason == "malformed JSON"


def test_no_json_abstains_with_reason():
    result = interpret_item(FakeClient("I cannot help with that."), _item(), SOURCE)
    assert result.summary_plain is None
    assert result.rejected_reason == "no JSON in reply"


def test_non_string_field_values_abstain_not_raise():
    # A numeric field value must be treated as absent, not raised as TypeError.
    result = interpret_item(
        FakeClient('{"summary_plain": 5, "decision_plain": null}'), _item(), SOURCE
    )
    assert result.summary_plain is None
    assert result.decision_plain is None
    assert result.rejected_reason is not None


def test_empty_payload_abstains():
    result = interpret_item(FakeClient("{}"), _item(), SOURCE)
    assert result.summary_plain is None
    assert result.decision_plain is None
    assert result.rejected_reason == "empty payload"


# --- ungrounded_tokens helper directly ----------------------------------


def test_ungrounded_tokens_grounded_is_empty():
    generated = "Raises base pay by 4 percent effective January 1, 2027 under Ordinance 2026-16."
    assert ungrounded_tokens(generated, SOURCE) == []


def test_ungrounded_tokens_flags_invented_numbers():
    generated = "Raises pay by 12 percent for 500 employees."
    bad = ungrounded_tokens(generated, SOURCE)
    assert "12" in bad
    assert "500" in bad


# --- client wiring -------------------------------------------------------


def test_client_called_with_configured_model_and_system():
    client = FakeClient(_reply("Pay changes for 2027.", None))
    interpret_item(client, _item(), SOURCE)
    kwargs = client.last_kwargs
    assert kwargs["model"] == config.AGENDA_INTERPRET_MODEL
    assert kwargs["max_tokens"] == config.AGENDA_INTERPRET_MAX_TOKENS
    assert "plain" in kwargs["system"].lower()
    assert kwargs["messages"][0]["role"] == "user"


# --- decision_plain null passthrough -------------------------------------


def test_decision_plain_null_passes_through():
    reply = _reply("Pay changes for police and fire employees for 2027.", None)
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.rejected_reason is None
    assert result.summary_plain is not None
    assert result.decision_plain is None
