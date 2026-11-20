# CLAUDE.md - Dolladabbe YouTube pipeline

Context for Claude Code. Read this before changing anything.

## The business and the goal

Dolladabbe is a home-style cloud kitchen in India (mom cooks, brother shoots).
They post 30-40 second recipe reels. Instagram posting is handled; YouTube is
owned by Avish (the owner of this repo), who was not posting consistently.

This repo automates YouTube: clips dropped in a Telegram group become
researched, packaged, approved and (later) uploaded and analysed Shorts.
Everything is controlled from Telegram.

### Agreed goals
1. Consistency: every clip is scheduled within 24h of approval; 3-4 Shorts a
   week on fixed slots.
2. Low effort: owner spends under 5 min a week (approvals only). Mom does
   nothing new. Brother only adds a caption.
3. Zero cost: free tiers only. Never add anything that needs billing.
4. Better packaging: title/description/tags from research on breakout videos
   for the dish plus the channel's own past winners.
5. Long-form: one auto-built compilation of the best clips per month.
6. Learning: 7-day post-mortem per video; lessons saved and applied to later
   videos; weekly report and "shoot next week" brief on Telegram.
7. Reliability: any failure reaches the owner on Telegram; no clip lost or
   posted twice.
8. Growth: in 90 days, median views per Short and monthly subscribers gained
   clearly above the baseline (baseline not yet measured).

Not goals: guaranteed virality, Instagram, creative editing, full-recipe
long videos (maybe later).

## Architecture

```
Telegram group / owner DM
   -> Telegram webhook (secret token header)
   -> Cloudflare Worker  worker/worker.js   (free plan; filters noise)
   -> GitHub repository_dispatch "telegram_update"
   -> GitHub Actions  .github/workflows/telegram.yml
   -> python -m pipeline.handle_update     (one run per Telegram update)
   -> commits state/ back to the repo (retrying pull --rebase + push)
```

- Repo: `DevAvishArora/dolodedabbe-youtube` (private). Note the spelling.
- Worker: `dolladabbe-bot` at `https://dolladabbe-bot.dolodedhabbe.workers.dev/`
  (Cloudflare account of the Dolladabbe Gmail).
- Telegram bot: `@DoloDeDabbe_bot`, admin in the "DoloDeDabbe" supergroup,
  privacy mode disabled.
- No database. State is JSON in the repo: `state/settings.json` and one file
  per clip in `state/videos/<telegram message id>.json` (one file per clip so
  concurrent runs do not conflict).

### Modules
| File | Role |
|---|---|
| `pipeline/handle_update.py` | Router: new clips, album handling, dish-name replies, owner commands, approvals |
| `pipeline/media.py` | Telethon (MTProto, logged in as the bot) for files >20 MB; album lookup; ffmpeg join/crop/fit |
| `pipeline/research.py` | YouTube Data API search (API key, read only); ranks by views per subscriber |
| `pipeline/writer.py` | Gemini REST calls: drafts and plain-language command routing; retries, low thinking, fallback model |
| `pipeline/tg.py` | Bot API text messages (splits >4000 chars) |
| `pipeline/state.py` | JSON state helpers |
| `pipeline/config.py` | Reads env/secrets |
| `voice.md` | Channel voice, injected into every writing prompt (owner still needs to fill it in) |
| `tests/test_logic.py` | Unit tests; all external calls are faked |

### Clip flow
1. Brother posts in the group with a caption: `Dish name #final` (use as is)
   or `Dish name #raw` (edit). No tag = final. No dish = bot asks in the group
   and waits for a reply to that question.
2. Multiple raw clips: sent as ONE album with one caption. Each album item
   arrives as its own update/run; only the run for the lowest message id
   handles a `#raw` album (others exit). A non-raw album = separate reels.
3. Raw editing: join in order, crop to 9:16 1080x1920, fit target length
   (setting `raw_seconds`, default 40, `/length`): untouched if it fits, else
   speed up to max 4x (audio dropped when sped up), else keep the middle of
   each clip. Optional logo `assets/logo.png` top-right.
4. Research -> Gemini draft (title, 2 alternatives, on-screen hook,
   description, tags) -> sent to owner DM.
5. Owner replies to the draft: `ok` approves; any other text = change request
   (redraft). Replies are matched by `draft_msg_id`.
6. Approved: copy-ready title/description/tags sent. Owner posts manually
   until phase 2.

### Owner commands (DM only; anyone else is ignored)
`/status /pause /resume /redo <id> /length <s> /instruct <rule>
/instructions /forget <n> /help`. Plain text that is not a reply to a draft
is routed to a command by Gemini.

## Secrets and config (values are NOT in this repo)

GitHub Actions secrets: `TG_BOT_TOKEN TG_OWNER_ID TG_CHAT_ID TG_API_ID
TG_API_HASH YT_API_KEY GEMINI_API_KEY`.
GitHub Actions variables: `GEMINI_MODEL` = `gemini-3.8-flash`; optional
`GEMINI_FALLBACK_MODEL` (code default `gemini-3.5-flash-lite`).
Worker secrets: `TG_WEBHOOK_SECRET TG_CHAT_ID TG_OWNER_ID GH_REPO GH_TOKEN`.

Rules: never print, log, commit or echo secret values. If the bot token
changes, re-run setWebhook with the same secret_token.

## Free-tier facts (checked Nov 2026; re-check before relying on them)
- Gemini free tier: no billing account = no charges; over-limit requests just
  fail. Free models include `gemini-3.8-flash`, `gemini-3.5-flash-lite`,
  `gemini-4-hydrogen` (low daily limit). Carbon/Argon/Neon are paid only.
  3.x thinking is controlled with `generationConfig.thinkingConfig.thinkingLevel`.
- YouTube Data API: since June 2026 `videos.insert` has its own bucket of
  100 calls/day; other calls share 10,000 units/day (`search.list` = 100).
  Unaudited projects: API uploads are locked to PRIVATE and cannot be
  appealed. The compliance audit form must be approved before auto-upload
  goes public. Audit not yet submitted as of 20 Nov 2026.
- Telegram Bot API: downloads capped at 20 MB, hence Telethon.
- GitHub Actions: 2,000 free minutes/month for private repos.
- Cloudflare Workers free: 100k requests/day.

## Status (20 Nov 2026)
- Phase 1 is live end to end: `/help` works through the full chain.
- First real clip (id 7, "Apple Protein Smoothie", #final) failed with a
  Gemini read timeout at 90s. Fixed in `writer.py` (timeout 180s, 3 retries,
  `thinkingLevel: low` with automatic removal on HTTP 400, fallback model) and
  `handle_update.py` (one error message instead of two).
- Also fixed: `/redo` on a held clip with no dish now asks in the group; replies
  to older drafts work (`draft_history`); a #final album shares its caption;
  `/status` lists pending before approved; one Telethon login per run; empty
  env values fall back to defaults; workflow alerts the owner on Telegram when
  setup or saving state fails.
- Known risk: a reply sent within seconds of a draft can arrive before that
  draft's state is pushed, and is then not recognised.
- Clip 4 is awaiting its dish name; clip 7 needs `/redo 7`.
- Manual Telegram test sheet: see the "Dolladabbe Test Sheet" artifact.
- `voice.md` and `assets/logo.png` not yet provided.
- The git clone is `../dolladabbe-youtube`; `dolladabbe-youtube 2` was a
  non-git copy where the Gemini fix was first written.

## Roadmap
- Phase 2: OAuth (one-time login by an account that owns/manages the channel;
  store refresh token as a secret), `videos.insert` as private with `publishAt`
  set to the next free slot (`settings.slots`, IST), mark posted. Shorts =
  vertical and <=3 min. Blocked on the audit for public visibility; until then
  keep the manual-post path working.
- Phase 3: scheduled workflow pulling YouTube Analytics at 48h and 7d
  (views, average view percentage, likes, comments, subs gained); per-video
  post-mortem against channel median; "lessons" stored in state with source
  video and status (open/proven/dropped); packaging lessons injected into the
  writing prompt automatically; shooting lessons sent to the group as a
  checklist; new clips checked against open lessons; lessons verified after a
  few videos follow them. Weekly Telegram report + "shoot next week" brief.
  Data is thin at 3-4 clips/week: only trust patterns across several clips.
- Phase 4: monthly compilation: pick 6-8 best clips, re-download from Telegram,
  join with title cards, chapters with timestamps in the description, approve,
  upload as a regular video (>3 min).
- Later: review then vendor (fixed version, MIT) useful pieces of
  `Jakeschincariol/youtube-agent-skill`: `swipe.py` (outlier ranking),
  `title.py` (title lint, show warnings in the approval message),
  `hookscore.py` + `hooks.json` (hook suggestions), `retention.py` (post-mortem),
  `yt-seo` and `yt-audit` instructions as Gemini prompts. Skip script, edit,
  chapters, shorts-from-long, plan, comment skills.

## Working conventions
- The bot commits to `state/` after every run, so ALWAYS `git pull --rebase`
  before pushing, and never hand-edit `state/` while the bot is running.
- Run `python -m pytest -q` before every push. Keep external calls faked in
  tests.
- Pushing to `main` is deploying: the next Telegram update uses the new code.
  Keep changes small and tell the owner what to test in Telegram.
- Worker changes are NOT deployed by git push: paste `worker/worker.js` into
  the Cloudflare editor and click Deploy (or set up wrangler later).
- Keep it zero-cost and keep Telegram as the only control surface.
- The owner reads on a phone: keep Telegram messages short and plain.
- This machine also has a work GitHub account (`Avish-Commversion`); pushes
  to this repo must use `DevAvishArora` (remote URL includes the username,
  password = fine-grained token).
