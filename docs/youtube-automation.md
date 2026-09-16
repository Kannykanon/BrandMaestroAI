# YouTube Automation — Design

**Status:** Agreed 2026-09-14. Phases 0–4 built and running in production. Run live: Vertex (planner), Kokoro (voice), Nano Banana 2 (images), InfiniteTalk (talking characters), the YouTube connection and a private upload. Not run live: Kling, Seedream, ElevenLabs sound. Later additions are listed under §14.
**Scope:** Turn approved marketing scripts into storytelling videos with a cast of AI characters, and publish them to one YouTube channel.

---

## 1. Summary

BrandMaestro already researches, writes and fact-checks scripts in a brand's voice. YouTube Automation is a separate, optional module that picks up an approved `script`, casts characters to its lines, and produces a finished video: AI-generated scene images for narration, lip-synced talking characters for dialogue, voices, captions and music. The video is uploaded to YouTube as **private**, and a person publishes it with one click.

The main cost decision: **only dialogue shots are animated.** Narration plays over still scene images with slow zoom and pan. This brings a 10-minute video from an estimated $34–90 of avatar rendering down to roughly **$5–9 in total**.

---

## 2. Goals and non-goals

**Goals**
- Produce long-form (16:9) and Shorts (9:16) videos from approved marketing scripts.
- Support many characters: each story casts its own faces and voices.
- Keep every approved word exactly as approved.
- Keep cost per video predictable, with a budget shown before the expensive steps run.
- Make every external service switchable through ports and adapters, the same design as `model.py` and `embedding_stategy.py`.
- Keep a human decision between rendering and publishing.

**Non-goals (for now)**
- Serving other people's channels. v1 publishes to **one channel: the owner's**.
- Writing or editing scripts inside YouTube Automation. Script changes happen in marketing, where the enforcer checks them.
- Fully animated videos, where every second is avatar-rendered.
- Running video models on local hardware (see §12).

---

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Separate, optional module. Marketing does not import it or depend on it. | Marketing users must be unaffected, whether or not YouTube Automation is set up. |
| D2 | Scripts come **only from marketing**, or are **imported by a person** and labelled as such. No second research, writing or enforcer loop. | The marketing pipeline already does the expensive, careful work; an imported script is the user's own responsibility and says so. |
| D3 | Eligible scripts: **human-approved**, or **enforcer-approved** and not rejected by a human. Each shows which kind it is. | Both are useful; the user should know whether a person has read it. |
| D4 | Scripts saved before the enforcer's verdict was stored are eligible **only if human-approved**. | There is no trustworthy automatic verdict for them. |
| D5 | Approved wording is never changed. The scene planner only structures it, and the result is checked word for word. | The enforcer approved those exact words. |
| D6 | A video project stores a **snapshot** of the script when it is created. | Later marketing edits or deletions must not change a video in progress. |
| D7 | Publish to the owner's single channel first. | Much simpler Google sign-in and API audit. |
| D8 | Both formats: long-form 16:9 and Shorts 9:16. | Chosen at project creation. |
| D9 | Storytelling with a **character library**: many uploaded faces, each with its own voice. | A story needs a cast, not one presenter. |
| D10 | **Animate only speaking shots.** Narration uses still images. Long lines cut away after a few seconds. | Main cost control. |
| D11 | Narration images are **AI-generated per scene, using the uploaded faces as references**. | Flexible and cheap; consistency comes from character sheets and a fixed style. |
| D12 | Every external service sits behind a port with swappable adapters. | Providers and prices change often. |
| D13 | Upload as **private**, review, then publish on click. Always set the synthetic-content disclosure. | Protects the brand, and matches YouTube's policy on AI content. |
| D14 | Hosted APIs for image and avatar generation at first. Self-hosted GPU adapters can be added later. | The development laptop has no GPU, and the production VM has no spare capacity (§12). |

---

## 4. Where it sits

```
MARKETING (unchanged)                          YOUTUBE AUTOMATION (new, optional)
─────────────────────                          ──────────────────────────────────
Researcher → Writer → Enforcer → Deployer
                                    │
                         approved `script` generation
                                    │  one-way, read only
                                    ▼
                          ┌─ Import (snapshot) ─────────────────────────┐
                          │  Scene planner  → shot list (words unchanged)│
                          │  Casting        → speaker → character        │
                          │  Voice          → audio per line             │
                          │  Scene images   → storyboard                 │
                          │  ── STORYBOARD REVIEW (human) ──             │
                          │  Avatar         → dialogue shots only        │
                          │  Captions       → word timings               │
                          │  Composer       → MP4 16:9 or 9:16           │
                          │  ── VIDEO REVIEW (human) ──                  │
                          │  Publisher      → private upload → publish   │
                          └──────────────────────────────────────────────┘
```

**Rules that keep the two sides apart**
- YouTube code lives in its own package (`youtube/`), with routes under `/youtube/...`, its own tables, and its own Celery queues and workers.
- It **reuses** shared services by calling them: `LLMSingleton`, the Brand Brain, auth and the database session. Marketing modules never import from `youtube/`.
- **The only marketing change:** save the enforcer's real verdict (§5.1).
- YouTube credentials are **not** part of the API's startup check. If they are missing, the app starts normally and only the YouTube section reports that it is not configured.
- Marketing tests stay untouched. YouTube Automation has its own test suite.

---

## 5. Script intake

**Sources (as built):** approved `script` generations, approved `ad` generations (read as narration; Headline, Body, CTA and similar labels are section labels, not speakers), and scripts a person imports. Importing is approval: the script is stored in `yt_imported_scripts`, labelled `imported`, and checked in advance to split into shots. Marketing tables are never written to. Approving a Script or an Ad in the Content Generator shows a **Make a video** button that creates the project and starts planning; it waits for the feedback worker to save the approval first.

### 5.1 Required marketing change: save the enforcer's verdict

Today the `generations` table has `status`, `score` and `content`, but not the enforcer's `approved` decision.
- `status = completed` does not mean approved: when revisions run out, the deployer saves the last draft anyway.
- `reviewer_learning.agent_auto_approved` is `score >= 8.0` (set in `learning_memory.py`), not the enforcer's verdict. The enforcer also refuses invented claims, whatever the score.

**Change:** add a nullable `approved` boolean to `generations`, written by the deployer from `state["approved"]`. Additive only, with no change to marketing behaviour. Rows saved before the change keep `approved = NULL`.

### 5.2 Eligibility and labels

Source tables: `generations` (content_type `script`, status `completed`, `approved`) joined to `reviewer_learning` (`human_approved`) by `generation_id`.

| `human_approved` | `generations.approved` | Result |
|---|---|---|
| `true` | any | ✅ **Human-approved** |
| `false` | any | Hidden |
| `NULL` (not reviewed) | `true` | 🤖 **Enforcer-approved, not reviewed by a person** |
| `NULL` | `false` | Hidden |
| `NULL` | `NULL` (legacy row) | Hidden |

### 5.3 Snapshot

Creating a video project copies the script text, topic, `generation_id`, approval label and approval timestamp into `yt_projects`. The project never reads the marketing row again. Changing the wording means regenerating in marketing and creating a new project.

---

## 6. Character library

A **character** is reusable across stories.

| Field | Notes |
|---|---|
| name | e.g. "Maya" |
| face images | 1–5 uploads |
| character sheet | Generated from the uploads (front, three-quarter view, full body, outfit) and approved by the user. Used as the reference for every scene. |
| voice | A voice ID from `VoicePort` |
| style notes | Optional, e.g. "late 20s, red jacket, calm" |
| rights confirmation | Required checkbox: "I have the right to use this face." Timestamped. |

**Face rights:** only AI-generated faces, illustrations, or real people who have consented. Animating real people without consent breaks providers' terms and YouTube's impersonation policy.

**Narrator:** a voice-only character, the default for lines with no speaker.

---

## 7. Pipeline

### 7.1 Scene planner

Built in two parts, so the approved text never passes through a model (`youtube/script_parser.py`, `youtube/planner.py`):

1. **Shots, in code.** Every script line is classified by its shape: `LABEL: line` or a speaker cue on its own line is dialogue; `VO:`/`NARRATOR:` and section labels (`HOOK:`, `CTA:`) are narration; scene headings (`INT. …`), `[brackets]`, `(beats)`, markdown headings and `VISUAL:`/`SFX:` labels are directions, shown but never spoken. Inline parentheticals are delivery notes, not speech. Narration is split at sentence ends into shots of about 25 words (8–10 seconds on screen); consecutive lines from one character are one shot. Unlabelled text is the narrator's.
2. **Annotations, by the model.** The LLM sees the shots by position and returns, per position, a shot type, a visual description and the characters on screen, plus a short description of each speaker. Its output contains no script text. Invalid values fall back to safe defaults per field; a failed call falls back entirely.

- **Word check (in code):** the parsed lines must cover the script exactly, each spoken line's words must appear in order in its source line, and the shots must read exactly the spoken words in order. Any mismatch fails the plan.
- **Speakers:** the user can reassign who speaks a shot; its words cannot be edited in YouTube Automation.
- **Short length check:** duration is estimated from word count, then measured from the audio. Over the Shorts limit it warns and suggests long-form; words are never trimmed.

### 7.2 Shot types

| Shot | On screen | Audio | Cost driver |
|---|---|---|---|
| Narration | Scene image, slow zoom or pan, captions | Narrator | Image |
| Dialogue | Speaking character, lip-synced | Character voice | Avatar seconds |
| Two-character | Both characters in one frame | Both voices | Avatar seconds (multi-character model) |
| Cutaway | Listener still or scene image | Speaker continues off-screen | Image |

**Dialogue cap:** a line longer than `YT_MAX_TALKING_SECONDS` (default 6) shows the speaker for that long, then cuts to a cutaway while the audio continues.

### 7.3 Voice
One audio file per line with the assigned character's voice, plus word timestamps for captions. If the provider doesn't return timestamps, the timings are worked out from the audio and text (forced alignment).

### 7.4 Scene images
- Prompt: channel or story **style lock** (or a default cinematic look) + the shot's visual description + only the characters in the shot + framing for the shot type (a dialogue shot shows the speaker's face clearly, for the avatar later) + the spoken line for mood.
- References: the approved character sheets of the characters present, each passed with a label ("Reference sheet for MAYA (Maya):"), plus the style's reference image. Nano Banana 2 keeps at most 4 characters consistent, so at most 4 are sent, the speaker first.
- A character the visual description names is always treated as on screen, whatever the planner listed, so their sheet is used.
- Rules learned from real output (2026-09-15): the model draws two moments as two copies of a character or as a split-screen, and adds letterboxing to "film still" looks. The planner describes one moment per shot, and every scene prompt forbids split screens, panels, collages, repeated people and black bars.
- One failed shot does not stop the others; its error stays on the shot and it can be redrawn alone. Rate limits are retried with waits of 15s, 30s, then 60s: on this project Vertex allowed about 5 image calls before returning 429.
- Faces and style references are shrunk to 1024px and re-encoded as JPEG before they leave the server, which also strips photo metadata.
- Batch mode (50% cheaper on Gemini) is not used yet.

### 7.5 Storyboard review (human)
The user sees every shot's image and text, and can regenerate an image, change a shot type or reassign a speaker. **Nothing beyond this point runs until the storyboard is approved.** Images cost cents; avatar video costs dollars.

### 7.6 Avatar (dialogue shots only)
- Input: the scene image of the speaking character plus that line's audio.
- Two-character shots use a multi-speaker model when both characters are in frame.
- Stops before starting if the estimated avatar cost exceeds `YT_AVATAR_BUDGET_USD` for the project (§11).

### 7.7 Captions
Burned-in captions from word timestamps, positioned for the format (lower third for 16:9, centre for Shorts).

### 7.7b Sound design (as built, `youtube/sound.py`)
- **Music:** a track the business uploads as an asset; one per project, looped or trimmed to the video and faded out. Licensing is the uploader's.
- **Ambience:** the planner writes a short sound line per shot ("heavy rain, distant thunder"), editable in the storyboard. `SoundPort` generates each distinct description once per project and caches it in storage, so re-renders do not pay again.
- **Ducking:** both beds are mixed under the voice through a sidechain compressor, so they drop whenever anyone speaks.
- Each render stores the mix it was made with, so changing music, volumes or a shot's sound marks it out of date.

### 7.8 Composer (ffmpeg)
- Narration and cutaway: slow zoom/pan over the image for the length of the audio.
- Dialogue: avatar clip, with a cutaway when capped. Clips are upscaled with Lanczos and lightly sharpened, and the part the avatar model redrew is colour-matched to the shot's still through a soft mask (see the finding under §14).
- Transitions, optional background music with volume ducking under speech, captions, loudness normalisation.
- Output: 1920×1080 (16:9) or 1080×1920 (9:16) MP4, plus a thumbnail from a chosen shot.

### 7.9 Metadata
Title, description and tags are generated with `LLMSingleton` and the Brand Brain for the business. This is a small separate step, not the enforcer loop. The user edits them before upload.

### 7.10 Video review and publishing
- Upload as **private**, with the synthetic-content disclosure set (§8, open question Q3).
- The user watches the private video and clicks **Publish**, which switches it to public, or schedules it.
- Each upload records the YouTube video ID, status and URL.

---

## 8. YouTube publishing

- **Sign-in:** Google OAuth (web flow) for the owner's account once, with the `youtube.upload` and `youtube` scopes (`youtube` is needed for `videos.update`, `channels.list` and `thumbnails.set`). The refresh token is stored encrypted with Fernet, keyed by `YT_TOKEN_KEY` or derived from `SECRET_KEY`. The OAuth `state` is signed and tied to the starting browser by an HttpOnly nonce cookie, so a sign-in link cannot attach someone else's channel.
- **OAuth app mode:** must be set to **"In production"**. In "Testing" mode refresh tokens expire after 7 days and uploads start failing silently. An unverified app is acceptable for the owner's own account; it shows a warning screen when connecting.
- **API audit (answers Q2):** every video uploaded through `videos.insert` from an API project created after 28 July 2020 that has not passed YouTube's compliance audit is **locked private**. A locked video cannot be made public through the API *or by hand in YouTube Studio*, and cannot be appealed; it must be re-uploaded after the audit. Uploading and reviewing work before the audit; publishing needs it. `publish` reads the video back and reports the lock instead of claiming success.
- **Disclosure (answers Q3):** `status.containsSyntheticMedia = true` on every upload and update. `status.selfDeclaredMadeForKids` must be chosen by the user. `videos.update` deletes status properties it is not sent, so every status field is always sent.
- **Quota (checked 2026-09-15):** `videos.insert` costs 1 from a separate bucket of 100 uploads a day; the general bucket is 10,000 units a day, from which `videos.update` and `thumbnails.set` take 50 and `videos.list` / `channels.list` take 1. Quotas reset at midnight Pacific time. A ledger (`yt_quota_usage`) counts use per Pacific day; an upload over quota waits (`waiting_quota`) and is retried after the reset, and a `quotaExceeded` answer from YouTube is treated the same way.
- **Thumbnails:** custom thumbnails need a phone-verified channel. A refused thumbnail is recorded as a note and does not fail the upload.
- **Policy:** YouTube restricts monetization of mass-produced or repetitive content, and requires disclosure of realistic synthetic content. Human review before publishing is the safeguard.

---

## 9. Ports and adapters

Same pattern as `LLMProvider` / `LLMSingleton`: an abstract port declares `name`, `required_env` and the interface; adapters implement it; a singleton picks one from an env var.

| Port | Interface (sketch) | Adapters (first → alternatives) | Selected by |
|---|---|---|---|
| `VoicePort` | `list_voices()`, `synthesize(text, voice_id) -> Audio` (24 kHz mono), `cost_usd(text)` | **Built:** Kokoro-82M (local, CPU, 28 English voices) and Google Cloud TTS. Later: ElevenLabs | `YT_VOICE_PROVIDER` (default for new characters; each character stores its provider) |
| `ImagePort` | `generate(prompt, labelled_references, aspect_ratio) -> GeneratedImage`, `cost_usd()`, `max_references`, `max_characters` | **Built:** Nano Banana 2 (`gemini-3.1-flash-image` on Vertex, verified live) and Seedream 4 via fal.ai (tested with fakes only; needs `FAL_KEY`). Later: FLUX.2 Pro | `YT_IMAGE_PROVIDER` |
| `AvatarPort` | `animate(image, mime, audio_wav, duration_s, prompt) -> AvatarClip`, `cost_usd(seconds)`, `min_seconds`, `max_seconds`, `lip_sync` | **Built:** still (no animation, free), Kling AI Avatar v2 Standard via fal.ai queue, InfiniteTalk via WaveSpeed (both tested with fakes only; need `FAL_KEY` / `WAVESPEED_API_KEY`). Later: Hedra Character-3, a multi-speaker model, self-hosted GPU | `YT_AVATAR_PROVIDER` |
| Composer | `youtube/media.py` building blocks and `youtube/render.py` (not a port: ffmpeg is the only implementation) | ffmpeg | `YT_FFMPEG` |
| `PublisherPort` | `authorization_url`, `exchange_code`, `channel`, `upload_private(video, metadata, session_url)` (resumable, chunked), `set_thumbnail`, `set_privacy(privacy, publish_at)`, `video_status`, `revoke` | **Built:** YouTube Data API v3 over HTTPS (tested with fakes; needs an OAuth client) | — |
| `SoundPort` | `generate(description, seconds, loop) -> mp3` | **Built:** off (music only) and ElevenLabs text to sound effects (`ELEVENLABS_API_KEY`; tested with fakes) | `YT_SOUND_PROVIDER` |
| `StoragePort` | `put(key, bytes)`, `get(key)`, `exists(key)`, `delete(key)`, `url(key)` | **Built:** Google Cloud Storage and local disk | `YT_STORAGE_PROVIDER` |

Each adapter reports its cost per call (per second, image or character) so the budget in §11 comes from real numbers.

---

## 10. Data model (new tables, `yt_` prefix)

| Table | Key columns |
|---|---|
| `yt_characters` | id, business_id, name, voice_provider, voice_id, style_notes, rights_confirmed_at, sheet_status |
| `yt_character_images` | id, character_id, kind (`upload` / `sheet`), storage_key, approved |
| `yt_styles` | id, business_id, name, prompt, reference_storage_key |
| `yt_projects` | id, business_id, source_generation_id, script_snapshot, approval_label, approved_at, format, style_id, status, estimated_cost_usd, actual_cost_usd |
| `yt_cast` | project_id, speaker_label, character_id |
| `yt_shots` | id, project_id, position, text, speaker_label, shot_type, visual_prompt, audio_key, image_key, clip_key, duration_s, status |
| `yt_renders` | id, project_id, format, video_key, thumbnail_key, status, error |
| `yt_uploads` | id, render_id, youtube_video_id, title, privacy, status, progress, upload_url (resumable session), retry_at, claimed_at, publish_at, published_at, youtube_status, error, thumbnail_error |
| `yt_channel` | id, business_id, channel_id, channel_title, refresh_token_encrypted, token_error, connected_at |
| `yt_quota_usage` | day (Pacific), bucket (`uploads` / `units`), used |
| `yt_assets` | id, business_id, name, kind (`product` / `logo` / `music`), storage_key |
| `yt_imported_scripts` | id, business_id, title, content, created_at |
| `yt_series` | id, business_id, name, logline |

**Columns added for series:** `yt_projects.series_id`, `.episode`, `.recap`.

**Columns added for products, end cards and sound:** `yt_projects.asset_ids`, `.end_card`, `.audio`; `yt_shots.asset_ids`, `.sound`, `.image_issues`; `yt_renders.end_card`, `.mix`; `yt_uploads.watched_seconds`.
| `yt_costs` | id, project_id, stage, provider, units, unit, cost_usd, created_at |

**Project status:** `draft → planned → cast → voiced → storyboard_ready → storyboard_approved → rendered → uploaded_private → scheduled → published`, derived from what the project has; `planning`, `voicing`, `drawing`, `rendering` and `uploading` while a background step runs; `failed` with the error. YouTube details (title, description, tags, category, made-for-kids) are columns on `yt_projects`.

---

## 11. Cost model

Prices checked on 2026-09-14 from provider pages; recheck before building.

| Item | Provider | Price |
|---|---|---|
| Scene image | Nano Banana 2 | $0.067 per 1K image ($0.034 batch) |
| | Seedream 4 Edit (fal) | $0.03 per image |
| | FLUX.2 Pro (fal) | $0.03 first megapixel + $0.015 per extra megapixel of input and output |
| Avatar | Kling AI Avatar v2 Standard (fal) | $0.056/s |
| | InfiniteTalk (WaveSpeed) | $0.03/s at 480p, $0.06/s at 720p |
| | Kling AI Avatar v2 Pro (fal) | $0.115/s |
| Voice | Local open-source TTS | $0 (CPU time) |

**Illustrative estimates** (not measured)

| | 10-minute long-form | 60-second Short |
|---|---|---|
| Scene images | ~60 × $0.03–0.067 ≈ **$2–4** | ~8 ≈ **$0.25–0.55** |
| Avatar (dialogue capped at 6 s) | ~90 s × $0.03–0.056 ≈ **$2.70–5** | ~15 s ≈ **$0.45–0.85** |
| Voice | $0 | $0 |
| **Total** | **≈ $5–9** | **≈ $0.70–1.40** |

**Budget controls**
- Estimate shown after planning and again after storyboard approval.
- `YT_AVATAR_BUDGET_USD` per project: avatar rendering does not start above it without explicit confirmation.
- Actual costs recorded in `yt_costs` and shown per project.

---

## 12. Infrastructure

**Capacity today**
- Development laptop: Intel i5-7300U (2 cores), 8 GB RAM, integrated graphics, no CUDA. It cannot run image or video models; it can run local TTS.
- Production VM (`brandmaestro`): e2-standard-2 (2 vCPUs, 8 GB RAM), 29 GB disk free, already running the API, 5 Celery workers, Postgres and Redis.

**Consequences**
- **Rendering does not run on the production VM.** ffmpeg on 2 shared vCPUs would slow marketing, and video files would fill the disk.
- **Built:** `worker_render` (compose profile `render`) consumes `yt_render` and needs the same storage as the API (`YT_GCS_BUCKET`). **Proposed:** run it on its own machine, either a separate VM or on-demand jobs (e.g. Cloud Run jobs), consuming the `yt_*` queues from the same Redis. Exact hosting is open (Q5).
- **Assets** (uploads, character sheets, images, audio, clips, renders) go to **Google Cloud Storage**, not local disk. Retention: keep final renders; delete intermediate files after N days (Q6).
- **Queues:** `yt_plan`, `yt_media` (voice, images, avatar API calls; mostly waiting on network), `yt_render` (ffmpeg; CPU-heavy), `yt_publish` (quota-limited). `worker_youtube` serves `yt_plan`, `yt_media` and `yt_publish` at concurrency 1, so a long upload delays voicing and drawing; give `yt_publish` its own worker if that becomes a problem.
- **Self-hosted GPU:** not in v1. `AvatarPort` and `ImagePort` allow a self-hosted adapter later, if rented-GPU benchmarks beat API prices.

---

## 13. API and UI

**Routes** (`/youtube`, bearer token, scoped to the user's `business_id`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/youtube/scripts` | Eligible scripts and ads, and imported scripts, with approval labels |
| POST/DELETE | `/youtube/scripts/import`, `/youtube/scripts/{id}` | Import your own script (paste or .txt/.md); delete an imported one |
| GET/POST/DELETE | `/youtube/assets` | Product photos, locations, logos and music tracks |
| POST | `/youtube/assets/draw` | Draw a location from a description |
| GET/POST/PATCH/DELETE | `/youtube/series`, `/youtube/series/{id}` | Series and their episodes |
| POST/GET/PATCH/DELETE | `/youtube/characters` | Character library |
| POST | `/youtube/characters/{id}/sheet` | Generate or regenerate a character sheet |
| POST/GET | `/youtube/styles` | Style locks |
| POST | `/youtube/projects` | Create from a script (snapshot, format, style) |
| POST | `/youtube/projects/{id}/plan` | Run the scene planner |
| PUT | `/youtube/projects/{id}/cast` | Assign characters to speakers |
| POST | `/youtube/projects/{id}/storyboard` | Voice and images |
| PATCH | `/youtube/projects/{id}/shots/{shot_id}` | Regenerate an image, change shot type or speaker |
| POST | `/youtube/projects/{id}/approve-storyboard` | Unlocks avatar and render |
| GET | `/youtube/projects/{id}/render/estimate` | Avatar cost, budget and anything blocking a render |
| POST | `/youtube/projects/{id}/render` | Avatar, captions, composer (409 above budget unless `confirm_over_budget`) |
| GET | `/youtube/projects/{id}/renders/{render_id}/video` and `/thumbnail` | The MP4 and its thumbnail (`as_link` for a signed URL) |
| GET | `/youtube/projects/{id}/shots/{shot_id}/clip` | A shot's talking clip |
| PATCH | `/youtube/projects/{id}/metadata` | Title, description, tags, category, made for kids |
| POST | `/youtube/projects/{id}/metadata/generate` | Draft them from the script and Brand Brain |
| POST | `/youtube/projects/{id}/upload` | Private upload of the current render (`reviewed: true` required) |
| POST | `/youtube/projects/{id}/uploads/{upload_id}/publish` | Make public now, or schedule with `publish_at` |
| POST | `/youtube/projects/{id}/uploads/{upload_id}/refresh` \| `cancel` \| `retry` | Read back from YouTube; cancel a waiting upload; try again |
| GET | `/youtube/projects/{id}` | Status, shots, costs |
| GET/DELETE | `/youtube/channel` | Connection status and quota; disconnect (revokes the token) |
| POST | `/youtube/channel/connect` | Start Google sign-in |
| GET | `/youtube/channel/callback` | Google's redirect back (authenticated by signed state and nonce cookie) |

**UI:** a **YouTube Studio** sidebar section with tabs for Scripts, Characters, Projects and Channel. Marketing panels are unchanged apart from one optional **Send to YouTube** button on eligible scripts, shown only when YouTube Automation is configured.

---

## 14. Build order

Each phase is useful on its own and ends with a working, testable result.

| Phase | Delivers | Done when |
|---|---|---|
| **0. Groundwork** | `generations.approved` saved by the deployer; `youtube/` package, tables and queues; `StoragePort` (GCS) | Marketing tests pass unchanged; eligible scripts list shows correct labels |
| **1. Voice** | `VoicePort` + local TTS adapter; scene planner with word check; casting; audio per line | An approved script becomes a downloadable voiced audio track with the right voice per speaker |
| **2. Characters and storyboard** | Character library, character sheets, style lock, `ImagePort` (Nano Banana 2 + Seedream), storyboard review UI | A 5-scene storyboard keeps two characters recognisably consistent |
| **3. Render** | `AvatarPort` (Kling Standard + InfiniteTalk), dialogue cap, captions, ffmpeg composer, budget gate, render worker off the production VM | A previewable MP4 in both formats, with recorded cost |
| **4. Publish** | Channel connection, private upload with disclosure, publish button, quota-aware queue, metadata step | A video goes from approved script to a published YouTube video with human sign-off |

**Phase 1 as built:** `yt.plan.plan_project` and `yt.media.voice_project` run on an optional `worker_youtube` compose service (profile `youtube`, not started by the deploy workflow). Each shot is voiced to its own WAV and joined into one track with 0.3 s gaps within a speaker and 0.6 s between speakers; only shots without audio are re-voiced, and recasting a speaker discards only that speaker's audio. Voice samples for casting are generated once per provider and shared. The YouTube Studio panel (`static/youtube.js`) covers scripts, characters and projects.

**Phase 2 as built:** faces need a rights confirmation before upload (withdrawing it blocks drawing); a character sheet is generated from 1–5 faces and must be approved; style locks carry a description and an optional reference image; `yt.media.character_sheet` and `yt.media.storyboard_project` run on the worker. Project status is derived from what the project has (shots, cast, audio, images, approval), so voicing and drawing can happen in either order, and any change to a shot, the cast, a sheet, the style or the audio clears storyboard approval. Approval needs every image and the voice track.

**Phase 3 as built:** `yt.render.render_project` runs on `worker_render` (compose profile `render`, image target `render` with ffmpeg and DejaVu fonts), meant for a machine other than the production VM. A render needs an approved storyboard; it first estimates avatar cost and refuses above `YT_AVATAR_BUDGET_USD` unless confirmed (the UI asks). Dialogue and two-character shots animate at most `YT_MAX_TALKING_SECONDS` (default 6) of their line; the rest plays over the shot's still with a slow pan, and narration shots are always stills. Talking clips are stored per shot and reused while their image, audio, provider and length are unchanged; each paid clip is committed and costed before the next, so a failure does not waste finished clips. Captions are timed per shot in proportion to word length with pauses at punctuation (Kokoro gives no word timings), grouped into cues of up to 5 words and burned in as ASS subtitles, higher up the frame for Shorts. Segments are cut to exact frame counts from the audio timeline so sound stays in sync, the voice track gets a 0.6 s tail, and loudness is normalised to −14 LUFS. The thumbnail is taken from the first talking shot. The last `YT_KEEP_RENDERS` (default 3) renders are kept; a render is "current" until the storyboard is approved again. Multi-speaker animation (`animate_pair`) and background music are not built; two-character shots animate the speaker only.

**Real run (2026-09-15, still avatar, $0):** the phase 2 bakery storyboard rendered locally in 38.5 s into a 15 s 1920×1080 MP4 (2.3 MB) with readable captions and correct sync. Kling and InfiniteTalk have not been run live yet.

**Real run (2026-09-15, Nano Banana 2, about $1.00 including test faces):** two AI-generated characters kept recognisably consistent across a 5-shot storyboard — same face, hair, earrings, apron; same glasses, beard, sweater. Seedream was not compared: there is no fal.ai key yet.

**Phase 4 as built:** the Channel tab walks through the Google Cloud setup (enable YouTube Data API v3, consent screen "In production", Web OAuth client with the redirect URI shown) and connects one channel per business. The project page gains a YouTube section: details can be drafted with `LLMSingleton` from the script and the script Brand Brain, then edited; limits are enforced in code (title 100 characters, description 5000 bytes, tags 500 characters counted as YouTube counts them, no `<` or `>`). Upload needs the current render, a connected channel, a title, a made-for-kids answer, and a ticked "I watched the current video". `yt.publish.upload_video` runs on `worker_youtube` (now listening on `yt_publish`), claims the upload so duplicate deliveries do nothing, uploads in 8 MB resumable chunks and keeps the session so an interrupted upload continues, sets the thumbnail, and leaves the video private. The person then publishes now or schedules it; refresh reads processing, rejection and scheduled publishing back from YouTube. A refused sign-in marks the channel for reconnection. Uploaded renders are never pruned. Not built: editing title or description after upload (do it in YouTube Studio), playlists, captions files.

**Ads: product references and end card (added 2026-09-15, `youtube/brand_assets.py`):** businesses upload product photos and logos once (`yt_assets`). A project lists the products it features; a shot shows a product when its line or visual names it, or when a person picks products for it. Product photos go to the image model as labelled references with an instruction to reproduce the product exactly (transparent PNGs are flattened on white), the prompt allows only the product's own printed text, and the image check ignores that text. The end card (logo, call to action, URL, brand colour, 2–6 s) is drawn with Pillow, appended after the last shot with silence under it, previewed in the UI, and stored on each render so changing it marks the render out of date. Routes: `/youtube/assets` (GET, POST multipart, DELETE, `/{id}/image`), `PATCH /youtube/projects/{id}` with `asset_ids` or `end_card` (only fields sent change), `PATCH .../shots/{id}` with `asset_ids`, `GET .../end-card/preview`. Not built: background music, brand fonts, product placement guarantees (the model can still alter a product, so check the storyboard).

**Series, story so far and locations (added 2026-09-16, `youtube/series.py`):** projects can belong to a `yt_series` and are numbered as episodes. Joining a series copies the previous episode's style lock, products and locations, sound settings and end card, and casts each speaker as the character who played them before (once the new episode is planned). Each episode's script is summarised into a short recap (one `LLMSingleton` call, failure is logged and skipped), and the planner of later episodes receives the series logline plus those recaps under STORY SO FAR. Locations are a new asset kind: uploaded, or drawn from a description with `ImagePort` (people-free reference), matched into shots by name like products and passed as a "the same place" reference with its own prompt section. Deleting a series leaves its episodes as stand-alone projects. Routes: `/youtube/series` (GET, POST), `/youtube/series/{id}` (GET with episodes, PATCH, DELETE), `series_id` on project create and PATCH, `POST /youtube/assets/draw`. Not built: continuity of props or wardrobe beyond the character sheets, and automatic ordering of episodes by script.

**Imports and the handoff button (added 2026-09-16):** `youtube/imports.py` stores scripts a person pastes or uploads; approved ads join approved scripts as video sources; approving either in content writing offers **Make a video**, which creates the project, starts planning and opens YouTube Studio.

**Sound design (added 2026-09-16, `youtube/sound.py`, `media.mix_audio`):** uploaded music per project, per-shot generated ambience behind `SoundPort` (off by default), both ducked under the voice; the mix is stored on each render. Talking clips are upscaled with Lanczos, sharpened, and colour-matched where the avatar model redrew the frame (`media.color_match_filter`, applied through `alphamerge`/`overlay`; `maskedmerge` shifted the whole frame and was wrong for this).

**Finding (2026-09-16, real footage):** colour matching does almost nothing for InfiniteTalk. Its clips already match the still over the frame, and the visible difference is the redrawn mouth: the changed pixels are different content (open mouth, invented lips and teeth), not the same content tinted differently, so no per-channel match can fix it. Only a better talking-head model, wider framing of speaking shots, or a full-body animation model will. The sharper upscale did help, and the correction stays for providers that do tint.

**Anti-slop checks (added 2026-09-15, `youtube/quality.py`):** YouTube details are checked against the script and Brand Brain (hype phrases, schedule promises, numbers and contact details not in the script, shouting, emoji, the brand's banned punctuation); a flagged draft is rewritten once and remaining issues are shown on every read. Storyboard images are checked for flat borders in code and, with Nano Banana, by `gemini-2.5-flash` for visible text, panels, blurred edge strips, repeated people and malformed bodies; a flagged image is redrawn once (both attempts costed) and remaining issues are marked, with a confirmation before approving. Uploads record how many seconds the person played in the app. Checks never block; they report.

**Real run (2026-09-15/16, production, InfiniteTalk 720p):** a 199-word imported demo script became a 75 s Short: 17 shots planned in 26 s, two characters with approved sheets, a 74 s Kokoro voice track, 17 Nano Banana images ($1.34 including three redraws for text or blurred strips), 10 animated speaking lines (23.1 s, $1.86, about 2½ minutes per clip on WaveSpeed), assembled in 6½ minutes on `worker_render`, and uploaded to the connected channel as private. Total about $3.50. Re-renders reuse the cached clips and cost nothing.

**Verified 2026-09-15 (fakes):** 52 tests over the adapter (chunking, resume, retry, quota and auth errors, every status field), quota days across DST, encryption, metadata, the service and routes; the full browser flow in Edge, from connecting a channel through a fake Google redirect to upload progress, schedule and publish. Not yet run against the real YouTube API: that needs an OAuth client and, for publishing, the audit.

**Before phase 2:** test Nano Banana 2 against Seedream on the same 5-scene storyboard (under $1).
**Before phase 3:** test Kling Standard, InfiniteTalk and Hedra on the same 20-second dialogue scene (about $3).

---

## 15. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Characters drift between scenes | Story looks incoherent | Approved character sheets, style lock, only in-shot characters as references, storyboard review |
| Image provider rate limits | Long storyboards fail partway | Retries with waits; per-shot errors; redraw missing shots only; request a higher quota for production volume |
| Provider price or model changes | Costs rise or quality drops | Adapters; per-call cost recorded; the two tests repeatable |
| Refresh token expires | Uploads fail silently | OAuth app in production mode; channel health check shown in UI |
| Uploads locked private | Cannot publish via API | Publish manually until the API audit is done |
| Monetization policy on mass-produced content | Channel not monetized | Human review, distinct stories, disclosure set |
| Rendering load on production | Marketing slows | Separate render worker and machine |
| Face rights | Legal and policy exposure | Rights checkbox, consent-only uploads |
| Marketing script without speaker labels | Wrong speaker assignment | Planner infers; user reassigns at casting |

---

## 16. Open questions

| # | Question |
|---|---|
| Q1 | ~~Which local TTS model?~~ Kokoro-82M, full-precision model: 28 English voices (American and British, male and female), about real time on a 2017 laptop CPU. Its int8 build was ~10× slower there. Revisit if stories need more voice variety. |
| Q2 | ~~Is the audit needed to publish?~~ Yes. Unaudited uploads are locked private, even in YouTube Studio (§8). Request the audit before publishing publicly. |
| Q3 | ~~Disclosure field?~~ `status.containsSyntheticMedia` (§8). |
| Q4 | Default `YT_MAX_TALKING_SECONDS` and `YT_AVATAR_BUDGET_USD` values after the first test videos. |
| Q5 | Render worker hosting: separate small VM, or on-demand jobs? |
| Q6 | Retention period for intermediate assets in storage. |
| Q7 | ~~Background music source and licensing?~~ Music is uploaded by the business as an asset, so licensing stays with them; generated ambience comes from `SoundPort`. |
| Q8 | Which full-body animation model replaces the talking-head adapter when lip-sync quality matters more than cost? |
| Q9 | How far should series continuity go: props and wardrobe sheets, or is the cast plus locations enough? |

---

## Appendix: sources for prices (checked 2026-09-14)

- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [fal: Seedream 4 Edit](https://fal.ai/models/fal-ai/bytedance/seedream/v4/edit)
- [fal: FLUX.2 Pro Edit](https://fal.ai/models/fal-ai/flux-2-pro/edit)
- [fal: Kling AI Avatar v2 Standard](https://fal.ai/models/fal-ai/kling-video/ai-avatar/v2/standard)
- [fal: Kling AI Avatar v2 Pro](https://fal.ai/models/fal-ai/kling-video/ai-avatar/v2/pro)
- [WaveSpeed: InfiniteTalk](https://wavespeed.ai/models/wavespeed-ai/infinitetalk)
- [WaveSpeed: InfiniteTalk Multi](https://wavespeed.ai/models/wavespeed-ai/infinitetalk/multi)
- [Hedra plans](https://www.hedra.com/plans)
