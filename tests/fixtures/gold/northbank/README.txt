NORTHBANK — an ad pair, half built.

WHY IT EXISTS

Every threshold in this system was tuned against one pair: one brand, one
content type, one piece of narrative. That is how a rule shipped that would
have refused the brand's own hand-written script, and it is why a change to a
gate is currently scored against storytelling alone.

Ad copy exercises the parts narrative never touches — the claims list, the
8-word copying threshold instead of the script's 14, the facts gate against a
document that is mostly figures — and it exercises the carve-outs in the other
direction: story coverage and the told-not-shown check must stay off, because
an ad uses a fraction of its source on purpose.

It has already earned its place. The first pass over brief.txt found that
"about 8 minutes" was being extracted as the fact "about 8 m", with "inutes"
left over: the unit that means millions was matching the first letter of the
following word. Every duration, distance and weight in every product document
was extracted wrong, and no narrative fixture contains one.

WHAT IS HERE

    type.txt               ad — this is what switches the gates
    brief.txt              the product document, written to exercise the gates
    rejected/*.txt         drafts that must be refused, and the gate that must
                           catch each one

Both rejected drafts are caught today, by exactly the gate they name and
nothing else.

WHAT IS MISSING, AND WHY IT IS NOT WRITTEN HERE

    gold.txt               the ad you would sign off
    voice/*.txt            the writing the brand's voice is measured from

These two are the pair. Everything else is scaffolding.

A gold file is worth exactly what the judgement behind it is worth. Its whole
job is to be writing somebody outside this system has already called good, so
that a future change to a gate has something to fail against that is not this
system's own taste. Written here, it would pass by construction and bless every
change made after it — which is the failure the harness exists to catch, rebuilt
one level up.

The same goes for the corpus. In the kancity pair the voice documents are four
published screenplays and the gold is hand-written, and the distance between
them is what gives that fixture its bite: the gold sits at a sentence-length
variation of 2.65 against a corpus at 3.39, and a rule that pushed drafts toward
the corpus would have pushed the correct answer away. A corpus and a gold from
the same hand cannot show that.

WHAT HAPPENS UNTIL THEY ARRIVE

The rejected drafts run — that half is mechanical. The test that the right
answer passes every gate skips, and says so by name. The two gates that need a
measured corpus (mechanics, preflight) find nothing, because there is nothing
to measure against yet.

TO FINISH IT

Drop in three or four pieces of ad copy in the register you want, as voice/*.txt
— they do not have to be yours, only right. Write or paste the ad this brief
should have produced as gold.txt. Then run:

    python -m pytest tests/test_gold_regression.py -q

If the gold is refused, that is the harness working: either the gold is wrong or
a gate is, and finding out which is the whole point.

If you would rather the pair be about a product you know, brief.txt is one file
and the rejected drafts are two more. The figures in brief.txt are invented and
the brand does not exist.
