"""Thresholds for the deterministic enforcement checks.

Collected in one place so the numbers that decide whether a draft ships are
visible together rather than scattered through the checks that apply them.
"""
import os


# A draft may reuse a brand's signature constructions (that is the product), but
# it must not reproduce its source material clause by clause. Measured baseline:
# two human-written documents from the same brand share at most a 4-word run,
# while an observed extractive draft lifted 15 consecutive words straight out of
# the product document it was given. 8 is comfortably clear of both the natural
# coincidence rate and the length of a typical signature phrase.
MAX_VERBATIM_SPAN_WORDS = 8

# A verbatim span needs at least this many words the writer actually chose
# before it counts as copying. Capitalised name components and function words
# do not count: an award title or festival name cannot be paraphrased, so a
# long span made almost entirely of them is a fact rather than lifted phrasing.
MIN_AUTHORED_SPAN_WORDS = 3


# Writer/Enforcer revision rounds. Tunable without a code change so the ceiling
# can be raised when a content type needs more passes — but note that extra
# rounds only help when the feedback is actionable: a loop that fails for the
# same reason three times usually fails for it five times too, at five times
# the latency. Must stay in step with graph.MAX_GRAPH_ITERATIONS, which reads
# the same variable.
MAX_ITERATIONS = int(os.getenv("MAX_REVISION_ITERATIONS", "3"))


# How far above a brand's measured rate a draft may go before it counts as
# amplifying the voice rather than matching it. Generous, because short pieces
# swing naturally: only a clear overshoot should fail.
MECHANICS_TOLERANCE = 1.8
# Rates below this are too small for a ratio to mean anything on a short draft.
MECHANICS_MIN_RATE = 0.5


# Below this corpus rate, the brand does not use bracketed slots at all, so any
# in a draft are unfilled template text rather than house style.
BRACKET_CONVENTION_MIN_RATE = 0.3


MIN_PHONE_DIGITS = 9


# A quoted passage this similar to a source quotation, without matching it, is
# an altered version of that quotation rather than unrelated phrasing. Set high
# enough that only a recognisable rewrite of a specific quote trips it.
QUOTE_ALTERATION_SIMILARITY = 0.72

# Shorter quoted fragments are terms of art rather than claims about what
# somebody said, so alterations below this length are not worth chasing.
MIN_QUOTED_PASSAGE_CHARS = 25

# With contractions expanded, a quoted passage this fully accounted for by a
# source quotation — in order — is that quotation restyled, not a different
# statement. High, because the whole point is that only the contractions and a
# stray connective have moved.
QUOTE_CANONICAL_COVERAGE = 0.92
