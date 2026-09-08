# SALVAGE — demo corpus

Ten documents of "previous best-performing content" for **Harbor Line Pictures**, a
fictional independent film distributor, covering the release of a fictional film,
*SALVAGE*. This is the material the Brand Brain is built from.

Everything here is invented: the company, the film, the festival, the people. The
festival is deliberately fictional (Cascadia International Film Festival) rather than a
real one, so no document can be mistaken for a real organisation's record or read as a
real endorsement.

## Two channels, two roles

The documents feed two separate channels and never both.

| Directory | `doc_role` | Channel | Supplies |
|---|---|---|---|
| `press-release/`, `social/`, `trailer-copy/`, `talent-bios/` | `voice` | Brand Brain | how the brand writes |
| `reference/` | `reference` | RAG retrieval | the facts |

A **voice** document teaches the Brand Brain the rhythm, the mechanics and the
shapes this brand reaches for. It is never indexed for retrieval, so the writer
never sees its text. Give the system a director's scripts and ask for a script on
another subject: nothing in those scripts belongs in the output, only the way
they move.

A **reference** document is the fact channel, alongside Parallel's web search. It
is indexed for retrieval and held out of the Brand Brain — a fact sheet is
written in its own flat register, and letting it define the voice would pull
generated copy toward press-kit prose.

`reference/salvage-production-information.txt` carries everything the copy needs
to be true: runtime, dates, cast, crew, the tank, the training, the awards, the
jury citation, and the approved quotations. It also carries an INTERNAL NOTES
section — positioning, what not to disclose, talent availability — which is
realistic for a press kit and exercises the publishable-facts filter and the
enforcer's internal-material gate. Neither should ever reach the output.

The sheet is uploaded once per content type, because retrieval is scoped to
(`business_id`, `content_type`).

| Directory | `content_type` | UI label | Documents |
|------------------|----------------|---------------------|-----------|
| `press-release/` | `blog`         | Press Release Model | 3 |
| `social/`        | `social`       | Social Caption Model| 3 |
| `trailer-copy/`  | `ad`           | Trailer Copy Model  | 2 |
| `talent-bios/`   | `proposal`     | Talent Bio Model    | 2 |
| `reference/`     | all four       | —                   | 1 each |

Load it with:

```sh
./demo/load_corpus.sh https://brandmaestro.ddnsfree.com <username> <password>
```

## The voice, and why it is shaped this way

The Brand Brain measures mechanics from the corpus rather than being told about them in
prose, so the corpus has to be internally consistent or there is nothing to learn. What
it should extract:

| Property | press-release | social | trailer-copy | talent-bios |
|---|---|---|---|---|
| words | 1306 | 1183 | 280 | 974 |
| contractions / 100 words | 3.0 | 3.3 | 6.1 | 3.4 |
| average sentence (words) | 13.9 | 9.6 | 13.3 | 16.8 |
| exclamation marks | 0 | 0 | 0 | 0 |
| emoji | 0 | 0 | 0 | 0 |

Consistent contraction rate across all four types, with sentence length doing the work
of separating them. Deliberate choices worth knowing about:

- **No exclamation marks and no emoji anywhere.** A dry distributor voice, and a clean
  signal: the system should not add them, and nobody had to configure that.
- **Concrete numbers instead of adjectives.** Four hundred thousand litres, nineteen
  days, nine weeks, four seconds. The corpus almost never reaches for "stunning" or
  "breathtaking", so generated copy that does is visibly off-voice.
- **Contractions cluster in quotations**, which is how real press materials read. The
  narration is more formal than the people quoted in it.
- **No media-contact block.** Real press releases end with a contact name and email.
  These deliberately do not: the pipeline has a gate against fabricated contact details,
  and a corpus full of invented press emails would teach it to produce exactly what that
  gate exists to stop.
- **Repeated boilerplate.** The "About Harbor Line Pictures" block closes all three press
  releases, unchanged. A company's name and what it does are facts about the company, so
  that block is reproducible; every other kind of repeated phrasing is not.
- **Publishable copy only — no internal notes.** The trailer sheets originally carried
  SOUND NOTES, RESTRICTIONS and USAGE sections, which is what a real production sheet looks
  like and the wrong thing for a voice reference. Those sections are production logistics,
  and the pipeline has a gate that refuses to publish internal material — so their presence
  made the trailer-copy path unsatisfiable. The measured all-caps rate averaged the cards
  against those low-caps sections and demanded 24.1 per 100 words, the writer could only
  reach that by including them, and the internal-material gate then rejected them. Removing
  them took the rate to 45.7, which is what card-heavy copy actually measures.

  The lesson generalises: upload what the brand publishes. Internal notes in a reference
  teach a voice the system is separately forbidden to use.

Facts are consistent across all ten documents — the same runtime, dates, crew, tank
volume and awards — so a generation that contradicts one of them is a real failure rather
than an artefact of a sloppy corpus.
