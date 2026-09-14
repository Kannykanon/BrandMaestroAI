# YouTube Automation — Design

**Status:** Draft, agreed in discussion on 2026-09-14. Not yet built.
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
| D2 | Scripts come **only from marketing**. No second research, writing or enforcer loop. | The marketing pipeline already does the expensive, careful work. |
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

### 7.1 Scene planner (LLM)
- **Input:** script snapshot, format (long-form or Short), cast list.
- **Output:** an ordered shot list. Each shot has: the exact line text, speaker (NARRATOR or character), shot type, visual description for the image prompt, and the characters present.
- **Speakers:** marketing scripts may or may not label them (e.g. `MAYA:`). The planner infers speakers when labels are missing, and the user can reassign them.
- **Word check (in code, not by asking the model):** join all shot texts and compare with the snapshot after normalising whitespace. Any mismatch fails the plan. Same principle as the enforcer's deterministic gates.
- **Short length check:** estimate duration from word count (and later from real audio length). If it is over the Shorts limit, warn and suggest long-form; never trim words.

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
- Prompt: channel or story **style lock** + shot description + only the characters in the shot.
- References: the character sheets of the characters present, plus an optional style reference image.
- Generated for narration, cutaway and dialogue shots. The dialogue shot's image is the character in the scene, and becomes the avatar input (§7.6).
- Use batch mode where the provider offers it (Gemini batch pricing is 50% lower).

### 7.5 Storyboard review (human)
The user sees every shot's image and text, and can regenerate an image, change a shot type or reassign a speaker. **Nothing beyond this point runs until the storyboard is approved.** Images cost cents; avatar video costs dollars.

### 7.6 Avatar (dialogue shots only)
- Input: the scene image of the speaking character plus that line's audio.
- Two-character shots use a multi-speaker model when both characters are in frame.
- Stops before starting if the estimated avatar cost exceeds `YT_AVATAR_BUDGET_USD` for the project (§11).

### 7.7 Captions
Burned-in captions from word timestamps, positioned for the format (lower third for 16:9, centre for Shorts).

### 7.8 Composer (ffmpeg)
- Narration and cutaway: slow zoom/pan over the image for the length of the audio.
- Dialogue: avatar clip, with a cutaway when capped.
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

- **Sign-in:** Google OAuth for the owner's account once, with the `youtube.upload` scope (plus whatever is needed to change privacy status). The refresh token is stored encrypted, like other secrets.
- **OAuth app mode:** must be set to **"In production"**. In "Testing" mode refresh tokens expire after 7 days and uploads start failing silently. An unverified app is acceptable for the owner's own account; it shows a warning screen when connecting.
- **API audit:** uploads from unaudited API projects are restricted to private. v1 works either way, because it uploads as private. Publishing through the API may need YouTube's API compliance audit; until then, publishing can be done by hand in YouTube Studio. (Q2)
- **Quota:** the default daily quota covers only a handful of uploads per day. Uploads go through a queue that respects the quota, and a higher quota can be requested if needed.
- **Policy:** YouTube restricts monetization of mass-produced or repetitive content, and requires disclosure of realistic synthetic content. Human review before publishing is the safeguard.

---

## 9. Ports and adapters

Same pattern as `LLMProvider` / `LLMSingleton`: an abstract port declares `name`, `required_env` and the interface; adapters implement it; a singleton picks one from an env var.

| Port | Interface (sketch) | Adapters (first → alternatives) | Selected by |
|---|---|---|---|
| `VoicePort` | `list_voices()`, `synthesize(text, voice_id) -> Audio(bytes, word_timings)` | Local open-source TTS (e.g. Kokoro, runs on CPU) → Google Cloud TTS, ElevenLabs | `YT_VOICE_PROVIDER` |
| `ImagePort` | `generate(prompt, reference_images, aspect) -> Image` | Nano Banana 2 (Vertex) → Seedream 4, FLUX.2 Pro | `YT_IMAGE_PROVIDER` |
| `AvatarPort` | `animate(image, audio) -> Clip`; optional `animate_pair(image, audio_left, audio_right)` | Kling AI Avatar v2 Standard → InfiniteTalk, Hedra Character-3, self-hosted GPU | `YT_AVATAR_PROVIDER` |
| `ComposerPort` | `render(shots, format) -> Video` | ffmpeg | — |
| `PublisherPort` | `upload_private(video, metadata)`, `publish(video_id)`, `status(video_id)` | YouTube Data API | — |
| `StoragePort` | `put(bytes) -> key`, `get(key)`, `signed_url(key)` | Google Cloud Storage → local disk (development) | `YT_STORAGE_PROVIDER` |

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
| `yt_uploads` | id, render_id, youtube_video_id, privacy, published_at, status |
| `yt_channel` | id, business_id, channel_id, refresh_token_encrypted, connected_at |
| `yt_costs` | id, project_id, stage, provider, units, unit, cost_usd, created_at |

**Project status:** `draft → planned → cast → voiced → storyboard_ready → storyboard_approved → animated → rendered → uploaded_private → published` (or `failed` at any step, with the error).

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
- **Proposed:** a separate `yt_render` worker on its own machine, either a separate VM or on-demand jobs (e.g. Cloud Run jobs), consuming the `yt_*` queues from the same Redis. Exact hosting is open (Q5).
- **Assets** (uploads, character sheets, images, audio, clips, renders) go to **Google Cloud Storage**, not local disk. Retention: keep final renders; delete intermediate files after N days (Q6).
- **Queues:** `yt_plan`, `yt_media` (voice, images, avatar API calls; mostly waiting on network), `yt_render` (ffmpeg; CPU-heavy), `yt_publish` (quota-limited).
- **Self-hosted GPU:** not in v1. `AvatarPort` and `ImagePort` allow a self-hosted adapter later, if rented-GPU benchmarks beat API prices.

---

## 13. API and UI

**Routes** (`/youtube`, bearer token, scoped to the user's `business_id`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/youtube/scripts` | Eligible scripts with approval labels |
| POST/GET/PATCH/DELETE | `/youtube/characters` | Character library |
| POST | `/youtube/characters/{id}/sheet` | Generate or regenerate a character sheet |
| POST/GET | `/youtube/styles` | Style locks |
| POST | `/youtube/projects` | Create from a script (snapshot, format, style) |
| POST | `/youtube/projects/{id}/plan` | Run the scene planner |
| PUT | `/youtube/projects/{id}/cast` | Assign characters to speakers |
| POST | `/youtube/projects/{id}/storyboard` | Voice and images |
| PATCH | `/youtube/projects/{id}/shots/{shot_id}` | Regenerate an image, change shot type or speaker |
| POST | `/youtube/projects/{id}/approve-storyboard` | Unlocks avatar and render |
| POST | `/youtube/projects/{id}/render` | Avatar, captions, composer |
| POST | `/youtube/projects/{id}/upload` | Private upload |
| POST | `/youtube/projects/{id}/publish` | Make public or schedule |
| GET | `/youtube/projects/{id}` | Status, shots, costs |
| GET/POST | `/youtube/channel` | Connection status, OAuth connect |

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

**Before phase 2:** test Nano Banana 2 against Seedream on the same 5-scene storyboard (under $1).
**Before phase 3:** test Kling Standard, InfiniteTalk and Hedra on the same 20-second dialogue scene (about $3).

---

## 15. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Characters drift between scenes | Story looks incoherent | Approved character sheets, style lock, only in-shot characters as references, storyboard review |
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
| Q1 | Which local TTS model and voice set gives enough distinct character voices, or is a cloud voice library needed for variety? |
| Q2 | Does the API audit need to be done before one-click publish works, or can a private upload be made public through the API without it? |
| Q3 | Exact API field for the synthetic-content disclosure on upload. |
| Q4 | Default `YT_MAX_TALKING_SECONDS` and `YT_AVATAR_BUDGET_USD` values after the first test videos. |
| Q5 | Render worker hosting: separate small VM, or on-demand jobs? |
| Q6 | Retention period for intermediate assets in storage. |
| Q7 | Background music source and licensing. |

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
