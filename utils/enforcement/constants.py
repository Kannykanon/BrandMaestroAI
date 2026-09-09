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

# And a ratio is meaningless on a handful of events however high the rate.
# A brand at 0.84 nominalisations per 100 words predicts about 3 in a
# 400-word piece; finding 7 is 2x the rate and ordinary variance. One of
# the brand's own documents failed its own corpus that way, on six words
# that were not abstractions at all — audience, citation, competition,
# decompression, direction, sentence.
#
# So a failure needs the ratio AND an excess over the brand's predicted count
# that is too large to be variance. How large that is depends on the prediction,
# which is why this is only a floor: the real threshold scales with it (see
# MECHANICS_EXCESS_SIGMAS).
#
# A flat floor cannot do the job alone. At 5 it silently switched the register
# check off for short content: a 153-word trailer against a 0.85 corpus rate
# predicts 1.3 abstractions and contained 6 — 4.6x the brand's rate, plainly a
# voice failure — and the excess of 4.7 missed the floor by less than a single
# word. Trailers run about this length, so for that content type the check did
# not exist. The floor's remaining job is only to stop one stray occurrence in a
# very short draft from firing.
MECHANICS_MIN_EXCESS = 2

# The excess also has to clear this many standard deviations of the count the
# brand's own rate predicts. Occurrences of a habit are counts, so their
# variance goes with the square root of the prediction — meaning the bar rises
# for long drafts, where a few extra words prove nothing, and falls for short
# ones, where they are all the evidence there is.
#
# Two sigmas separates the two cases that matter. The brand's own document that
# failed its own corpus predicted 3.6 and had 7, an excess of 3.4 against a bar
# of 3.8 — correctly quiet. The trailer predicted 1.3 and had 6, an excess of
# 4.7 against a bar of 2.3 — correctly flagged.
MECHANICS_EXCESS_SIGMAS = 2.0


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
