# Demo video script — 3:00 hard cap

Devpost requires ≤3 minutes, English, showing the product actually working.
Judges score Technological Implementation, Design, Potential Impact and
Quality of Idea equally, so the video has to show the partner integration
firing, not just claim it.

Record at 1920x1080. Use the hosted app, not localhost — a judge who sees
`localhost:5173` in the URL bar assumes nothing is deployed.

---

## 0:00–0:20 — The problem, in the domain

Say, over the Ninebark corpus on screen:

> "A studio publicity team ships press releases, trailer copy and talent bios
> for every title. Each one has to sound like the studio and stay factually
> correct about a film that is still changing. Miss the voice and it reads
> like a different company. Miss a fact and you correct it in public."

Do not explain the architecture yet. Name the audience — publicity and
marketing crews — because the brief asks for entertainment workflows.

## 0:20–0:50 — Brand Brain, built from the studio's own documents

Show the corpus already uploaded. Open the Brand Brain view.

> "BrandMaestro reads the studio's own published work and synthesises a Brand
> Brain: voice, register, and the mechanics it measures — sentence length,
> punctuation rate, the vocabulary it actually uses."

Point at one measured number on screen. A measured value is what separates
this from a prompt that says "write in our brand voice".

## 0:50–1:35 — Generate, with Parallel visibly on

This is the most important 45 seconds in the video.

1. Pick content type **press_release**, topic referencing something recent
   about the title.
2. **Show the Web research toggle is ON** — hover it, let it sit on screen.
3. Submit.
4. While it streams, say:

> "The researcher agent calls Parallel's Search API for what has happened
> since the studio's own documents were written, then filters both sources
> down to publishable facts before the writer sees them."

5. **Show a fact in the output that could only have come from the web** and
   say so explicitly. If you can, put the Parallel result and the generated
   sentence side by side.

If the integration is invisible here, the Parallel track has nothing to score.

## 1:35–2:10 — The enforcer loop

> "The enforcer scores the draft against the measured mechanics and sends it
> back to the writer with a specific instruction — not 'be more on brand',
> but 'this is a rate, here are the two ways to fix it'."

Show one revision cycle and the score moving. Then show the human-in-the-loop
approval step. This is the Design criterion: a complete product, not a demo.

## 2:10–2:40 — It is genuinely running

Fast cuts, roughly five seconds each:

- Gemini 2.5 Flash on Vertex AI, ADC from the attached service account
- The LangGraph pipeline deployed on Google Cloud Agent Platform Runtime
- Celery workers, Redis, Postgres with pgvector
- The Brand Brain cache: synthesised once, ~5,561 tokens saved per run

Say the caching claim exactly as the README states it. Do not claim implicit
prompt caching — Vertex does not give it on this path, and a judge who checks
will find you overstated one number and discount the rest.

## 2:40–3:00 — Close

> "Four agents — researcher, writer, enforcer, deployer — on Gemini and Google
> Cloud, with Parallel Search as the live research path. Brand voice that is
> measured against the studio's own documents, not described in a prompt."

End on the hosted URL, readable, held for three full seconds.

---

## Checks before you upload

- [ ] Under 3:00
- [ ] The Parallel toggle is visibly on, and a web-sourced fact is pointed at
- [ ] The URL bar shows the hosted app throughout
- [ ] Public on YouTube or Vimeo — unlisted is fine, private is not
- [ ] No API keys, tokens or `.env` contents visible in any frame
- [ ] Audio is intelligible; judges will not rewind
