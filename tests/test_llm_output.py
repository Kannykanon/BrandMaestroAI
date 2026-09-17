"""Reading the judge's answer when the judge quotes somebody.

A calibration run died on this, and the same parse sits in the enforcer:

    json.decoder.JSONDecodeError: Expecting ',' delimiter: line 52 column 48

The evaluator had been asked to quote the sentence it objected to. The sentence
was from a treatment — he understands the "no" isn't really an option — and its
quotation marks closed the JSON string three words early. A whole evaluation
was discarded over punctuation in somebody else's prose.

The prompt forbids this, which is not the same as preventing it. The enforcer's
answer was a second model call asking for the same evaluation again; this costs
nothing.
"""
import json

import pytest

from utils.llm_output import parse_llm_json

BROKEN = """{
  "voice_score": 7.0,
  "voice_rewrites": [
    {"from": "He understands the "no" isn't really an option.", "to": "No is not an option."}
  ],
  "feedback": "The line reading "this marks the beginning" is summary, not scene.",
  "score": 6.5
}"""


class TestQuotesInsideValues:
    def test_the_evaluation_survives_them(self):
        parsed = parse_llm_json(BROKEN)
        assert parsed["voice_score"] == 7.0 and parsed["score"] == 6.5

    def test_the_quoted_words_are_kept_exactly(self):
        """Stripping the quotes would change what the judge said the writer
        wrote, which is the one thing a rewrite has to get right."""
        parsed = parse_llm_json(BROKEN)
        assert parsed["voice_rewrites"][0]["from"] == 'He understands the "no" isn\'t really an option.'
        assert '"this marks the beginning"' in parsed["feedback"]

    def test_an_already_escaped_quote_is_left_alone(self):
        raw = '{"note": "she said \\"yes\\" twice"}'
        assert parse_llm_json(raw)["note"] == 'she said "yes" twice'

    def test_a_quote_before_a_comma_still_ends_its_value(self):
        raw = '{"a": "one", "b": "two"}'
        assert parse_llm_json(raw) == {"a": "one", "b": "two"}


class TestTheOrdinaryCases:
    def test_clean_json(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_markdown_fences(self):
        assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_commentary_around_the_object(self):
        raw = 'Here is my evaluation:\n{"score": 8.0}\nHope that helps.'
        assert parse_llm_json(raw) == {"score": 8.0}

    def test_fences_and_commentary_and_a_quote_at_once(self):
        raw = 'Sure:\n```json\n{"note": "she said "yes" twice"}\n```\nDone.'
        assert parse_llm_json(raw)["note"] == 'she said "yes" twice'


class TestWhenNothingCanBeSalvaged:
    def test_it_raises_so_the_caller_can_retry_or_fail_closed(self):
        """The enforcer asks the model to re-emit its evaluation, then fails
        closed. Returning an empty dict instead would read as an approval with
        no findings."""
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("not json at all")

    def test_a_bare_list_is_not_an_evaluation(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("[1, 2, 3]")
