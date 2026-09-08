# SALVAGE — demo corpus

Ten documents of "previous best-performing content" for **Harbor Line Pictures**, a
fictional independent film distributor, covering the release of a fictional film,
*SALVAGE*. This is the material the Brand Brain is built from.

Everything here is invented: the company, the film, the festival, the people. The
festival is deliberately fictional (Cascadia International Film Festival) rather than a
real one, so no document can be mistaken for a real organisation's record or read as a
real endorsement.

## Layout

| Directory        | `content_type` | UI label            | Documents |
|------------------|----------------|---------------------|-----------|
| `press-release/` | `blog`         | Press Release Model | 3 |
| `social/`        | `social`       | Social Caption Model| 3 |
| `trailer-copy/`  | `ad`           | Trailer Copy Model  | 2 |
| `talent-bios/`   | `proposal`     | Talent Bio Model    | 2 |

Load it with:

```sh
./demo/load_corpus.sh https://brandmaestro.ddnsfree.com <username> <password>
```

All ten upload with `doc_role=voice`, so every one feeds the voice profile. To
demonstrate the `reference` role — facts for retrieval, held out of voice extraction —
upload a document with `doc_role=reference` instead.

## The voice, and why it is shaped this way

The Brand Brain measures mechanics from the corpus rather than being told about them in
prose, so the corpus has to be internally consistent or there is nothing to learn. What
it should extract:

| Property | press-release | social | trailer-copy | talent-bios |
|---|---|---|---|---|
| words | 1306 | 1183 | 582 | 974 |
| contractions / 100 words | 3.0 | 3.3 | 3.4 | 3.4 |
| average sentence (words) | 13.9 | 9.6 | 12.7 | 16.8 |
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
  releases, unchanged. That is realistic, and it exercises the extractive-copying gate's
  exemption for language a brand reuses across its own documents.

Facts are consistent across all ten documents — the same runtime, dates, crew, tank
volume and awards — so a generation that contradicts one of them is a real failure rather
than an artefact of a sloppy corpus.
