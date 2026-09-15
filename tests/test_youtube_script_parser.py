"""The script parser must never change an approved word.

Shots are slices of the script, classified by shape. These tests cover the
formats marketing scripts arrive in, and that the word check catches any
change: added, dropped, reordered or reworded.
"""
import pytest

from youtube.script_parser import (
    DIRECTION,
    NARRATOR,
    SPOKEN,
    ScriptError,
    Shot,
    build_shots,
    parse_script,
    plan_shots,
    verify_words,
    words,
)


def spoken(script):
    return [(s.speaker, s.spoken) for s in parse_script(script) if s.kind == SPOKEN]


class TestSpeakers:
    def test_uppercase_label(self):
        assert spoken("MAYA: We did it.") == [("MAYA", "We did it.")]

    def test_title_case_name(self):
        assert spoken("Maya: We did it.") == [("MAYA", "We did it.")]

    def test_bold_markdown_label(self):
        assert spoken("**LEO:** Did we?") == [("LEO", "Did we?")]

    @pytest.mark.parametrize("label", ["VO", "V.O.", "Narrator", "NARRATOR", "Voiceover"])
    def test_narrator_labels(self, label):
        assert spoken(f"{label}: Once upon a time.") == [(NARRATOR, "Once upon a time.")]

    @pytest.mark.parametrize("label", ["HOOK", "CTA", "Intro", "Call to action"])
    def test_section_labels_are_narration(self, label):
        [segment] = [s for s in parse_script(f"{label}: Visit today.") if s.kind == SPOKEN]
        assert (segment.speaker, segment.spoken, segment.section) == (NARRATOR, "Visit today.", label.upper())

    def test_parenthetical_after_name_is_delivery(self):
        [segment] = parse_script("MAYA (whispering): We did it.")
        assert (segment.speaker, segment.spoken, segment.delivery) == ("MAYA", "We did it.", "whispering")

    def test_inline_notes_are_not_spoken(self):
        [segment] = parse_script("LEO: Did we? (beat) Look [points] outside.")
        assert segment.spoken == "Did we? Look outside."
        assert segment.delivery == "beat; points"

    def test_screenplay_cue_on_its_own_line(self):
        script = "MAYA\nIt's a queue.\nAround the block!\n\nThe night went on."
        assert spoken(script) == [("MAYA", "It's a queue."), ("MAYA", "Around the block!"),
                                  (NARRATOR, "The night went on.")]

    def test_cue_with_extension(self):
        [segment] = [s for s in parse_script("MAYA (V.O.)\nWe did it.") if s.kind == SPOKEN]
        assert (segment.speaker, segment.delivery) == ("MAYA", "V.O.")

    def test_empty_label_line_hands_following_lines_to_the_speaker(self):
        assert spoken("LEO:\nLook outside.") == [("LEO", "Look outside.")]


class TestProseIsNotMistakenForDialogue:
    @pytest.mark.parametrize("line", [
        "Here's the truth: nobody came.",
        "The result was simple: sales doubled in a week.",
        "At 9:30 the doors opened.",
    ])
    def test_colon_in_prose(self, line):
        assert spoken(line) == [(NARRATOR, line)]


class TestDirections:
    @pytest.mark.parametrize("line", [
        "# The Launch", "**Scene 1**", "---", "INT. SHOP - NIGHT", "EXT. STREET – DAY",
        "[Cut to the street]", "(beat)", "CUT TO:", "VISUAL: crowd shot", "SFX: door creaks",
        "On screen: logo",
    ])
    def test_never_spoken(self, line):
        [segment] = parse_script(line)
        assert segment.kind == DIRECTION and segment.spoken == ""


class TestShots:
    def test_narration_splits_at_sentence_ends(self):
        text = ("Maya had worked on the launch for eleven months. Tonight, the doors opened. "
                "The first customers walked in, and nobody said a word. Then someone laughed.")
        _, shots = plan_shots(text)
        assert [s.text for s in shots] == [
            "Maya had worked on the launch for eleven months. Tonight, the doors opened. "
            "The first customers walked in, and nobody said a word.",
            "Then someone laughed.",
        ]

    def test_a_long_sentence_stays_whole(self):
        sentence = " ".join(["word"] * 40) + "."
        _, shots = plan_shots(sentence)
        assert [s.text for s in shots] == [sentence]

    def test_closing_quotes_stay_with_their_sentence(self):
        _, shots = plan_shots('She said "stop." Then she left.', max_narration_words=3)
        assert [s.text for s in shots] == ['She said "stop."', "Then she left."]

    def test_consecutive_lines_from_one_speaker_are_one_shot(self):
        _, shots = plan_shots("MAYA\nIt's a queue.\nAround the block!")
        assert [(s.speaker, s.text) for s in shots] == [("MAYA", "It's a queue. Around the block!")]

    def test_a_direction_between_lines_keeps_them_apart(self):
        _, shots = plan_shots("MAYA: It's a queue.\n[She points]\nMAYA: Around the block!")
        assert len(shots) == 2
        assert shots[1].context == "[She points]"

    def test_directions_become_context_for_the_next_shot(self):
        _, shots = plan_shots("INT. SHOP - NIGHT\nThe doors opened.")
        assert shots[0].context == "INT. SHOP - NIGHT"

    def test_positions_are_sequential(self):
        _, shots = plan_shots("A. B.\n\nMAYA: C.\n\nLEO: D.")
        assert [s.position for s in shots] == list(range(1, len(shots) + 1))


FULL_SCRIPT = """# The Launch

HOOK: What if the doors never opened?

INT. SHOP - NIGHT

Maya had worked on the launch for eleven months. Tonight, the doors opened. The first customers walked in, and nobody said a word. Then someone laughed.

MAYA (whispering): We did it.
**LEO:** Did we? (beat) Look outside.

[Cut to the street]

MAYA
It's a queue.
Around the block!

VISUAL: crowd shot
CTA: Visit today and see for yourself.
"""


class TestWordCheck:
    def test_full_script_passes(self):
        segments, shots = plan_shots(FULL_SCRIPT)
        spoken_words = [w for s in segments if s.kind == SPOKEN for w in words(s.spoken)]
        assert [w for shot in shots for w in words(shot.text)] == spoken_words

    def test_every_spoken_word_comes_from_the_script_in_order(self):
        _, shots = plan_shots(FULL_SCRIPT)
        script_words = iter(words(FULL_SCRIPT))
        for word in (w for shot in shots for w in words(shot.text)):
            assert any(word == s for s in script_words), word

    @pytest.mark.parametrize("tamper", [
        lambda shots: setattr(shots[1], "text", shots[1].text.replace("eleven", "twelve")),
        lambda shots: setattr(shots[1], "text", shots[1].text + " Obviously."),
        lambda shots: setattr(shots[1], "text", " ".join(shots[1].text.split()[:-1])),
        lambda shots: shots.reverse(),
        lambda shots: shots.pop(),
    ])
    def test_any_change_is_caught(self, tamper):
        segments = parse_script(FULL_SCRIPT)
        shots = build_shots(segments)
        verify_words(FULL_SCRIPT, segments, shots)  # untouched: passes
        tamper(shots)
        with pytest.raises(ScriptError):
            verify_words(FULL_SCRIPT, segments, shots)

    def test_a_dropped_line_is_caught(self):
        segments = parse_script(FULL_SCRIPT)
        shots = build_shots(segments)
        del segments[3]
        with pytest.raises(ScriptError):
            verify_words(FULL_SCRIPT, segments, shots)

    def test_spoken_text_that_is_not_in_its_line_is_caught(self):
        segments = parse_script("MAYA: We did it.")
        segments[0].spoken = "We really did it."
        with pytest.raises(ScriptError):
            verify_words("MAYA: We did it.", segments, [Shot(1, "MAYA", "We really did it.")])

    def test_emphasis_markers_and_spacing_do_not_count_as_changes(self):
        _, shots = plan_shots("The doors  **finally**   opened.")
        assert shots[0].text == "The doors finally opened."

    @pytest.mark.parametrize("script", ["", "   \n\n", "# Title only\n[Cut to black]"])
    def test_nothing_to_read_is_an_error(self, script):
        with pytest.raises(ScriptError):
            plan_shots(script)

    def test_windows_line_endings(self):
        _, shots = plan_shots("MAYA: Hi.\r\nLEO: Hello.")
        assert [(s.speaker, s.text) for s in shots] == [("MAYA", "Hi."), ("LEO", "Hello.")]


def test_ad_copy_labels_are_read_as_narration_not_speakers():
    from youtube.script_parser import plan_shots

    ad = ("**Headline:** Fresh sourdough, baked at dawn\n"
          "**Body:** Crumb & Co bakes every loaf by hand.\n"
          "**CTA:** Order yours today")
    _, shots = plan_shots(ad)
    assert {s.speaker for s in shots} == {"NARRATOR"}
    assert " ".join(s.text for s in shots) == "Fresh sourdough, baked at dawn Crumb & Co bakes every loaf by hand. Order yours today"
