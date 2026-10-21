# Habitica Analytica

## What this is

A personal coaching harness on top of Habitica, self-hosted on your own VPS, talked to through Telegram (or a terminal). It has three jobs:

1. **Front-end to Habitica.** Complete tasks, create todos, check stats, switch gear, browse challenges — by text or voice, in English, Russian or French. The bot can answer in voice too.
2. **Telemetry collector.** Habitica only exposes the *current* streak and prunes task history for non-subscribers. We snapshot the account continuously and receive its webhooks, so we own the full record. Nothing here assumes a paid Habitica account — the point is that anyone can run this on a VPS and get subscriber-grade analytics from their own data.
3. **Analyst and coach.** Plain statistics (polars) over our own telemetry find the patterns you would avoid looking at — the daily you keep for four days, skip one, keep for four days again; the habit you already do three times a week that should become a daily; the todos you always finish the night before the deadline. An LLM then turns *those findings* (not raw data) into short, concrete advice. Statistics find; the LLM explains. The LLM never does the finding.

### The thesis: smooth transitions

Habitica gives you three task types and leaves the transitions to you. This app is about the transitions.

```
aspiration  →  first todo  →  habit  →  daily  →  second nature
"I want to     "Go to the      "Workout"      "Workout        (retire the daily;
 start          gym once"       scored 2-3×     Mon/Wed/Fri"    it no longer needs
 lifting"                       a week                          tracking)
```

You bring an aspiration ("I want to learn Latin"); the bot discusses it with you and you agree on a starter set of todos and habits. From then on the statistics watch the telemetry and propose the next transition when the data supports it — todo done → make it a habit? habit stable → make it a daily? daily solid for months → retire it? daily failing → demote it? Every transition is proposed by numbers, explained by the model, and executed only after you press a button. The goal is that a new skill enters your life as a single small task and leaves the app, months later, as something you simply do.

Google Calendar is the one external integration: Habitica todos get scheduled into free slots on your main calendar, and `(done)` on an event is the same as pressing *done* in Habitica — in both directions.

**Codex will review your work.**

---

## Ground rules for Claude Code

These are the operating rules. They come from Gourze, the Alpi standard for building software with Claude Code, adapted to a single-repo personal project. Read them before touching anything.

### The Isomorphism Rule

```
1 Phase (in GAMEPLAN.md)  =  1 Branch  =  1 PR into main
```

- Every phase in `GAMEPLAN.md` maps to exactly one branch: `feature/<NN>-<slug>` (e.g. `feature/03-llm-tools`).
- Every branch maps to exactly one PR, against `main`.
- No branch serves two phases. No phase spans two branches. No PR covers two phases.
- If a phase turns out to be too big, split it into two phases *in GAMEPLAN.md first*, then branch. Prefer small phases. An M that could be two S's should be two S's.

### Workflow per phase

1. **Pre-flight.** `git checkout main && git pull`. Confirm the phase's *Open questions* in `GAMEPLAN.md` are all answered. If any is unanswered, **STOP and ask** — do not guess and do not start.
2. **Branch.** `git checkout -b feature/<NN>-<slug>`.
3. **Read before writing.** Read every file you are about to touch and its neighbours. Match existing style. Never write against an imagined codebase.
4. **Work step by step.** Each task in the phase is one commit. After each task: run the tests, tick the box in `GAMEPLAN.md`, commit both the code and `GAMEPLAN.md` **in the same commit**. A commit that completes a task without ticking its box is wrong. A tick without the work is worse.
5. **Tests are part of the phase, not an afterthought.** Every acceptance criterion in the phase maps to at least one test. Tests must be meaningful, not "it doesn't crash".
6. **Self-review.** `git diff main..HEAD --stat`, `git log --oneline main..HEAD`, `ruff check .`, `ruff format --check .`, `pytest`. No unrelated changes in the diff.
7. **Open the PR** with `gh pr create --base main` using the PR template below, and end the turn with the **reading list**: every changed file sorted into *read thoroughly* / *skim* / *skip*, in the order to read them, one line each saying what to look for or why it can be skipped. The list goes in the PR body and in the final message. Then **STOP.** The operator reads and validates every PR manually. You never merge, never push to `main`, never force-push, never open the next phase's branch until the current PR is merged (or the operator explicitly says to proceed).
8. **Codex will review your work.** Address every Codex comment with a follow-up commit on the same branch (tick nothing new; fix, then reply in the PR thread). If you disagree with a comment, say why in the thread — don't silently ignore it.

### Commits

Atomic, conventional, one logical change each. Scope is the phase number.

```
feat(p03): add score_task tool for the LLM
test(p03): cover score_task with a fake Habitica client
fix(p03): handle 401 from Habitica without crashing the turn
chore(p00): pin polars and add ruff config
docs(p06): describe the consistency metrics in README
```

`GAMEPLAN.md` changes ride along in the commit that does the work — never in a separate "update gameplan" commit.

### PR template

```markdown
## Relates to Phase <NN> — <title>

### Summary
<2-3 sentences: what this phase adds and why>

### Changes
- `path/to/file` — what changed
- ...

### Tests
- N unit tests, N integration tests, all passing
- How to run: `pytest`

### Deviations from GAMEPLAN
<none, or what changed and why>

### Notes for reviewer
<anything you're unsure about, anything you did outside the code (live runs, account changes)>

### Reading list
**Read thoroughly** — `path` — what to look for
**Skim** — `path` — what it is
**Skip** — `path` — why (migration mirrors the model, strings, fake, …)
```

Say `Relates to`, never `Closes` / `Fixes` / `Resolves`: those keywords make GitHub auto-close an issue on merge, and closing stays a human decision. If GitHub issues are ever used for phases, reference them the same way: `Relates to #N`.

### When something goes wrong

- Blocked by an unanswered question, a missing credential, an API that doesn't do what the spec assumes → **STOP and ask.** Write down what you found in the phase's *Notes & deviations* in `GAMEPLAN.md` and end the turn.
- Small, necessary deviation (a renamed function, an extra helper) → do it, record it under *Notes & deviations*, mention it in the PR body.
- Do not "fix" things outside the phase's scope. Note them under *Backlog / found along the way* at the bottom of `GAMEPLAN.md` instead.

### Tooling choices (why CLI and raw APIs, not MCP)

Gourze uses the `gh` CLI instead of the GitHub MCP because a large MCP tool schema taxes every call and is harder to script deterministically. Same principle here:

- GitHub operations: `gh` CLI, driven from the shell.
- Habitica, Google Calendar, OpenRouter (LLM, ASR, TTS): **raw REST APIs** wrapped in small, hand-written Python clients, exposed to the LLM as plain tool functions where relevant. Google has an official Calendar MCP (developer preview), but for a single-user bot a thin REST client is smaller and easier to test. **Never** pull in a third-party MCP server. If an official MCP turns out to be clearly better than the raw API for some integration, propose it under *Open questions* first.

### Worktrees

Phases are sequential in this project, so a plain branch is enough. If the operator asks to run two phases in parallel, use `git worktree add ../.worktrees/feature-<NN>-<slug> -b feature/<NN>-<slug> main` — never two phases in one checkout.

---

## Code style

Write Python that a human wrote and another human can review in ten minutes.

- Simple over clever. Functions over classes unless state genuinely needs to live somewhere. No metaclasses, no decorators that hide control flow, no `**kwargs` passthrough soup.
- Small functions with obvious names. A function does one thing, and its name says which.
- Type hints everywhere. `dataclass` or Pydantic for data that crosses a boundary. Plain dicts do not cross boundaries.
- Docstrings only where a name isn't enough. Comments explain *why*, never *what*.
- Explicit errors. Catch what you can handle; let the rest propagate with context. Never swallow exceptions silently.
- No premature abstraction. Two similar lines are fine. Three similar blocks might be a function. A "plugin architecture" is never the answer.
- Async where I/O happens (HTTP, DB, Telegram). Sync everywhere else (analytics, formatting, charts).
- Every external boundary (Habitica, OpenRouter, Telegram, Google) is one module with one client class and no business logic in it. Business logic lives in `core/`, `analytics/`, `coach/` and never imports a bot library.
- Config from environment via `pydantic-settings`. No secrets in code, no secrets in git. `.env.example` lists every variable.
- Logging with the standard `logging` module, structured enough to grep. Log every outbound API call at DEBUG, every failure at WARNING or above.
- `ruff` for lint and format (line length 100). `pytest` for tests, `pytest-asyncio` for async tests. Tests use fakes and recorded fixtures, never the live network.

---

## Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | `uv` for env + lockfile |
| Web/API | FastAPI | health, Habitica webhook receiver, Telegram webhook receiver |
| DB | SQLite | one file, `data/analytica.db`, WAL mode. SQLAlchemy 2 (async, `aiosqlite`) + Alembic (batch mode). Backup = copy the file. |
| Analytics | polars | all stats. Load rows via SQLAlchemy, build `pl.DataFrame` from them — no extra connector. |
| Charts | seaborn/matplotlib → PNG for Telegram; `plotext` 5.x for the terminal | one `ChartData` object, two renderers in `analytics/charts.py`. plotext is pinned `<6`: 6 is a rewrite with a different API |
| Chat adapter | aiogram 3 | webhook mode on the VPS, polling mode for local dev; isolated in `adapters/telegram/` |
| LLM | OpenRouter | the official `openrouter` Python SDK (`OpenRouter(...).chat.send_async`), with a bounded retry policy — its default retries for up to an hour. Two models: `LLM_STRONG_MODEL=z-ai/glm-5.3` (default) and `LLM_CHEAP_MODEL=deepseek/deepseek-v4-flash`; `/model` switches. A failed call falls back to the other one, then `LLM_FALLBACK_MODELS`. Verify both ids on openrouter.ai/models at build time. |
| ASR (speech → text) | Qwen3-ASR 1.7B via OpenRouter, then `ASR_FALLBACK_MODELS` | `POST /audio/transcriptions` with base64 `input_audio` (Ogg/Opus straight from Telegram); auto language detection; the endpoint has no vocabulary field, so task names and the glossary bias the cleanup pass instead (verified 2026-09-14). One key for everything. |
| TTS (text → speech) | Fish Audio S2.1 Pro via OpenRouter | `POST /audio/speech`, single voice for all languages: Selene, Fish reference id `b347db033a6549378b48d00acb0d06cd` passed as `voice` (verified 2026-09-14). Output is mp3 (`mp3` or `pcm` only); Telegram sends mp3 as a voice note, so nothing is transcoded. |
| Scheduler | APScheduler (AsyncIOScheduler) | in-process; jobs: snapshots, day-close + cron, reminders, gear, calendar sync, weekly report |
| HTTP | httpx | everywhere |
| TLS / reverse proxy | the operator's existing nginx + certbot | the app binds to `127.0.0.1:8000` only; nginx proxies and terminates TLS. No Caddy, nothing else on 80/443. |
| Frontend | — | PNG charts first. A Svelte dashboard is a "maybe, later" (GAMEPLAN phase 10). Don't build towards it; don't block it either (keep analytics functions returning plain data). |
| Rust / Capacitor | — | Not applicable. polars is already Rust; Telegram is the mobile client. |

Single process: FastAPI app whose lifespan starts the Telegram adapter and the scheduler. `uv run` locally in polling mode; Docker on the VPS behind nginx in webhook mode (phase 05).

---

## Architecture

```
analytica/
├── CLAUDE.md
├── GAMEPLAN.md
├── README.md
├── pyproject.toml
├── .env.example
├── alembic/                     # migrations
├── data/                        # analytica.db, content cache, voice temp files (gitignored)
├── deploy/                      # Dockerfile, docker-compose.yml, nginx-analytica.conf, backup.sh
├── app/
│   ├── main.py                  # FastAPI app + lifespan (bot, scheduler)
│   ├── config.py                # pydantic-settings
│   ├── db/
│   │   ├── engine.py
│   │   └── models.py            # SQLAlchemy models (one file until it hurts)
│   ├── habitica/
│   │   ├── client.py            # thin async REST client, rate-limit aware
│   │   └── types.py             # dataclasses for user stats, task, gear, challenge
│   ├── collector/
│   │   ├── snapshots.py         # pull user + tasks, write snapshot rows, diff → events
│   │   ├── day_close.py         # final snapshot → daily_records → trigger cron
│   │   ├── backfill.py          # one-shot import of task.history on first run
│   │   └── webhooks.py          # FastAPI route for Habitica webhooks
│   ├── core/                    # platform-agnostic harness — THE product
│   │   ├── harness.py           # text_message_received(), voice_message_received(), button_pressed(), command_received()
│   │   ├── replies.py           # Reply, Button, ChartData dataclasses
│   │   ├── state.py             # per-user conversation state (pending transcription, pending confirm)
│   │   ├── turn.py              # Turn: session, user settings, clients — what a handler receives
│   │   ├── commands.py          # /language /model /tts /glossary /new /session /aspirations /stats /graph /report /remind /gear /skills /target /challenges /plan /sync
│   │   ├── day_turn.py          # "still unticked: X, Y?" before our cron; the tick / nothing-else buttons
│   │   ├── sessions.py          # open/close/replay sessions, summaries, token budget
│   │   ├── conversation.py      # a free-text turn: session → prompt → tool loop → Reply; confirm/cancel buttons
│   │   ├── tasks.py             # tasks as our DB knows them; short ids for the model, resolved by prefix
│   │   ├── skills.py            # the class skills the user can cast now, from content + stats (F12)
│   │   ├── outbound.py          # send_to_user() hook for scheduled messages
│   │   └── i18n.py              # fixed strings in en/ru/fr
│   ├── llm/
│   │   ├── client.py            # OpenRouter chat completions + usage telemetry
│   │   ├── tools.py             # tool schemas + dispatch to habitica/analytics/calendar functions
│   │   ├── prompts.py           # system prompt builder (language, current stats, task list)
│   │   └── models.py            # strong / cheap registry
│   ├── speech/
│   │   ├── asr.py               # transcribe(audio, mime) -> Transcription, via OpenRouter
│   │   └── tts.py               # speak(text) -> bytes (mp3), via OpenRouter
│   ├── analytics/
│   │   ├── frames.py            # load tables into polars DataFrames
│   │   ├── thresholds.py        # every tunable number, in one place
│   │   ├── dailies.py           # per-daily completion, streaks, breaks, flags
│   │   ├── habits.py            # ups/downs, weekly regularity, "should be a daily" detection
│   │   ├── todos.py
│   │   ├── deadlines.py         # lead time, completion offset, start fraction, last-minute flags
│   │   ├── lifecycle.py         # stage per task, transition proposals (F7e)
│   │   ├── stats_series.py      # gp/hp/mp/exp/lvl over time
│   │   └── charts.py            # ChartData → PNG (seaborn) or terminal (plotext)
│   ├── coach/
│   │   ├── advice.py            # findings (JSON) → LLM → advice text in the right language
│   │   └── aspirations.py       # planning-session prompt, create_plan
│   ├── jobs/
│   │   ├── scheduler.py
│   │   ├── reminders.py         # 21:00 "you still have N dailies"
│   │   ├── gear.py              # INT / PER / CON switching around day start
│   │   ├── new_day.py           # message after cron: damage, mana, dailies due
│   │   ├── weekly_targets.py    # count ups per week, complete the Sunday daily when met
│   │   └── weekly_report.py     # Sunday evening coach report
│   ├── calendar/
│   │   ├── client.py            # Google Calendar REST: events, freebusy, patch title
│   │   ├── sync.py              # (done) isomorphism, both directions
│   │   └── planner.py           # todos → free slots → proposed events
│   └── adapters/
│       ├── telegram/
│       │   ├── bot.py           # aiogram wiring → calls core.harness, renders Reply
│       │   └── render.py        # text, inline keyboard, photo, voice note
│       └── cli.py               # `uv run python -m app.adapters.cli` — REPL with terminal charts
├── scripts/
│   ├── dev.sh                   # migrate + run locally (polling mode)
│   ├── record_fixture.py
│   ├── google_auth.py           # one-time OAuth flow
│   ├── register_webhook.py      # register Habitica webhook
│   └── snapshot_now.py
└── tests/
    ├── fakes/                   # FakeHabiticaClient, FakeLlm, FakeAsr, FakeTts, FakeCalendar
    ├── fixtures/                # recorded JSON from real APIs (scrubbed)
    └── ...
```

### The platform-agnostic core

`app/core/harness.py` is the product. The Telegram adapter is a translator, nothing more.

```python
# core/replies.py
@dataclass
class Button:
    label: str
    action: str            # e.g. "confirm_transcription"
    payload: dict = field(default_factory=dict)

@dataclass
class ChartData:           # adapters decide how to draw it
    title: str
    x: list[date]
    series: dict[str, list[float]]
    kind: str = "line"     # "line" | "bar" | "heatmap"

@dataclass
class Reply:
    text: str
    buttons: list[Button] = field(default_factory=list)
    chart: ChartData | None = None
    voice: bytes | None = None     # mp3 audio, when TTS is on for this reply

# core/harness.py — the external clients live on the dataclass so adapters and tests inject fakes
@dataclass
class Harness:
    habitica: HabiticaClient
    llm: LlmClient | None = None   # None when OPENROUTER_API_KEY is unset: commands work, free text is refused
    async def text_message_received(self, user: User, text: str) -> Reply: ...
    async def voice_message_received(self, user: User, audio: bytes, mime_type: str) -> Reply: ...
    async def button_pressed(self, user: User, action: str, payload: dict) -> Reply: ...
    async def command_received(self, user: User, command: str, args: str) -> Reply: ...
```

Rules:

- `core/`, `llm/`, `analytics/`, `coach/`, `speech/`, `calendar/` never import `aiogram` or anything Telegram-specific.
- The adapter maps Telegram updates → these four functions, and `Reply` → Telegram messages: text, inline keyboard, chart as a PNG photo, voice as a voice note.
- The CLI adapter proves the point: the whole bot must be usable from a terminal with zero Telegram. Charts render in the terminal with `plotext`; voice replies are written to `data/voice/` and played with `ffplay`/`afplay` if one is on `PATH`, otherwise the path is printed.
- Conversation state lives in the DB (`conversation_state` table), keyed by user, not in bot-library memory.

### Users and access

Single user per deployment. Still: every table has a `user_id`, and the Telegram adapter checks an allowlist (`TELEGRAM_ALLOWED_USER_IDS`) and ignores everyone else. Don't build multi-user; don't paint it into a corner either — other people will run their own copy.

### Habitica day, timezone, and cron — read this twice

Habitica's "day" does not start at midnight. `GET /user` returns `preferences.dayStart` (hour, 0–23; the operator's is 11) and `preferences.timezoneOffset` (minutes). **Cron** — the moment Habitica applies damage for missed dailies, regenerates mana, resets dailies — runs **only on `POST /cron`**. No read and no score triggers it (Habitica's server source, `libs/cron.js` + `routes.js`, verified 2026-09-18; it has been so since the *Record Yesterday's Dailies* feature). After `dayStart`, `GET /user` reports `needsCron: true` and everything keeps working against the day that is ending: that is the window in which the app lists yesterday's unticked dailies, scores what you tick with the ordinary score endpoint, and only then posts `/cron`. The collector therefore never disturbs anything; the bot's own cron is the only thing that must wait. So the bot does what the app does, on a schedule:

```
dayStart − 30 min   ask: "still unticked: X, Y — did you do them?" — one button per due, unticked
                    daily (those with `yesterDaily: false` are left out, as in the app) and
                    *Nothing else*; a pressed daily is scored up and counts for the ending day.
                    Same moment, gear job: incomplete due dailies? → equip CON set, else → INT set
dayStart − 2 min    close_day: final snapshot, write daily_records for the ending day,
                    complete any weekly-target daily whose target was met (F7c)
dayStart + 1 min    turn_day: daily_records again as they stand now, POST /cron (with the right
                    gear on), snapshot again, equip PER set, send the "new day" message: HP lost,
                    mana now, dailies due today — unless the question is still unanswered, in
                    which case our cron waits until the answer or dayStart + CRON_GRACE_MIN (90)
rest of the day     PER stays on; snapshots every 30 min; webhooks in real time
```

`user_settings.cron_hold_until` carries the question's state: `None` = not asked for this day turn, a future instant = asked and waiting, a past instant = answered. A restart after day start does not fire cron blind: `run_cron_job` asks first if nobody did. If the operator opens the app meanwhile and presses *Start my day*, Habitica's cron runs there; ours is a no-op then and the buttons answer "too late".

The bot owns the reset. The operator never needs to open the app for it, no streak is lost for a daily that was done and not ticked, and no data window is lost. The before/after snapshots around `POST /cron` are themselves telemetry: damage taken, mana regenerated, streaks that broke.

All Habitica-day arithmetic uses `dayStart` + `timezoneOffset` from the API. Human clock times (reminder, weekly report) use `TIMEZONE` from config (`Europe/Zurich`).

---

## Feature specs

### F1 — Message flow (text and voice)

**Text.** User sends a text message → `text_message_received` → LLM turn with tools → reply in the active language.

**Voice.** User sends a voice message →
1. Adapter downloads the audio, calls `voice_message_received(user, bytes, "audio/ogg")`.
2. Core transcribes (ASR) with bias context, then runs a **cleanup pass** with the cheap model: fix punctuation, fix terms against the glossary and the task list, do not change meaning, return the text only. Both raw and cleaned text are stored. It stores `conversation_state = awaiting_confirmation` with the cleaned transcript and replies with it plus two buttons: **✅ Correct** and **✏️ Amend**.
3. **Correct** → the transcript is processed exactly as a text message.
4. **Amend** → core sets `conversation_state = awaiting_amendment` and asks what's wrong. The next text message is the amendment. The LLM gets one user message shaped like:
   ```
   Voice transcription: "<transcript>"
   User's correction: "<amendment>"
   ```
   and proceeds.
5. Any other message while awaiting confirmation cancels the pending transcript (say so briefly).

Transcripts are stored (`transcriptions` table: raw, cleaned, accepted) so both passes can be measured later.

**Glossary.** `asr_glossary(user_id, term)` holds the words the models get wrong for you. It feeds two places: the ASR bias context (together with task names, "Habitica", "daily", "habit", "todo", stat names, command names) and the cleanup prompt. `/glossary`, `/glossary add <term>`, `/glossary remove <term>`. When you amend a transcript, the bot diffs the cleaned text against your amendment; words that appear only in your version are proposed as glossary terms with one button (**➕ Add "Vergil"**).

**Voice out.** Setting `tts` per user: `off` (default) or `on`. `/tts on|off`. Speaking to the bot does not make the bot speak; only the setting does. When on, the text is still sent (Telegram: text message + voice note). One voice for all languages (`TTS_VOICE`, Selene). Long replies (> 600 chars) or replies with charts are text-only regardless. Browsing the voice library (`/voices`) is a later feature.

### F1b — Sessions and context

The model never gets the whole chat history; it gets a **session**.

- `sessions(id, user_id, kind, topic, started_at, last_activity_at, ended_at, summary, collapsed_summary, collapsed_through_id)`, `kind ∈ {quick, planning}`. Each `messages` row that belongs to a session carries its `session_id` and the LLM-formatted message (role, content, tool calls) in `llm`, so a session can be replayed into a prompt; tool calls and tool results are rows of `kind=tool`.
- A message arrives → if the user's open session is within its idle limit, append to it; otherwise close it (below) and open a new `quick` one. Idle limits: `quick` 15 min, `planning` 120 min (config).
- **Planning sessions** are for long conversations — designing the tasks for a new aspiration, reworking a week. Opened by the model via the `start_planning_session(topic)` tool when it recognises the intent, or by the user (`/session planning <topic>`). Closed by the model via `end_session()` once the plan is committed, by the user (`/new`), or by the idle limit.
- **Closing a session**: the cheap model writes a ≤ 3-line summary ("Discussed learning Latin; agreed on habit *Latin vocab 10 min* and todo *Buy Wheelock*; grammar deferred"), stored on the row. The next system prompts include the last 3 summaries under "Recent context", so continuity survives the reset without carrying the transcript.
- **Token budget**: if a session's replayed turns exceed `SESSION_TOKEN_BUDGET` (24k), the oldest half is collapsed into a running summary by the cheap model. Tool results older than the current turn are replaced by a one-line stub.
- `/new` closes the current session immediately. `/session` shows kind, topic, age.

### F2 — Language

- `/language en|ru|fr` sets the reply language (stored per user, default `en`).
- The system prompt states the active language. If the user's message explicitly asks for another language ("reply to me in French", "ответь по-русски"), the LLM obeys for **that reply only**; the stored setting doesn't change.
- Fixed bot strings (button labels, command help, reminders) come from `core/i18n.py` in all three languages. Free-form text comes from the LLM.
- **Habitica content is always English.** Task titles created via the bot are English regardless of input language (the LLM translates). When the user refers to a task in Russian or French, the LLM matches it against the English task list it is given in the prompt. A fuzzy-match fallback (`rapidfuzz`) is acceptable if the model proves unreliable.

### F3 — Habitica client

Thin async client over `https://habitica.com/api/v3`. Required headers on every call: `x-api-user`, `x-api-key`, `x-client: <user-id>-HabiticaAnalytica`. Respect the rate limit (30 requests/minute; read `X-RateLimit-Remaining` and sleep if it hits zero). No retries on 4xx; one retry with backoff on 5xx.

| Purpose | Call |
|---|---|
| User stats, gear, preferences (`dayStart`, `timezoneOffset`) | `GET /user` |
| All tasks | `GET /tasks/user` (+ `?type=completedTodos`) |
| Score a task | `POST /tasks/{id}/score/{up\|down}` |
| Create / update / delete a task | `POST /tasks/user`, `PUT` / `DELETE /tasks/{id}` |
| Reorder a task within its list | `POST /tasks/{id}/move/to/{position}` (0 = top, −1 = bottom) |
| Equip an item | `POST /user/equip/equipped/{key}` |
| Run cron on purpose | `POST /cron` |
| Game content (gear stats) | `GET /content` (cached on disk, refreshed weekly) |
| Challenges | `GET /challenges/user`, `GET /challenges/{id}`, `POST /challenges/{id}/join` |
| Cast a class skill | `POST /user/class/cast/{key}?targetId={taskId}` (target only for task skills) |
| Webhooks | `POST /user/webhook` with `type: taskActivity` (scored, created, updated, deleted) |

Every response is parsed into dataclasses in `habitica/types.py`. **Raw JSON is always stored too** (`raw` column) so we never regret a field we didn't parse. Collect everything; decide what to use later.

### F4 — Telemetry collection

**Tables**

- `user_snapshots(id, user_id, taken_at, hp, mp, exp, gp, lvl, class, to_next_level, raw)`
- `tasks(id, user_id, habitica_id, type, text, notes, created_at, first_seen_at, last_seen_at, deleted_at, position, raw)` — every task we've ever seen; `position` is its index in its type's list as arranged in the app (from `tasksOrder`, and from the bot's own moves — a reorder is remembered, never snapshotted)
- `task_snapshots(id, task_id, taken_at, completed, is_due, streak, value, counter_up, counter_down, checklist_done, checklist_total, raw_hash)`
- `task_events(id, task_id, at, kind, direction, delta, source, raw)` — `kind ∈ {scored, created, updated, deleted, completed, uncompleted, checklist, started}`, `source ∈ {bot, snapshot_diff, webhook, backfill, calendar, reminder}`
- `daily_records(id, task_id, day, was_due, completed, source)` — one row per daily per Habitica day; what analytics reads
- `webhook_deliveries(id, received_at, type, raw_hash)` — everything Habitica sends us, untouched
- `raw_blobs(hash, json)` — content-addressed storage for the two tables above: each distinct document once, keyed by the sha256 of its canonical JSON, like a git blob. `store_blobs()` in `db/blobs.py`; the models expose `.raw` through the pointer. An unchanged task costs a pointer per snapshot, not a copy. `user_snapshots.raw` stays inline (the user object changes on every request).
- `llm_calls(id, user_id, at, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, tool_calls, ok, error)`
- `transcriptions(id, user_id, at, provider, model, raw_text, accepted_text, language, latency_ms)`
- `tts_calls(id, user_id, at, provider, model, chars, latency_ms)`
- `messages(id, user_id, at, direction, kind, text, session_id, llm)` — chat log; `session_id` and `llm` (the model-format message) only on rows that belong to a session
- `user_settings(user_id, language, model, tts, reminder_time, gear_int, gear_per, gear_con, last_reminder_day, cron_hold_until, dismissed_proposals, recent_suggestions)` — `cron_hold_until`: while the "still unticked" question is open, our cron waits until this instant at most (P05c)
- `weekly_targets(id, user_id, habit_task_id, daily_task_id, per_week, created_at)`, `weekly_target_weeks(id, target_id, week_start, count, met, sessions_json)`
- `task_durations(task_id, minutes, source)` — user-stated estimates for the planner
- `sessions(id, user_id, kind, topic, started_at, last_activity_at, ended_at, summary, collapsed_summary, collapsed_through_id)` — F1b; the last two hold the running summary once a session outgrows its token budget, and the id of the last message it stands for
- `asr_glossary(id, user_id, term, added_from, created_at)` — F1
- `aspirations(...)`, `aspiration_tasks(...)` — F7e
- `conversation_state(user_id, state, payload, updated_at)`
- `telegram_buttons(id, user_id, action, payload, created_at)` — adapter-only: Telegram callback data is capped at 64 bytes, so an inline button carries this row's id and the action/payload live here
- `calendar_links(id, task_id, event_id, calendar_id, done, last_sync_at)`

**Jobs**

- `snapshot_user` + `snapshot_tasks` every 30 min. Diff tasks against the previous snapshot → `task_events` with `source=snapshot_diff`.
- `ask_unticked` / `close_day` / `turn_day` as described in the day-start section above; only our own `POST /cron` waits for the answer, the snapshots do not.
- `backfill` on first run: import each daily's and habit's `history[]` (`date`, `isDue`, `completed`) into `daily_records` / `task_events` with `source=backfill`. Idempotent. Partial for non-subscribers; that's fine.
- `new_day_message` right after `run_cron`: HP lost overnight, current mana, dailies due today, one line (F8b).
- Webhooks: `POST /webhooks/habitica/{secret}` receives `taskActivity` events with timestamps → `task_events` with `source=webhook`, plus the raw row. Registered once by `scripts/register_webhook.py`. This gives second-resolution data; snapshots remain the safety net.

### F5 — LLM harness (OpenRouter)

- One `chat(messages, tools, model) -> LlmResult` function via the `openrouter` SDK. OpenRouter returns `usage.cost` on every response; every call is recorded in `llm_calls`.
- Registry: `strong` = `LLM_STRONG_MODEL`, `cheap` = `LLM_CHEAP_MODEL`. Default `strong`. `/model` shows both with calls / cost / avg latency from `llm_calls`; `/model cheap` switches for this user. The coach always uses `strong`.
- Tool-calling loop: max 6 tool rounds per turn, then force a text answer.
- System prompt (built per turn): role, active language + override rule, "Habitica content is English", a compact primer on how Habitica works (the model cannot look the game up and is not meant to; this is its whole knowledge of the mechanics), one-line stats, the skills the user can cast now (F12), compact task list (id, type, text, due/completed, streak). **Every derived number is computed in Python and handed to the model** — XP to the next level, MP left after a cast, days overdue — never left to the model's arithmetic.

**Tools exposed to the model**

| Tool | Does | Confirm? |
|---|---|---|
| `list_tasks(type?)` | tasks with ids | |
| `score_task(task_id, direction)` | complete a daily/todo, +/- a habit | |
| `create_task(type, text, notes?, checklist?, due_date?, difficulty?, repeat_days?)` | English text only; sub-items go in `checklist`, never in notes; `repeat_days` for dailies on chosen weekdays | |
| `update_task(task_id, text?, notes?, checklist?, due_date?, difficulty?)` | `checklist` replaces the list; unchanged items keep their tick | |
| `delete_task(task_id)` | | **yes** |
| `move_task(task_id, position)` | `top`, `bottom` or a slot number from 1 — the drag in the app | |
| `get_stats()` | hp/mp/exp/gp/lvl | |
| `get_consistency_report(window_days)` | findings JSON | |
| `plot_stat(stat, days)` | attaches a chart to the reply | |
| `convert_habit_to_daily(habit_id, repeat_days? / every_x?)` | | **yes** |
| `convert_daily_to_habit(task_id)` | demote a daily that isn't working | **yes** |
| `set_weekly_target(habit_id, per_week)`, `remove_weekly_target(habit_id)` | F7c | set: **yes** |
| `list_challenges(search?)`, `join_challenge(id)` | | join: **yes** |
| `cast_skill(skill, task_id?)` | F12 — cast one of the user's class skills; `task_id` for task-targeted ones | **yes** |
| `start_planning_session(topic)`, `end_session()` | F1b | |
| `create_plan(aspiration, items[])` | batch-create the agreed todos/habits/dailies for an aspiration, link them | **yes** (one confirm for the whole batch) |
| `propose_transition(task_id, to_stage)` | F7e — surfaces a lifecycle move with buttons | **yes** |
| `add_glossary_term(term)` | | |
| `get_deadline_report(window_days)` | F6 deadlines | |
| `mark_started(task_id)` | records the start signal for deadline analytics | |
| `set_task_duration(task_id, minutes)` | "this takes 10 minutes" — used by the calendar planner | |
| `list_calendar_events(days)`, `propose_schedule(days)`, `create_calendar_events(proposal_id)` | | create: **yes** |

**Destructive or externally visible actions always require a confirm button**, even when the user's message was explicit. The tool returns `needs_confirmation`; the core stores the pending action in `conversation_state` and shows **✅ Do it / ❌ Cancel**. The prompt is never trusted to enforce this.

### F6 — Analytics (statistics, not AI)

All in polars, reading `daily_records`, `task_events`, `user_snapshots`. Windows: 14 / 30 / 90 days. Every tunable number lives in `app/analytics/thresholds.py` — one small module of named constants, nothing else — so the definitions can be adjusted in one place once real data is in.

**Dailies** — per daily and window:
- `due_days`, `completed_days`, `misses = due − completed`, `completion_rate`
- streak list, `current_streak`, `max_streak`, `mean_streak`, `n_breaks`
- completion rate by weekday
- tiers, from the operator's definition "inconsistent = failing more than once per two weeks":
  - `solid`: `misses ≤ due_days / 14` (at most one miss per 14 due days), ≥ 14 due days
  - `inconsistent`: more misses than that, but `completion_rate ≥ 0.5`
  - `abandoned`: `completion_rate < 0.5`, ≥ 10 due days — the coach's advice here is never "try harder": it is to drop the daily or demote it to a habit (F7d). Nobody gets punished by a report.
  - `too_new`: < 7 due days
  Constants: `SOLID_MAX_MISSES_PER_14_DAYS = 1`, `ABANDONED_BELOW_RATE = 0.5`, `MIN_DUE_DAYS = 7`.

**Habits** — per positive habit:
- ups per day, per weekday; 30-vs-30 trend
- **"should be a daily" detection** (see F7b): weekly up-counts over the last 8 weeks; `median_per_week`; `stable = at least 6 of 8 weeks within ±1 of the median` and `median ≥ 2`; weekday regularity: the smallest set of weekdays that covers ≥ 75% of scorings; if that set has `≤ median + 1` days → propose a weekly daily on those days, else propose `every X days` with `X = round(7 / median)`.
  Constants: `HABIT_WEEKS = 8`, `HABIT_STABLE_WEEKS = 6`, `HABIT_MIN_MEDIAN = 2`, `HABIT_WEEKDAY_COVERAGE = 0.75`.

**Todos**: created vs completed per week, median age at completion, count overdue.

**Deadline behaviour** (`analytics/deadlines.py`) — for todos that had a due date and got completed:
- `lead_days = due − created`
- `completion_offset_days = completed − due` (negative = early)
- `first_activity`: the earliest of — an explicit **started** event (below), first checklist tick, first `task_events.updated` (notes or checklist change), or a bot-scheduled calendar slot the user confirmed working in (F11 check-in). Habitica has no "started" signal of its own, so we collect one:
  - **Explicit**: the user says "I'm starting on the essay" → tool `mark_started(task_id)` → `task_events(kind=started, source=bot)`. Also `/started <task>`.
  - **Asked**: the daily reminder (F8) lists due-dated todos that have no start signal yet and asks "Started any of these?" with one button per todo. Pressing it records `started` with `at = now` and a `started_via=reminder` note; the analytics treat asked and explicit starts the same.
  - `None` if nothing is known before completion.
- `start_fraction = (first_activity − created) / (due − created)`, in [0, 1]; `None` when unknown.
- Aggregates over the window: median `completion_offset`, share completed on the last day or late, median `start_fraction`, and the same grouped by tag and by `lead_days` bucket (< 3 days, 3–14, > 14) — to answer "do I wait till the end on everything, or on a specific subset?".
- Flags: `last_minute` (≥ 60% of todos completed on the due day or later), `no_start_signal` (≥ 70% have no first activity — the coach's answer is "split it into a checklist").
Constants: `LAST_MINUTE_SHARE = 0.6`, `NO_START_SHARE = 0.7`, `LEAD_BUCKETS = (3, 14)`.

**Stat series**: gp / hp / mp / exp / lvl over time, resampled daily; `ChartData` out; seaborn PNG or plotext terminal in.

### F7 — Coach

`coach/advice.py` takes the findings JSON (flagged dailies, habit-to-daily candidates, weekday patterns, trend) and asks the strong model for advice **about the flagged items only**. Rules in the prompt: concrete, ≤ 5 sentences per item, one suggestion and one question per item, no motivational filler, in the active language. Target tone: "You've completed *Vacuum the floors* 11 of the last 14 due days, usually missing on Mondays. Would moving it to Saturday help, or is the vacuum itself the problem?"

Beyond flagged items, each report may carry **one broader suggestion** — the genuinely AI part: the model sees the full task list, the tiers, and the recent history, and may point at a gap or a next step ("you log a lot of cardio; nothing about strength — want a twice-a-week lifting habit?"). One per report, clearly separated from the statistical findings, never repeated within 30 days (`user_settings.recent_suggestions`).

`/report [days]` runs analytics + coach on demand. `jobs/weekly_report.py` sends it every Sunday at 19:00 (`TIMEZONE`).

#### F7b — Habit → daily proposals

When `habits.py` flags a habit as stable (F6), the coach includes: "You've logged *Exercise* 3 times a week for 7 of the last 8 weeks, mostly Mon/Wed/Fri. Want me to turn it into a daily on those days?" with buttons **Yes, Mon/Wed/Fri**, **Yes, every 2 days**, **No**. On yes: `convert_habit_to_daily` creates the daily (`frequency: weekly` + `repeat`, or `frequency: daily` + `everyX`), then *offers* to delete the old habit (second confirm; keeping it is fine). Never deletes without the second confirm. A declined proposal is remembered (`user_settings.dismissed_proposals`) and not repeated for 60 days.

#### F7c — Weekly targets ("3 times a week, any days")

Habitica has no daily type for "N times a week on any days": a daily completed on a non-due day is reset again at the next day start, so three "Workout 1/2/3" dailies due on Sunday would need to be clicked again on Sunday. We build the missing type from our own telemetry:

- `/target Workout 3` (or the `set_weekly_target` tool): ensures a habit **Workout** exists (creates it if not), creates one daily **Workout: 3/week** with `frequency: weekly`, `repeat: {su: true}` only, and records `weekly_targets(habit_task_id, daily_task_id, per_week)`.
- The target daily carries a **checklist** with one item per required session (`Workout 1`, `Workout 2`, `Workout 3`), created via `POST /tasks/{id}/checklist`. After each habit up the bot ticks the next unticked item (`POST /tasks/{id}/checklist/{itemId}/score`) and rewrites the **notes**: `2/3 this week (Tue, Thu) — tracked by Analytica`. Progress is visible in the app either way. Whether cron resets a weekly daily's checklist on non-due days is unknown at spec time — the operator will report; if it does, the bot re-ticks from its own count at every snapshot, and the notes remain the reliable display.
- The bot counts the habit's ups per Habitica week (Mon–Sun, days bounded by `dayStart`) from `task_events`. Our DB is the truth; the checklist and notes are its mirror. Every checklist tick (ours or the user's, seen via webhook or snapshot diff) is recorded as `task_events(kind=checklist)`, and each week closes into `weekly_target_weeks(target_id, week_start, count, met, sessions_json)` so the history of hits and misses is analysable.
- On Saturday's reminder, if the count is short, say so: "Workout: 2 of 3 this week."
- In `close_day` on Sunday (i.e. just before Monday's reset): if `ups ≥ per_week`, the bot completes the Sunday daily via `score_task(up)`. If not, it leaves it, and Habitica applies its own damage at cron. No hacks on HP.
- `/target` lists targets with this week's counts. `/target Workout off` removes the daily and the row, keeps the habit.

#### F7d — Daily → habit demotion

The mirror of F7b for `abandoned` dailies: the coach offers "drop it" or "make it a habit"; `convert_daily_to_habit` creates a positive habit with the same text and notes, then offers to delete the daily (second confirm; keeping it is fine).

#### F7e — The lifecycle, as rules

F7b, F7c and F7d are instances of one thing. Every task the bot knows sits at a stage; the statistics propose moves; the model explains; buttons execute.

| From | To | Proposed when (statistics) | Executed by |
|---|---|---|---|
| aspiration | first todos + habits | the planning session ends with an agreed list | `create_plan` |
| todo | habit | a todo linked to an aspiration is completed, and it reads like a repeatable action ("first gym session") | `create_task(habit)` |
| habit | daily | stable weekly frequency (F6 habit rules) | `convert_habit_to_daily` |
| daily | retired | `solid` for ≥ 90 due days | `retire_daily` — the daily is deleted; the aspiration keeps the record ("second nature since …") |
| daily | habit | `abandoned` | `convert_daily_to_habit` |
| any | dropped | `abandoned` and the user declines demotion | delete, with confirm |

`aspirations(id, user_id, title, created_at, status, notes)` and `aspiration_tasks(aspiration_id, task_id, stage, since)` keep the record. `/aspirations` lists them with each one's tasks and stages; the weekly report includes one line per active aspiration. A declined proposal is not repeated for 60 days. Constants: `RETIRE_AFTER_SOLID_DAYS = 90`.

#### F7f — Aspirations (the entry point)

"I want to learn Latin." The model opens a planning session (F1b), asks what the user already knows, how much time per week, and what "done in three months" looks like — a few turns, not an interrogation — then proposes a starter set: usually one or two todos (concrete first steps), one habit (the repeatable practice), rarely a daily on day one. On agreement it calls `create_plan`; one confirm creates everything in Habitica (English titles), links them to the aspiration, and closes the session with a summary.

### F8 — Reminders

`/remind HH:MM` sets the daily reminder time (default 21:00, `TIMEZONE`); `/remind off`. Once per day: list due-and-incomplete dailies; on Saturdays any weekly target that is short (F7c); and the due-dated todos with no start signal, with a **Started** button each (F6 deadlines). Skip if nothing to say. `last_reminder_day` guards against double sends.

#### F8b — New-day message

Right after our cron at `dayStart + 1 min`: one message with HP lost overnight (from the snapshots before and after cron), mana now, and the dailies due today. This is the daily ritual anchor.

### F9 — Gear (three sets, cron-aware)

Three gear sets per user: `gear_int`, `gear_per`, `gear_con`, each `{slot: item_key}`.

- Default schedule (from the day-start section): CON-or-INT at `dayStart − 30 min`, PER right after our explicit cron.
- `/gear` shows the three sets and which is on. `/gear int auto` builds a set from *owned* gear by picking the highest-INT item per slot using `GET /content` (same for `per`, `con`). `/gear int <slot> <key>` sets one slot by hand. `/gear now int` equips immediately.
- Class bonuses are ignored for picking; the stat on the item is what counts.

### F10 — Challenges

`/challenges [search]` lists public challenges matching a keyword (study, pomodoro, learn). The model can describe one and join it after confirmation.

### F11 — Google Calendar

Direction: **Habitica → Calendar.** Habitica is the source of tasks; the calendar is where time gets allocated for them. The calendar is never a source of new Habitica tasks.

- **Auth**: OAuth2 installed-app flow once (`scripts/google_auth.py`), token at `GOOGLE_TOKEN_PATH`. Raw REST via httpx; `google-auth` only for token refresh. Main calendar only (`primary`) — no dedicated Habitica calendar.
- **Planning**: `/plan [days]` (or "schedule my todos"): `planner.py` takes open todos (those with a due date first, then by Habitica priority), reads `freebusy` for the next N days inside working hours (`PLAN_HOURS=09:00-19:00`), proposes one slot per todo — duration from `task_durations` if the user has said "this takes 10 minutes" (`set_task_duration`, also written into the task notes as `~10m`), else a `~Nm` marker in the notes, else `PLAN_SLOT_MINUTES=60` — presents the proposal, and creates the events only after **✅ Do it**. Each created event is recorded in `calendar_links`; the event description holds the Habitica task id; the task's notes get a line `📅 Thu 14:00–15:00 — scheduled by Analytica` via `PUT /tasks/{id}`. That scheduled start is also the first-activity proxy for the deadline analytics.
- **Done isomorphism, both directions**, via `sync.py` every 5 min:
  - Habitica task completed (bot, snapshot diff, webhook) and a link exists → event title gets ` (done)` appended (once).
  - Linked event's title ends with `(done)` and the task isn't completed → `score_task(up)`, `task_events` with `source=calendar`.
  - One-directional: undone → done only. The bot never removes ` (done)` and never un-completes anything. If a task is un-completed in Habitica, the link is marked `done=false` and left alone; the event keeps its suffix.
- **Post-slot check-in**: 10 minutes after a scheduled slot ends, the bot asks "Did you work on *Essay draft*?" with **Done** / **Partly** / **Not yet**. *Done* scores the task; *Partly* records `started` (if not already) and offers to reschedule the remainder; *Not yet* offers to reschedule. This is where "when do you actually start" data comes from for scheduled work.
- Never touches events that aren't in `calendar_links`.

### F12 — Class skills

Each class unlocks four skills between levels 11 and 14; casting one costs mana and, for the task-targeted ones (Burst of Flames, Brutal Smash, Pickpocket, Backstab), acts on one task. The skill table comes from `GET /content` (`spells.{warrior,wizard,rogue,healer}`: `mana`, `lvl`, `target ∈ {task, self, party, tasks}`), never from code; the API calls the mage `wizard`.

- `core/skills.py`: `available_skills(stats, content)` — the user's class, unlocked at their level, each with `castable = mp ≥ mana` computed locally.
- The system prompt lists them ("Burst of Flames — fireball, 10 MP, on a task") next to the stats line; the primer says what skills are.
- `cast_skill(skill, task_id?)` tool: name or key; a task skill without `task_id` is an error result, a non-task skill with one too. Always a confirm button (mana is spent, the app shows it), then a `collect(source=bot)` round: a cast changes stats and a task skill changes the task's value.
- `/skills` lists what can be cast now with costs. Party-targeted skills are cast as is — the party is Habitica's concern, not the bot's.

---

## Configuration (`.env.example`)

Grouped by when it is first needed. Everything without a value shown is a secret and must be filled in; everything with a value is a default that can be left alone.

Comments go on their own line, never after the value: dotenv keeps everything after `=` as the value, so an inline `# comment` ends up inside the setting and `cp .env.example .env` fails to boot. This block is `.env.example` verbatim; keep them identical.

```
# ---- core (phase 00) ----
DATABASE_URL=sqlite+aiosqlite:///./data/analytica.db
TIMEZONE=Europe/Zurich
LOG_LEVEL=INFO

# ---- Habitica (phase 01) ----
# Habitica -> Settings -> Site Data -> User ID
HABITICA_USER_ID=
# same page -> API Token (keep secret)
HABITICA_API_TOKEN=

# ---- Telegram (phase 02) ----
# from @BotFather
TELEGRAM_BOT_TOKEN=
# your numeric id, comma-separated for more; from @userinfobot
TELEGRAM_ALLOWED_USER_IDS=
# polling (local dev) | webhook (VPS)
TELEGRAM_MODE=polling

# ---- public URL + webhooks (phase 01 for Habitica, 02 for Telegram; real values in phase 05) ----
# https://bot.example.com - leave empty until the VPS has a domain
PUBLIC_BASE_URL=
# random string, e.g. `openssl rand -hex 24`; used in both webhook paths
WEBHOOK_SECRET=

# ---- models, all via OpenRouter (phases 03-04) ----
OPENROUTER_API_KEY=
LLM_STRONG_MODEL=z-ai/glm-5.3
LLM_CHEAP_MODEL=deepseek/deepseek-v4-flash
# tried in order when a call fails, after the other of the two models above; comma-separated, empty = none
LLM_FALLBACK_MODELS=google/gemini-3.5-flash
# speech -> text, POST /audio/transcriptions
ASR_MODEL=qwen/qwen3-asr-1.7b
# tried in order when ASR_MODEL fails (OpenRouter drops models without notice); empty = none
ASR_FALLBACK_MODELS=openai/gpt-4o-mini-transcribe,mistralai/voxtral-mini-transcribe-3-0
# text -> speech, POST /audio/speech; returns mp3
TTS_MODEL=fish-audio/s2.1-pro
# Fish Audio reference id passed as `voice`: Selene, used for all languages
TTS_VOICE=b347db033a6549378b48d00acb0d06cd

# ---- sessions and context (phase 03) ----
SESSION_IDLE_QUICK_MIN=15
SESSION_IDLE_PLANNING_MIN=120
SESSION_TOKEN_BUDGET=24000

# ---- schedule (phase 05) ----
REMINDER_TIME=21:00
WEEKLY_REPORT=sun 19:00
# minutes after day start that the bot waits for your "still unticked" answer before running cron
CRON_GRACE_MIN=90

# ---- Google Calendar (phase 09) ----
GOOGLE_CLIENT_SECRETS_PATH=./data/google_client_secrets.json
GOOGLE_TOKEN_PATH=./data/google_token.json
PLAN_HOURS=09:00-19:00
PLAN_SLOT_MINUTES=60
PLAN_CHECKIN_DELAY_MIN=10
```

`config.py` validates lazily by group: a missing Google path is only an error once phase 09 code runs, so the bot boots and works with the keys you have. `PUBLIC_BASE_URL` and `WEBHOOK_SECRET` are required when `TELEGRAM_MODE=webhook`.

---

## Testing conventions

- `tests/fakes/` holds `FakeHabiticaClient`, `FakeLlm`, `FakeAsr`, `FakeTts`, `FakeCalendar` with the same method signatures as the real clients. Core and analytics tests use fakes only.
- `tests/fixtures/` holds real (scrubbed) JSON for parser tests. Record once with `scripts/record_fixture.py`, commit the JSON.
- Analytics tests build small tables by hand and assert exact numbers (rate, streaks, tiers, habit stability).
- The CLI adapter doubles as the integration harness: `tests/test_cli_flow.py` drives a full text turn, a voice confirm/amend turn, and a confirm-button turn through the core with fakes.
- Tests run against a temporary SQLite file; no test touches the network.
- **Live runs against the operator's real Habitica account are read-only.** Commands, `/stats`, transcription and a look at what the model *would* say are fine; never press a confirm button, never send the model a message it could act on (score, create, change, delete), never call a writing endpoint — unless the operator asks for exactly that in the same turn. "It probably won't match a task" is not a reason. Whatever a live run did or touched goes in the PR under *Notes for reviewer*.

---

## Non-goals

Multi-user hosting (others run their own copy). KanbanFlow or any other task source besides Habitica. A web UI (unless phase 10 is ever green-lit). Habitica party/quest/chat features. Replacing Habitica's mechanics — Habitica is the game; this is the coach on the sidelines.
