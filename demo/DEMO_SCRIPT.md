# BrandMaestro AI — Demo Script

**Live URL:** https://brandmaestro.ddnsfree.com
**Demo account:** `harborline` / `SalvageDemo2026!`
**Pre-loaded brand:** Harbor Line Pictures, a fictional indie distributor, with 10 previous
best-performing documents for the film *SALVAGE* already ingested across four content types.

Target length: **under 3 minutes.** Timings below add to 2:50, leaving margin.
Anything marked *(cut first)* goes if you are running long.

---

## Before you record

Do these once. They are the difference between a demo that lands and one that stalls.

1. **Log in and leave the tab open.** First page load pulls fonts from a CDN.
2. **Demo the Press Release Model.** It is the verified path: on the topic below it comes
   back **approved at 9.0/10 in two enforcer iterations**, with 2.5 contractions per 100
   words against the corpus's 3.0 and a 13.8-word average sentence against 13.9. See
   "Known rough edges" before you consider demoing a different content type.
3. **Run one throwaway generation first.** The first generation for a content type builds
   the vector index; every one after that loads it. You want the judges watching the fast
   path, not the cold one.
4. **Have the Documents tab pre-loaded** so the 10 corpus documents are visible.
5. **Pick your live topic now** and paste it from a note rather than typing it.
   Use: `SALVAGE arrives on digital and disc on 14 April, with a commentary track by Ines
   Kowalczyk and Wren Okpara` — nothing in the corpus mentions a home release, so whatever
   appears on screen is genuinely new. This is the exact topic the 9.0 run used.
6. **Decide your fallback.** If a live generation stalls, cut to a pre-recorded capture.
   Say nothing about it; just keep talking.

---

## 0:00 — 0:20 · The problem

> "A film distributor releases six to nine titles a year. Every one needs a press release,
> social captions, trailer card copy and talent bios — and every one has a different voice.
> An arthouse thriller doesn't sound like a family animation.
>
> So the publicity team writes it all by hand, because a generic AI writing tool produces
> copy that could belong to anyone. The voice is the entire product, and that's exactly
> what generic tools throw away."

**On screen:** the sign-in page, or the Documents tab showing the 10 SALVAGE documents.

---

## 0:20 — 0:40 · What the system does

> "BrandMaestro AI takes the material a title has already published — the pieces that
> actually performed — and learns that specific voice. Then it writes new publicity in it.
>
> This account is Harbor Line Pictures. Ten documents: three press releases, three sets of
> social captions, two trailer copy sheets, two talent bios. That's all it's been given."

**On screen:** scroll the Documents table. Point at the Role column — every row says Voice.

*Optional beat, if a judge is likely to ask about hallucination:* documents can be uploaded
as **Reference** instead, which feeds retrieval with facts but is deliberately held out of
the voice profile.

---

## 0:40 — 1:10 · The architecture

> "It's a four-agent LangGraph pipeline, and each agent has one job.
>
> **Researcher** gathers the facts. It pulls the brand's own documents out of a pgvector
> store, and when web research is switched on it calls **Parallel's Search API** and filters
> the results down to publishable facts.
>
> **Writer** produces a draft against the Brand Brain — a synthesised voice profile for this
> title and this content type.
>
> **Enforcer** is the part I'd point at first. It checks the draft against the brand's
> measured mechanics and sends it back if it misses. Not a vibe check — counted properties.
>
> **Deployer** persists the approved piece and its score."

**On screen:** an architecture diagram, or the live console showing each node reporting in.

**The line worth landing:**

> "The Brand Brain is computed once and cached, not rebuilt per request. That's about 23% of
> the tokens a naive implementation would spend on every single generation."

---

## 1:10 — 1:40 · Why the Enforcer matters

> "Here's the part that makes it usable rather than a demo.
>
> The Enforcer's gates are deterministic. The brand's contraction rate, sentence length,
> punctuation habits and bracket conventions are **counted from the corpus**, not described
> to the model in prose. Harbor Line's press releases run 3 contractions per 100 words at
> about 14 words a sentence; their social copy runs 3.3 at 9.6 words. Those are measurements,
> and the draft has to match them within tolerance.
>
> On top of that it blocks things a language model does by default and a publicist can't ship:
> unfilled placeholders, invented press contacts, fabricated quotes, and passages copied too
> closely from the source material. Verbatim spans over eight words get flagged and rewritten."

**On screen:** the Enforcer's console output — the iteration count and score.

**What the verified run actually did**, and it is a better story than "it passed":
iteration 1 was rejected for reproducing 16 consecutive words of the festival's jury
citation outside quotation marks. The writer paraphrased that one passage. Iteration 2
approved at 9.0. That is the loop doing its job on a real violation, not a formality.

**If asked "how do you know it isn't just copying?"** — the extractive-copying gate measures
4-gram overlap against the source corpus. It came down from 23.5% to 3.2% when that gate went
in, and the number is checked on every generation, not sampled.

---

## 1:40 — 2:20 · Live generation

Paste the topic. Hit generate. Talk over it.

> "Nothing in those ten documents mentions a home release date, so this can't be retrieved —
> it has to be written.
>
> Watch the console. Researcher, writer, enforcer. If the enforcer rejects, it comes back
> with the specific passage that failed and the writer fixes that passage rather than
> starting over."

**When the output lands, read one line aloud and say what it tells you.** Something concrete —
a number, a short declarative sentence, the absence of hype. Then:

> "That's Harbor Line's voice. Short sentences. Concrete numbers. No exclamation marks
> anywhere, because there aren't any in their corpus either — nobody told the system that,
> it measured it."

---

## 2:20 — 2:40 · Human in the loop

> "The publicist scores it and can leave a note. That note isn't filed for later — it goes
> into the writer's prompt and the enforcer's checks on the very next generation, and the
> approved and rejected patterns accumulate in the title's memory."

**On screen:** submit a score and a short directive, e.g. `Lead with the commentary track,
not the date.` Then run once more if you have time *(cut first)*.

---

## 2:40 — 2:50 · Close

> "Four agents, one voice per title, deterministic gates so the output is publishable.
> Running on Google Cloud — Gemini through Vertex AI, Compute Engine, Cloud SQL-compatible
> Postgres with pgvector — and web research through Parallel.
>
> It's live at brandmaestro.ddnsfree.com. The account is in the description; you can break it
> yourself."

---

## Hackathon relevance — have these ready, don't script them

**Why Agentic Cinema.** The pipeline is built for the specific problem of film publicity:
one distributor, many titles, each with a voice that has to hold across press, social,
trailer copy and bios. The content types in the product are named for those artefacts.

**Why the Parallel track.** Parallel's Search API is the researcher's web-research path.
The two sources are deliberately not exclusive: a title's own production notes supply the
authoritative facts, Parallel supplies what's happened since, and the researcher filters
both down to publishable facts before the writer sees them. An earlier version made them
mutually exclusive and turning search on silently discarded the brand's own documents.

**Google Cloud.** Gemini 2.5 Flash via Vertex AI, on a Compute Engine VM with an attached
service account — Application Default Credentials from the metadata server, no key file
anywhere. Embeddings are Google's `text-embedding-004`. Caddy terminates TLS with an
automatic Let's Encrypt certificate.

**What you should say is honest, if asked about caching.** Vertex AI does not give implicit
prompt caching on this path, and the README says so. The saving that is real and measured is
the Brand Brain: synthesised once per title and content type, cached in Redis with a
Postgres fallback and stale-while-revalidate, which is roughly 5,561 tokens a run. Don't
claim prompt caching. The tradeoff is documented deliberately.

---

## Known rough edges — read this before you pick a content type

Verified on the live host today, same topic each time.

**Press Release Model (`blog`) — demo this one.** Approved at 9.0/10 in two enforcer
iterations. 2.5 contractions per 100 words against the corpus's 3.0, 13.8-word average
sentence against 13.9, no exclamation marks. Reads like the corpus: opens with the
announcement, quotes the director and the sound designer, closes with the credit block and
the standing boilerplate. Takes a few minutes and several heartbeats.

**Social Caption Model (`social`) — do not demo live.** It produces a single block of copy
rather than the platform-labelled caption sets the corpus is made of, and it reaches for
register the corpus never uses ("Experience SALVAGE. Hear the acclaimed sound design.").
On one run it finished unapproved at 5.0 after a specific collision: the Brand Brain had
inferred a signature construction from the corpus — announce a decision, then give the
audience-facing reason — the writer implemented it as "We decided to release SALVAGE on
digital and disc on 14 April…", and the internal-material gate then rejected "We decided"
as something the brand told itself rather than told its audience. The score went 7.9 → 5.0
because the enforcer's own instruction produced the violation. Both rules are individually
right; they disagree here. Not fixed.

**Trailer Copy Model (`ad`) and Talent Bio Model (`proposal`) — untested end to end.**
The corpora are loaded and the brains are synthesised, but no generation has been run
through either. Do not put them on camera without trying them first.

If you want a second content type on screen, run it a few times beforehand and use a
recording of a good one.

## Questions you should expect

**"Does it work for a brand that isn't this one?"**
Nothing about a brand is hardcoded — the voice profile, the mechanics thresholds and the
banned-punctuation set are all derived per business and content type. Two spots that had been
hardcoded to a specific brand were found and removed; a third, an exclamation-mark rule,
was a document-level gate that stripped every exclamation mark from a brand that used them
heavily, and now works by proximity to the cue instead.

**"What stops it inventing a quote from a real person?"**
A provenance gate. Attributions and contact details that don't appear in the source material
are rejected before the piece is ever persisted. That's also why the demo corpus carries no
media-contact email — the system is built not to fabricate them, so the corpus shouldn't
teach it to.

**"How long does a generation take?"**
Social is the fast path. Press releases run longer and can take several enforcer iterations,
which is why the stream sends a heartbeat with elapsed seconds. Be straight about this — the
press-release path is slower and scores slightly lower than social, and that's in the README.

**"What happens to a document I delete?"**
Its embeddings are removed and the voice profile is rebuilt from what's left, before the
request returns. There's also a per-content-type reset that clears the documents, the
embeddings, the metrics and the cached profile — so a title can be retrained from scratch
without touching any other title.
