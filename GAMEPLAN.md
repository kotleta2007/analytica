# Game Plan — Habitica Analytica

> **Repo**: `analytica` (the app is called Habitica Analytica)
> **Base branch**: `main`
> **Rule**: 1 phase = 1 branch `feature/<NN>-<slug>` = 1 PR into `main`. See `CLAUDE.md`.
> **Reviewer**: the operator reads every PR manually. Codex reviews too.

_How to use this file: work phases in order. Inside a phase, work tasks in order; each task is one commit that includes the code, its tests, and the ticked box below. Do not start a phase whose open questions are unanswered. When a phase's PR is open, STOP._

Legend: `[ ]` todo · `[x]` done · `[-]` dropped (say why in Notes)

---

## Decisions already made

Recorded so nobody re-asks them. Details in `CLAUDE.md`.

- SQLite, one file, WAL. No Postgres, no Docker for the DB.
- Runs on a VPS behind the operator's existing nginx + certbot (no Caddy, no second web server). Habitica webhooks on. Telegram webhook mode on the VPS, polling for local dev.
- LLM: `z-ai/glm-5.3` (strong, default), `deepseek/deepseek-v4-flash` (cheap). Verify ids on OpenRouter at build time.
- Everything model-related goes through OpenRouter with one key: LLM, ASR (Qwen3-ASR-Flash), TTS (Fish Audio S2.1 Pro, voice Selene `b347db033a6549378b48d00acb0d06cd` for all languages). Verify ids and the TTS voice parameter at build time; STOP and ask if OpenRouter can't do TTS with that voice.
- Voice replies: `/tts on|off`, default off. Speaking to the bot does not make it speak.
- Gear: INT before day start (max mana at cron), CON instead if dailies are incomplete, PER during the day. Because any API read after `dayStart` triggers the reset, the bot triggers it itself at `dayStart + 1` with the right gear on, then sends a "new day" message (damage, mana, dailies due). No manual mode; the operator never has to open the app for the reset.
- Timezone `Europe/Zurich`; Habitica `dayStart` = 11 and `timezoneOffset` are read from the API, not configured. Reminder 21:00. Weekly report Sunday 19:00.
- Calendar: Habitica → Calendar for tasks/events; `(done)` is isomorphic with completion in both directions, but only undone → done. Nothing is ever un-done by the bot. Main calendar. Free/busy drives scheduling of todos.
- KanbanFlow: dropped (API needs a paid board).
- Destructive or externally visible actions always get a confirm button.
- Charts: seaborn PNG in Telegram, `plotext` in the CLI. Svelte dashboard: maybe, later, phase 11.
- Consistency: "inconsistent" = more than one miss per 14 due days (and still ≥ 50% done); below 50% is "abandoned", and the advice for abandoned is drop-or-demote-to-habit, never "try harder". All thresholds in `app/analytics/thresholds.py`.
- Coach gives one broader, non-statistical suggestion per report too (cardio → try lifting), separate from findings.
- Habit → daily proposals: yes (F7b). After conversion, offer to delete the habit; keeping it is fine. Daily → habit demotion: yes (F7d).
- Weekly targets ("3× a week, any days") are built by the bot from telemetry, since Habitica has no such daily type (F7c). Progress mirrored into the daily's checklist and notes; the operator will report whether cron resets the checklist on non-due days.
- Start-of-work is collected by asking: `mark_started` tool, `/started`, a **Started** button in the reminder, and a post-slot check-in after scheduled calendar slots.
- Planner slot length: user-stated duration (`set_task_duration`, "this takes 10 minutes") > `~Nm` in notes > 60-minute default.
- Raw JSON stored for every external response.
- Deploy is P05, right after the bot becomes usable: telemetry then accumulates on the VPS while the analytics phases are still being built. Local dev stays on polling; the VPS runs webhooks behind the operator's existing nginx (no Caddy).
- The thesis (aspiration → todo → habit → daily → second nature) goes in the README and CLAUDE.md; the lifecycle rules are F7e.
- Voice input is two-pass: ASR with bias context, then a cheap-model cleanup; a user glossary feeds both; amendments propose glossary terms.
- Context is per session: quick (15 min idle) or planning (120 min); closed sessions leave a 3-line summary that the next prompts see.
- Task notes are written by the bot (`PUT /tasks/{id}`) to show weekly-target progress and scheduled slots inside the app.

## Open questions

| # | Question | Blocks | Answer | Status |
|---|---|---|---|---|
| Q1 | Does cron reset a weekly daily's checklist on a non-due day? The operator will observe once P07 runs; until then the bot re-ticks from its own count at every snapshot, which is correct either way. | — (informational) | | ⏳ |

---

## Phase 00 — Bootstrap

**Branch**: `feature/00-bootstrap`
**Goal**: an empty but runnable project with SQLite, config, lint, tests, and a health endpoint.

**Current Status:** Nothing exists. Notes and this plan only.

**Action Required:**

- [x] 1. `uv init`, `pyproject.toml` with deps: fastapi, uvicorn, httpx, pydantic-settings, sqlalchemy[asyncio], aiosqlite, alembic, apscheduler, polars, seaborn, matplotlib, plotext, aiogram, openai, google-auth, ruff, pytest, pytest-asyncio. Python 3.12. — `chore(p00): project skeleton with uv and deps`
- [x] 2. `ruff` config (line length 100), `pytest` config, `.gitignore` (`data/`, `.env`), `.env.example` with every variable from CLAUDE.md. — `chore(p00): lint, test and env config`
- [x] 3. `app/config.py` (pydantic-settings), `app/db/engine.py` (async engine, WAL pragma, session factory), `alembic/` initialised in batch mode with an empty first migration. — `feat(p00): config and sqlite engine`
- [x] 4. `app/main.py`: FastAPI app, `GET /health` returns `{"ok": true, "db": true}` after a `SELECT 1`. `scripts/dev.sh`: migrate + run with `TELEGRAM_MODE=polling`. — `feat(p00): fastapi app with health endpoint`
- [x] 5. `README.md`: what this is — including the thesis diagram (aspiration → todo → habit → daily → second nature) — how to run locally, how to test. Short. — `docs(p00): readme`

**Tests**

- [x] `tests/conftest.py` with a temp-file SQLite fixture and migrated schema; `tests/test_health.py`. — `test(p00): health endpoint on a temp db`
- [x] `tests/test_config.py` — settings load from env, missing required var raises clearly. — `test(p00): config loading`

**Acceptance**

```gherkin
Given a fresh clone and a filled .env
When I run scripts/dev.sh
Then data/analytica.db exists, migrations are applied, and GET /health returns ok
```

**Done when**: all boxes ticked · `ruff check` + `ruff format --check` + `pytest` green · PR open · STOP.

**Notes & deviations**:

- `app/db/models.py` is created in this phase, empty except for the `Base` declarative class, so `alembic/env.py` has a metadata object to autogenerate against. The tables themselves still arrive in P01.
- `ruff` excludes `*.md`: newer ruff formats Python code blocks inside Markdown, which would rewrite the code in `CLAUDE.md` and `GAMEPLAN.md`.
- `.env.example` puts each explanatory comment on its own line rather than inline after the value. dotenv keeps everything after `=` as the value, so the inline form CLAUDE.md originally showed makes `cp .env.example .env` fail to boot (`TELEGRAM_MODE` and `TELEGRAM_ALLOWED_USER_IDS` both reject it). Every variable and every comment is still there. CLAUDE.md's configuration block was amended by the operator's instruction to hold `.env.example` verbatim, so the two can no longer drift; `tests/test_config.py` loads the file to keep it loadable.

---

## Phase 01 — Habitica client, collector, webhooks, day-close

**Branch**: `feature/01-habitica-collector`
**Goal**: everything Habitica knows about the account lands in our DB, with raw JSON, on a schedule and in real time.

**Current Status:** `GET /health` only. No Habitica code.

**Action Required:**

- [x] 1. `app/habitica/types.py`: dataclasses `UserStats` (incl. `day_start`, `timezone_offset`), `Task` (with `history` entries), `GearItem`. Small `from_json` functions. — `feat(p01): habitica types`
- [x] 2. `app/habitica/client.py`: async client with required headers, rate-limit awareness (`X-RateLimit-Remaining`/`Reset`, sleep when exhausted), one retry on 5xx. Methods: `get_user`, `get_tasks`, `get_completed_todos`, `score_task`, `create_task`, `update_task`, `delete_task`, `equip`, `run_cron`, `get_content` (disk-cached), `create_webhook`, `list_webhooks`. — `feat(p01): habitica rest client`
- [x] 3. `scripts/record_fixture.py`: hit the real API once, scrub ids/tokens, write `tests/fixtures/habitica/user.json`, `tasks.json`, `content.json`. Commit the fixtures. — `chore(p01): record scrubbed habitica fixtures`
- [x] 4. Alembic migration + models: `user_snapshots`, `tasks`, `task_snapshots`, `task_events`, `daily_records`, `webhook_deliveries`, `user_settings`. Every table has `user_id`; every table that stores an API response has `raw`. — `feat(p01): telemetry tables`
- [x] 5. `app/collector/snapshots.py`: `snapshot_user()`, `snapshot_tasks()` (upsert `tasks`, insert `task_snapshots`, diff vs previous → `task_events` with `source=snapshot_diff`). — `feat(p01): snapshot collectors`
- [x] 6. `app/collector/day_close.py`: `close_day()` writes `daily_records` for the Habitica day ending now; `run_cron()` calls `POST /cron` then snapshots. Habitica-day math from `day_start` + `timezone_offset`. — `feat(p01): day close and explicit cron`
- [x] 7. `app/collector/backfill.py`: on first run, import each daily's and habit's `history[]` into `daily_records` / `task_events`, `source=backfill`. Idempotent. — `feat(p01): backfill from habitica task history`
- [x] 8. `app/collector/webhooks.py`: `POST /webhooks/habitica/{secret}`; wrong secret → 403; store `webhook_deliveries` raw; map `taskActivity` payloads to `task_events` with `source=webhook`. `scripts/register_webhook.py` registers `PUBLIC_BASE_URL/webhooks/habitica/<secret>` for scored/created/updated/deleted, skipping if already registered. **If `PUBLIC_BASE_URL` is empty it logs one line and exits 0 — do not stop and ask for a URL; the VPS does not exist until P05.** Until then the receiver is exercised by tests only and snapshots carry the load. — `feat(p01): habitica webhook receiver and registration`
- [x] 9. `app/jobs/scheduler.py`: APScheduler in the FastAPI lifespan. Jobs: snapshots every 30 min; `close_day` at `dayStart − 2 min`; `run_cron` at `dayStart + 1 min`. Times recomputed daily from the latest `user_snapshots.raw`. `scripts/snapshot_now.py` for manual runs. — `feat(p01): scheduled collection`

**Tests**

- [x] Parsers against fixtures: stats incl. dayStart/offset, dailies with history, gear. — `test(p01): habitica parsers`
- [x] Client: rate-limit sleep and 5xx retry via httpx `MockTransport`. — `test(p01): client rate limit and retry`
- [x] Snapshot diff: two fake task lists → expected `task_events` (completed, uncompleted, created, deleted, habit counter change). — `test(p01): snapshot diff events`
- [x] Day-close: correct `was_due`/`completed` rows; running twice doesn't duplicate; Habitica-day boundary at 11:00 Zurich handled across DST. — `test(p01): day close and day math`
- [x] Backfill idempotent. — `test(p01): backfill idempotent`
- [x] Webhook: sample `scored` payload → `task_events` row + raw row; bad secret → 403. — `test(p01): webhook handling`

**Acceptance**

```gherkin
Given valid Habitica credentials and a registered webhook
When I complete a task in the Habitica app
Then task_events has a webhook-sourced row within seconds
And after 11:00 Zurich there is one daily_records row per daily for the day that ended
And cron was triggered by us at 11:01, not by a random collector call
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**:

- Operator's `dayStart` is 11 (checked against the API), not 12; every derived clock time in this plan and in `CLAUDE.md` was moved back an hour.
- Every round writes the full `/user` object into `user_snapshots.raw` and one `task_snapshots` row per task, as specified. Task JSON and webhook payloads are content-addressed: `raw_blobs(hash, json)` stores each distinct document once (sha256 of its canonical JSON), and `task_snapshots.raw_hash` / `webhook_deliveries.raw_hash` point at it, the way git stores blobs. Every snapshot row still exists; an unchanged task costs a pointer. Measured on the operator's account: 90 tasks × 3 rounds = 270 rows, 90 blobs, 82 KB instead of ~246 KB. The user object changes on every request (`_v`, timestamps), so it stays a full inline copy; it is the dominant cost (~0.7 GB/year) by the operator's decision.
- `snapshot_tasks` also reads `?type=completedTodos`, otherwise a completed todo (which leaves `/tasks/user`) would look deleted. Habitica later purges old completed todos from that list too: such a todo gets `deleted_at` (it is gone) but no `deleted` event (the user did not delete it).
- Cron resets every daily to not-completed. `collect()` snapshots the user first, notices when `lastCron` moved, and then does not record those resets as `uncompleted` events; a daily un-ticked by hand between two ordinary snapshots still is one.
- Backfill reads what a daily's `history[]` actually means, checked against the recorded account: an entry with `completed: true` is a tick (value goes up) on its own Habitica day; an entry with `completed: false` written at a cron time is the damage for the day that just ended (a completed daily gets no cron entry); a `completed: false` entry at any other time is an un-tick and is ignored. Cron times come from `user.history.exp`, which has one entry per cron, so `backfill()` takes the client and fetches `/user` once. A cron miss wins over a tick for the same day (a tick synced late at cron time lands on the wrong day; the damage does not). Days with no entry stay unrecorded rather than guessed. Backfill also writes `completed` events for daily ticks (time of day is useful later) and never overwrites a `close_day` record.
- A webhook payload carries the full task, so the receiver also writes a `task_snapshots` row for it; the next scheduled snapshot then sees no change in the parsed fields and does not record the same completion twice. Deliveries for another user id or of a non-`taskActivity` type are stored raw and not processed.
- Habit `scored` events from snapshot diffs use the `counterUp`/`counterDown` deltas (one event per count); when the counters were reset by Habitica's daily/weekly/monthly counter reset, the sign of the value change decides one event. Webhooks (task 8) carry the exact events; the diff is the safety net.

---

## Phase 02 — Core harness, CLI adapter, Telegram adapter

**Branch**: `feature/02-harness-adapters`
**Goal**: talk to the bot. No LLM yet — commands, and a polite "not yet" for free text. Proves the platform-agnostic design before anything clever is added.

**Current Status:** Collector runs headless. No chat surface.

**Action Required:**

- [x] 1. `app/core/replies.py` (`Reply`, `Button`, `ChartData`), `app/core/state.py` (`conversation_state` table + get/set/clear), `messages` table. Migration. — `feat(p02): reply types, conversation state, message log`
- [x] 2. `app/core/i18n.py`: fixed strings for en/ru/fr. `t(key, lang)`. — `feat(p02): i18n strings`
- [x] 3. `app/core/commands.py` + `app/core/harness.py` with the four entry points and `core/outbound.py` (`send_to_user` hook the adapter registers). Commands: `/start`, `/help`, `/language`, `/stats`, `/sync`. Free text → `t("no_llm_yet")`. — `feat(p02): core harness and commands`
- [x] 4. `app/adapters/cli.py`: REPL. Renders `Reply` as text, numbered buttons (type the number to press), charts via `plotext`, voice to `data/voice/` (+ play if `ffplay`/`afplay` exists). — `feat(p02): cli adapter`
- [x] 5. `app/adapters/telegram/bot.py` + `render.py`: aiogram 3; `TELEGRAM_MODE=webhook` mounts `POST /webhooks/telegram/{secret}` on FastAPI and sets the webhook at startup; `polling` starts long polling in the lifespan. Allowlist. Maps message/command/callback → harness; renders text, inline keyboard, photo, voice note. — `feat(p02): telegram adapter with webhook and polling modes`

**Tests**

- [x] Harness: `/language fr` persists; `/help` in French; unknown command → i18n string; messages logged both directions. — `test(p02): commands and language`
- [x] CLI flow with `FakeHabiticaClient`: `/stats`. — `test(p02): cli flow`
- [x] Telegram render: `Reply` with buttons → `InlineKeyboardMarkup`; chart → photo; allowlist rejects unknown user; webhook path with wrong secret → 403. — `test(p02): telegram render, allowlist, webhook secret`

**Acceptance**

```gherkin
Given the bot is running and I am on the allowlist
When I send /language ru then /stats
Then the stats line comes back in Russian
And the same works in the CLI without Telegram
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**:

- The four entry points are methods of a `Harness` dataclass that holds the external clients (today: the Habitica client), not module-level functions: adapters and tests inject fakes without patching. `core/turn.py` carries what a handler receives (`Turn`: session, user, settings, client, clock, timezone); `User` is a one-field dataclass in `core/replies.py`.
- Every turn is one transaction that logs the inbound message, loads `user_settings` (created with defaults on first contact), answers, and logs the reply. A handler that raises still leaves the inbound message logged; the user gets a "something went wrong" line in their language and the traceback goes to the log. Habitica failures (`HabiticaError`, `httpx.HTTPError`) get their own line with the reason.
- `/stats` reads the latest `user_snapshots` row and only collects when it is older than 10 minutes (the reminder's rule from F8); `/sync` always runs one collection round. Both go through `collect()`, so a chat-triggered read is recorded like a scheduled one and, after `dayStart`, fires cron exactly as a scheduled snapshot would.
- An empty text handed to `text_message_received` means "the adapter got something it can't read" (a sticker, a photo); the reply is the `unsupported_message` line. Voice handling in the Telegram adapter is P04.
- Telegram inline buttons: callback data is capped at 64 bytes and a payload with one task UUID does not fit, so every rendered button is a `telegram_buttons` row and the callback carries its id (migration 0004). A button pressed for another user, or an id that does not exist, reaches the core as action `expired` and gets the `unknown_button` line. Rows are tiny and never cleaned up.
- The CLI keeps the last reply's buttons in memory, numbered; pressing one consumes the offer. It migrates the database on start so a fresh clone can talk to the bot without `scripts/dev.sh`.
- `analytics/charts.py` (`to_png`, `to_terminal`) lands here instead of P07 task 4: both adapters must render a `ChartData` to be tested. P07 adds the stat series and `/graph`. `plotext` is pinned `<6`: 6.x is a rewrite with a different API that still mis-renders dated series; `pyproject.toml` and the CLAUDE.md tech-stack row say so.
- Outbound messages (`core/outbound.py`) go to every allowlisted Telegram id; with no adapter registered (headless collector, scripts) the message is logged at WARNING and dropped, and `send_to_user` returns `False`.
- Two test-infrastructure fixes outside the phase's files: `alembic/env.py` passes `disable_existing_loggers=False` (tests migrate in-process, and `fileConfig`'s default switched off every app logger created before the upgrade, so `caplog` saw nothing); `tests/conftest.py` stops pydantic-settings from reading the developer's `.env` and clears credential variables for every test — the health test's lifespan had been reaching for Habitica since P01 on a machine with a filled `.env`, and would now start polling the real bot as well.
- `main.py` builds one `HabiticaClient` shared by the collector and the harness; the collector still closes it at shutdown.

---

## Phase 03 — LLM harness with Habitica tools

**Branch**: `feature/03-llm-tools`
**Goal**: free text does something. "I vacuumed the floors" completes the daily. "Ajoute une tâche: acheter du lait demain" creates an English todo due tomorrow.

**Current Status:** Free text is refused with an i18n message. No OpenRouter code.

**Action Required:**

- [x] 1. `app/llm/models.py` (`strong`/`cheap` registry from config), `app/llm/client.py`: `chat(messages, tools, model)` via the `openrouter` SDK, writes `llm_calls`. Migration. — `feat(p03): openrouter client with call telemetry`
- [x] 2. `app/llm/tools.py`: schemas + dispatch for `list_tasks`, `score_task`, `create_task`, `update_task`, `delete_task`, `get_stats`. Tools flagged as destructive return `needs_confirmation`; the core stores the pending action in `conversation_state` and shows **✅ Do it / ❌ Cancel**. — `feat(p03): habitica tools and confirm mechanism`
- [x] 3. `app/llm/prompts.py`: system prompt builder — role, active language + per-message override rule, "Habitica content is English; translate when creating, match semantically when the user speaks RU/FR", compact stats line, compact task list. — `feat(p03): system prompt builder`
- [x] 4. `app/core/sessions.py`: `sessions` table, `messages.session_id`; open/append/close with idle limits (quick 15 min); replay a session into prompt messages; close writes a ≤3-line summary with the cheap model; last 3 summaries go into the system prompt as "Recent context"; token budget collapses the oldest half. `/new`, `/session`. Migration. — `feat(p03): sessions and recent-context summaries`
- [x] 5. Harness: `text_message_received` → session → tool loop (max 6 rounds) → `Reply`. After any task-changing tool: `snapshot_tasks()` and a `task_events` row with `source=bot`. — `feat(p03): llm turn in the harness`
- [x] 6. `/model` shows strong/cheap with calls, cost, avg latency from `llm_calls`; `/model cheap|strong` switches for this user. — `feat(p03): model switching and comparison`

**Tests**

- [x] `FakeLlm` with scripted tool calls; assert dispatch, snapshot after change, `task_events` row, final text. — `test(p03): tool loop`
- [x] Prompt builder: override sentence present; task list compact; English-only rule present. — `test(p03): prompt builder`
- [x] `llm_calls` row on success and on error. — `test(p03): llm telemetry`
- [x] `delete_task` → confirm buttons; **Do it** deletes; **Cancel** or any other message cancels. — `test(p03): destructive confirm`
- [x] Sessions: a message 16 min after the last one starts a new session and the old one has a summary; the summary appears in the next prompt; `/new` closes immediately; over-budget session is collapsed. — `test(p03): session lifecycle`

**Acceptance**

```gherkin
Given a daily "Vacuum the floors" exists
When I send "j'ai passé l'aspirateur" with language=fr
Then the daily is scored up in Habitica
And the reply is in French
And llm_calls has a new row with model z-ai/glm-5.3 and cost > 0
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**:

- The client uses the official `openrouter` Python SDK (`openrouter>=1.1`, Speakeasy-generated) instead of the `openai` SDK pointed at OpenRouter, by the operator's choice during review; `openai` is dropped from the dependencies. Three things about it worth knowing: its default retry policy keeps retrying 5xx and connection errors with backoff for up to an *hour* (`max_elapsed_time=3600000`), so `client.py` passes its own (two retries, one second apart); it pins `pydantic<2.13`, which moved the lockfile from 2.13 to 2.12.5; and its response serializer ignores `include=`, so the replay message (role, content, tool calls) is built by hand rather than dumped from the model — otherwise `reasoning` and `refusal` fields would be stored and sent back. `usage.cost` is present on every response without any `usage.include` flag (checked live with both models).
- Both model ids verified on OpenRouter's public model list at build time (`z-ai/glm-5.3` $1.09/$3.43 per M tokens, `deepseek/deepseek-v4-flash` $0.05/$0.10; both support `tools`). One live call each with the phase's prompt and tool schemas: both return `score_task(task_id="d1a2b3c4", direction="up")` for "j'ai passé l'aspirateur" and both responses carry `usage.cost`. GLM also emits text alongside its tool call ("Bravo ! Je coche ta tâche…"); the loop keeps that content in the assistant message and the user gets the next round's text.
- Two modules not in the architecture list: `core/conversation.py` holds the free-text turn (session → prompt → tool loop → `Reply`) and the confirm/cancel buttons, so `harness.py` stays the thin entry-point file it was; `core/tasks.py` reads tasks back from our `tasks` table and owns the short ids.
- **The bot's `task_events` rows come from the collection round it runs right after a task-changing tool, with `source=bot`**, not from a hand-written row next to it. Writing both would record every completion twice (the diff sees the change the bot just made). `collect()` / `snapshot_tasks()` and the event builders take a `source`; the next scheduled round then sees no change, like after a webhook. One imprecision, bounded: a change the user made in the Habitica app within the ≤10 minutes since the previous snapshot is attributed to `bot` by that round.
- The turn refreshes stats and tasks through `collect_if_stale` (10 minutes, the rule `/stats` already had, now shared) before building the prompt; after `dayStart` a chat message therefore fires cron exactly as a scheduled snapshot would. Tools read tasks from our own `tasks` rows, not live — they are at most 10 minutes old and refreshed after every change — so a turn costs Habitica requests only when something changed. `get_stats` follows the same rule; `score_task` returns the post-score stats Habitica sends back, so "vacuumed, how much HP now?" is still answered live.
- Task ids are shown to the model as their first 8 characters (in the prompt, in tool results) and resolved by prefix; a full UUID also works. Saves ~700 tokens per call on a 90-task account and copies more reliably. Ambiguous prefix → error result asking for more characters (not seen on the fixture: no collisions in 36 tasks).
- `create_task` accepts `repeat_days` for dailies (`["mon","wed","fri"]` → `frequency: weekly` + `repeat`), otherwise a daily on chosen days could not be created in chat; the F7b conversion tool will reuse the mapping. `difficulty` maps to Habitica `priority` (0.1 / 1 / 1.5 / 2). A due date on a non-todo, a bad date, or a bad difficulty is an error result the model can correct.
- Every tool failure — unknown tool, unparsable JSON arguments, a missing task, a Habitica 4xx, Habitica unreachable — becomes `{"error": …}` content for the model; the turn never dies on a tool. An `LlmError` (network, HTTP status, empty choices) ends the turn with the `llm_error` line and still leaves the `llm_calls` row (`ok=false`).
- `llm_calls.model` records the id that was *requested*, not the one the provider reports (a routed variant would split `/model`'s aggregate). `ok=false` rows count as calls with no tokens and no cost.
- Sessions: the running summary lives on the row (`collapsed_summary`, `collapsed_through_id`) and message rows are never rewritten; the cut lands on the first user message at or after the midpoint so an assistant tool call is never separated from its results. Tokens are estimated as characters / 4 (no tokenizer dependency). Replay also drops an assistant tool call whose results were never logged (a crash between the two), since the API would refuse it. Tool calls and results are `messages` rows with `kind=tool`, `direction` out/in, the model-format message in `llm`; a confirm/cancel press is logged into the session as `[pressed: confirm]` so the model knows what happened. The summary and collapse prompts live in `sessions.py`; the summariser (cheap model) is injected, so `sessions.py` never imports the LLM client.
- Pending confirmation: the buttons carry a random token that must match the parked action, so ✅ from an earlier offer cannot fire a newer one. Any text message or command drops the parked action and says so in one line (`action_dropped`) before its own reply.
- `no_llm_yet` became `llm_not_configured`: the bot boots and every command works without `OPENROUTER_API_KEY`; only free text is refused, with the variable named.
- Found in the first live conversation: Habitica stores a todo's due date as *local midnight in UTC* (due 14/09 in Zurich is `2026-09-13T22:00:00Z`), and the prompt and `list_tasks` took `.date()` of the UTC value — every due date was announced one day early, and todos due today were listed as overdue. `core/tasks.py` now owns `due_day(task, tz)` (read in `TIMEZONE`) and `due_datetime(day, tz)` (what `create_task`/`update_task` send: local midnight in UTC, the same form the Habitica app writes). The deadline analytics in P07 must use the same helper.
- Dates are shown the European way, `31.12.2026`, in every language: `DATE_FORMAT` and `format_day` / `format_moment` / `parse_day` in `core/i18n.py`. The model copies the format it is shown, so the prompt's clock line, the task list and every tool result use it, and one prompt rule says so; `due_date` on the tools accepts both `31.12.2026` and `2026-12-31`.
- The model writes Markdown and Telegram showed the asterisks. The Telegram adapter now converts the common subset (`**bold**`, `*italic*`/`_italic_`, `` `code` ``, fenced blocks, links, `#` headings, `-` bullets, `~~strike~~`) to Telegram HTML and sends with `parse_mode=HTML`; everything else is HTML-escaped, so `<`, `&` and a task name like `snake_case` or `2 * 3` are shown as typed. Telegram's MarkdownV2 was not an option (an unescaped `.` rejects the whole message). If Telegram still refuses the entities, the chunk is resent as plain text and logged. The CLI prints the Markdown as is.
- `tests/test_cli_flow.py` now drives a real text turn and a confirm-button turn through the CLI with `FakeLlm`; the voice turn arrives with P04.

---

## Phase 04 — Voice in and voice out

**Branch**: `feature/04-voice`
**Goal**: send a voice note, confirm or amend the transcript, get the answer — spoken back if you want.

**Current Status:** Voice messages are ignored by the adapters. No speech code.

**Action Required:**

- [ ] 1. `app/speech/asr.py`: `transcribe(audio, mime, context_text) -> Transcription(text, language, latency_ms)` via OpenRouter chat completions with an `input_audio` part (base64) and a text part carrying the context (task names, Habitica vocabulary, command names). `transcriptions` table. First step of this task: confirm the model id and payload shape on openrouter.ai; STOP if it isn't there. — `feat(p04): qwen3 asr via openrouter and transcription log`
- [ ] 2. `asr_glossary` table + `/glossary` commands; bias context = glossary + task names + Habitica vocabulary + command names. — `feat(p04): asr glossary`
- [ ] 3. Cleanup pass: cheap model fixes punctuation and terms (glossary + task list), returns text only; raw and cleaned stored. Harness `voice_message_received`: transcribe → clean → state `awaiting_confirmation{transcript}` → `Reply(cleaned, [✅ Correct, ✏️ Amend])`. — `feat(p04): two-pass transcription and confirm flow`
- [ ] 4. `button_pressed`: confirm → run as text; amend → `awaiting_amendment`, ask what to fix; next text → combined message → LLM turn; anything else while awaiting → cancel. Accepted text saved. After an amendment, diff cleaned vs amended; words only in the amendment are offered as glossary terms with one button each. — `feat(p04): amend flow, state transitions, glossary suggestions`
- [ ] 5. `app/speech/tts.py`: `speak(text) -> bytes` (opus) via OpenRouter with `TTS_MODEL` and `TTS_VOICE`; `tts_calls` table. First step: confirm on openrouter.ai that the model is served and accepts a voice/reference id; STOP if not. — `feat(p04): fish audio tts via openrouter`
- [ ] 6. TTS policy in the harness: `user_settings.tts ∈ {on, off}`, default `off`. `/tts on|off`. Skip voice for replies > 600 chars or with charts. — `feat(p04): voice replies and /tts`
- [ ] 7. Telegram: download voice (`audio/ogg`), send voice notes. CLI: `/voice file.ogg`, save/play replies. — `feat(p04): voice in adapters`

**Tests**

- [ ] State machine: confirm, amend, cancel paths with `FakeAsr` + `FakeLlm`. — `test(p04): voice state machine`
- [ ] Combined amendment message shape exact. — `test(p04): amendment message format`
- [ ] Cleanup pass gets glossary and task names in its prompt; raw and cleaned both stored; amendment "Vergil" vs cleaned "virgil" proposes the glossary term; adding it changes the next bias context. — `test(p04): glossary and cleanup`
- [ ] TTS policy: off by default even after voice input; on speaks for text and voice input; long reply never; `tts_calls` row written. — `test(p04): tts policy`

**Acceptance**

```gherkin
Given I send a voice note saying "I did my morning stretch"
When the bot shows the transcript and I press Correct
Then the "Morning stretch" daily is scored up
And with /tts on I receive the answer as text and as a voice note
Given the transcript is wrong and I press Amend and type "stretch, not sketch"
Then the model receives both the transcript and my correction
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**: _none yet_

---

## Phase 05 — Deploy to the VPS

**Branch**: `feature/05-deploy`
**Goal**: the bot runs on the VPS from now on. Every later phase lands on a machine that is already talking to you and already collecting telemetry.

**Current Status:** Runs locally with `scripts/dev.sh` in polling mode. `PUBLIC_BASE_URL` is empty, so the Habitica webhook from P01 is registered nowhere and Telegram is polling.

**Action Required:**

- [ ] 1. `deploy/Dockerfile` (uv, non-root, `data/` as a volume) and `deploy/docker-compose.yml` — app only, bound to `127.0.0.1:8000`. The operator already runs nginx and certbot on the host; **no Caddy, no second web server, nothing listening on 80 or 443.** — `chore(p05): dockerfile and compose`
- [ ] 2. `deploy/nginx-analytica.conf` — a server block to copy into `sites-available`: `server_name` from the operator, `proxy_pass http://127.0.0.1:8000`, the usual `Host` / `X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto` headers, and `client_max_body_size 25M` so long voice notes don't 413. TLS lines are left to `certbot --nginx`. — `chore(p05): nginx reverse proxy config`
- [ ] 3. `deploy/backup.sh`: nightly `sqlite3 .backup` into `data/backups/`, keep 30. Cron line in the README. — `chore(p05): sqlite backup`
- [ ] 4. Switch to webhook mode: `TELEGRAM_MODE=webhook` mounts the Telegram route and sets the webhook at startup; run `scripts/register_webhook.py` to point Habitica at `PUBLIC_BASE_URL`. Startup self-check verifies both webhooks are registered to the current `PUBLIC_BASE_URL` and re-registers if the URL changed; logs clearly what is missing. — `feat(p05): webhook mode and startup self-check`
- [ ] 5. README "Deploy" section: DNS A record (a wildcard `*` record already covers the subdomain), nginx block, `certbot --nginx -d <host>`, `.env`, `docker compose up -d`, register the Habitica webhook, backup cron. Note that Google Calendar auth comes later, in P10. — `docs(p05): deployment guide`

**Tests**

- [ ] Self-check reports each missing piece (no `PUBLIC_BASE_URL`, webhook registered to a stale URL, unreachable app) with fakes. — `test(p05): startup self-check`

**Acceptance**

```gherkin
Given a VPS with nginx, a DNS record and a certificate for the host
When I follow the README Deploy section
Then the bot answers voice and text on Telegram
And Habitica webhooks arrive within seconds of me completing a task in the app
And snapshots keep accumulating while I am not using the bot
And a backup exists the next morning
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**: _none yet_

---

## Phase 06 — Reminders and gear

**Branch**: `feature/06-reminders-gear`
**Goal**: the scheduled features. Small phase.

**Current Status:** Scheduler runs collectors and cron only. No outbound scheduled messages. `equip` unused.

**Action Required:**

- [ ] 1. `app/jobs/reminders.py`: daily at `reminder_time` (default 21:00): list due-and-incomplete dailies from the latest snapshot (refresh if older than 10 min); skip if none; `last_reminder_day` guard. `/remind HH:MM`, `/remind off`. (The Saturday target line and the Started buttons are added in P07.) — `feat(p06): daily reminder`
- [ ] 2. `app/jobs/gear.py`: three sets `gear_int/per/con` in `user_settings`; `dayStart − 30 min` job picks CON if any due daily is incomplete else INT; `run_cron` (P01) followed by PER. `/gear` shows sets; `/gear <stat> auto` picks best owned item per slot from content; `/gear <stat> <slot> <key>`; `/gear now <stat>`. — `feat(p06): int/per/con gear sets around day start`
- [ ] 3. `app/jobs/new_day.py`: after `run_cron`, one message: HP lost (snapshot before vs after), mana, dailies due today. — `feat(p06): new-day message`

**Tests**

- [ ] Reminder: fires once, lists only due+incomplete, skips when none, respects off. — `test(p06): reminder rules`
- [ ] Gear: CON chosen when a due daily is incomplete, INT otherwise; PER after cron; auto-pick chooses the highest-stat owned item per slot from the content fixture. — `test(p06): gear selection`
- [ ] New-day message computes HP lost from two snapshots and lists due dailies. — `test(p06): new day message`

**Acceptance**

```gherkin
Given one due daily is incomplete at 11:30 Zurich
Then the CON set is equipped, and at 11:01 the PER set is
Given all dailies are done at 11:30
Then the INT set is equipped instead
And at 11:01 I get one message with the damage taken and today's dailies
Given two dailies are due and one is incomplete at 21:00
Then I receive exactly one message listing the incomplete one
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**: _none yet_

---

## Phase 07 — Analytics, charts, coach, lifecycle

**Branch**: `feature/07-analytics-coach`
**Goal**: the reason the project exists. `/report` finds the inconsistent dailies and the habits that should be dailies, and says something useful. `/graph gold 30` sends a chart.

**Current Status:** Tables are filling up. Nothing reads them.

**Action Required:**

- [ ] 1. `app/analytics/frames.py` + `thresholds.py` (constants from CLAUDE.md F6, nothing else in the file). — `feat(p07): analytics frames and thresholds`
- [ ] 2. `app/analytics/dailies.py`: rates, misses, streaks, breaks, weekday rates, tiers (`solid`/`inconsistent`/`abandoned`/`too_new`). Dataclasses + `to_findings_json()`. — `feat(p07): daily consistency metrics`
- [ ] 3. `app/analytics/habits.py`: ups per day/weekday, 30-vs-30 trend, stable-weekly detection and the proposed daily shape (weekday set or every-X). `todos.py`: throughput, median age, overdue. — `feat(p07): habit regularity and todo metrics`
- [ ] 4. `app/analytics/stats_series.py` + `charts.py`: gp/hp/mp/exp/lvl daily series → `ChartData`; `to_png()` (seaborn) and `to_terminal()` (plotext). `/graph <stat> [days]`. — `feat(p07): stat series and chart renderers`
- [ ] 5. `app/coach/advice.py`: findings → strong model → advice (rules from F7). `/report [days]`. Tools `get_consistency_report`, `plot_stat`. `jobs/weekly_report.py` Sunday 19:00. — `feat(p07): coach, report command, weekly report`
- [ ] 6. Habit → daily proposal: buttons on the coach message; tool `convert_habit_to_daily` (confirm) creates the daily; second confirm offers to delete the habit, keeping it is fine; dismissed proposals remembered 60 days. Mirror: `convert_daily_to_habit` for abandoned dailies (F7d). — `feat(p07): habit/daily conversions`
- [ ] 7. Broader suggestion: coach prompt gets the full task list and tiers and may add one non-statistical suggestion per report, separated from findings, not repeated within 30 days. — `feat(p07): one broader suggestion per report`
- [ ] 8. Weekly targets (F7c): `weekly_targets` + `weekly_target_weeks` tables; `/target <habit> <n>`, `/target`, `/target <habit> off`; tool `set_weekly_target`; the target daily gets one checklist item per session; `jobs/weekly_targets.py` counts ups per Habitica week, ticks the next checklist item and rewrites the notes (`2/3 this week (Tue, Thu)`) after each up, re-ticks from its own count at every snapshot; checklist ticks recorded as `task_events(kind=checklist)`; Saturday reminder line when short; Sunday `close_day` completes the target daily when met and closes the week row. — `feat(p07): weekly targets with checklist and notes tracker`
- [ ] 9. Start signal + `app/analytics/deadlines.py`: tool `mark_started`, `/started <task>`, **Started** buttons in the reminder for due-dated todos without a signal (`task_events(kind=started)`); lead days, completion offset, first activity, start fraction, aggregates by tag and lead bucket, `last_minute` / `no_start_signal` flags; tool `get_deadline_report`; section in `/report`. — `feat(p07): start signal and deadline behaviour analytics`
- [ ] 10. `app/analytics/lifecycle.py`: stage per task, transition proposals per the F7e table (incl. retire after 90 solid days); `retire_daily` tool with confirm; proposals surface in `/report` and the weekly report with buttons. `aspirations` / `aspiration_tasks` tables (empty until P08 fills them, but the stage logic reads them). — `feat(p07): lifecycle stages and transitions`

**Tests**

- [ ] Hand-built `daily_records` (4 done / 1 miss repeating) → exact rate, streak list, `n_breaks`, tier `inconsistent`; one miss in 14 → `solid`; 40% → `abandoned`; edge cases. — `test(p07): daily metrics and tiers`
- [ ] Habit stability: 3/week for 7 of 8 weeks on Mon/Wed/Fri → stable, proposal = weekly on those days; same counts on random days → proposal = every 2 days; 1/week → not proposed. — `test(p07): habit to daily detection`
- [ ] Series resampling with gaps; PNG bytes non-empty; terminal renderer produces output. — `test(p07): charts`
- [ ] Coach prompt contains only flagged items; `FakeLlm` echo shows language and item names. — `test(p07): coach prompt scope`
- [ ] Conversion: confirm creates the daily with the right `repeat`/`everyX`; deletion needs a second confirm; dismissed proposal not repeated; daily → habit mirror. — `test(p07): conversion confirms`
- [ ] Weekly target: 3 ups Tue/Thu/Sat → three checklist items ticked, notes read `3/3`, Sunday daily completed at close_day, week row `met=true`; 2 ups → not completed, Saturday reminder says 2 of 3; a checklist wiped externally is re-ticked at the next snapshot; week boundaries follow dayStart. — `test(p07): weekly targets`
- [ ] Deadlines: hand-built todos (created, started, completed, due) → exact lead/offset/start fraction; explicit and reminder-button starts both count; 4 of 5 done on the due day → `last_minute`; grouping by tag isolates the late subset. — `test(p07): deadline metrics`
- [ ] Lifecycle: solid daily with 90 due days → retire proposal; declined proposal silent for 60 days. — `test(p07): lifecycle transitions`

**Acceptance**

```gherkin
Given 30 days where "Vacuum the floors" is done 4 days and missed 1, repeatedly
When I send /report
Then it is named inconsistent with its rate and miss count, with advice in my language ending in a question
Given "Exercise" has 3 ups a week for 7 of the last 8 weeks
Then the report proposes turning it into a daily, and confirming creates it
When I send /graph gold 30
Then I get a PNG chart in Telegram, and a terminal chart in the CLI
Given /target Workout 3 and three Workout ups this week
Then the "Workout: 3/week" daily is completed before Monday's reset without me touching it
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**: _none yet_

---

## Phase 08 — Aspirations and planning sessions

**Branch**: `feature/08-aspirations`
**Goal**: "I want to learn Latin" turns into an agreed starter set of tasks in Habitica, linked to the aspiration, and tracked through the lifecycle from then on.

**Current Status:** Sessions exist (P03) but there is no planning kind. Lifecycle stages exist (P07) but nothing creates aspirations.

**Action Required:**

- [ ] 1. Planning sessions: `kind=planning`, 120-min idle limit; tools `start_planning_session(topic)` and `end_session()`; `/session planning <topic>`. The model is instructed to open one when it recognises an aspiration or a request to rework several tasks. — `feat(p08): planning sessions`
- [ ] 2. `app/coach/aspirations.py`: planning prompt (ask what they know, time per week, what "done in three months" looks like; propose one or two todos, one habit, rarely a daily; English titles). — `feat(p08): aspiration planning prompt`
- [ ] 3. Tool `create_plan(aspiration, items[])`: one confirm shows the full list; on **Do it** creates every task, writes `aspirations` + `aspiration_tasks`, stamps each task's notes with `🎯 <aspiration>`, closes the session with a summary. — `feat(p08): create_plan with batch confirm`
- [ ] 4. `/aspirations` lists aspirations with their tasks and stages; weekly report gets one line per active aspiration; todo → habit proposal (F7e row 2) wired for aspiration-linked todos. — `feat(p08): aspirations view and report line`

**Tests**

- [ ] Planning session opens via tool, survives 100 min idle, closes on `end_session` with a summary that names the agreed items. — `test(p08): planning session`
- [ ] `create_plan` creates nothing before confirm, everything after, with links and notes stamps; cancel creates nothing. — `test(p08): create_plan confirm`
- [ ] Completing an aspiration-linked todo triggers a todo → habit proposal. — `test(p08): todo to habit proposal`

**Acceptance**

```gherkin
Given I say "I want to learn Latin"
When the bot asks two or three questions and I answer
Then it proposes a todo "Buy a Latin textbook", a habit "Latin vocabulary, 10 min" and asks for one confirm
And after confirming, they exist in Habitica, linked to the aspiration "Learn Latin"
When I come back the next day and ask "how's Latin going?"
Then the answer uses yesterday's session summary and the linked tasks' status
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**: _none yet_

---

## Phase 09 — Challenges

**Branch**: `feature/09-challenges`
**Goal**: browse and join Habitica challenges from the bot.

**Current Status:** Client has no challenge methods.

**Action Required:**

- [ ] 1. Client: `list_challenges(search?)`, `get_challenge(id)`, `join_challenge(id)`; types; fixture. — `feat(p09): challenge endpoints`
- [ ] 2. `/challenges [keyword]` → compact list; tools `list_challenges`, `join_challenge` (confirm). — `feat(p09): challenges command and tools`

**Tests**

- [ ] Parser on fixture; join requires confirm. — `test(p09): challenges`

**Acceptance**

```gherkin
When I send /challenges pomodoro
Then I see matching public challenges
When I ask the bot to join one and confirm
Then it appears in my Habitica challenges
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**: _none yet_

---

## Phase 10 — Google Calendar

**Branch**: `feature/10-google-calendar`
**Goal**: schedule todos into free slots on the main calendar; `(done)` is isomorphic with Habitica completion, both ways.

**Current Status:** No Google code. Completion events exist in `task_events`.

**Action Required:**

- [ ] 1. `app/calendar/client.py`: `scripts/google_auth.py` one-time OAuth; `list_events(start, end)`, `freebusy(start, end)`, `create_event`, `patch_event_title`. Raw REST via httpx, `google-auth` for refresh. Recorded fixture. — `feat(p10): google calendar client`
- [ ] 2. `calendar_links` table + migration. — `feat(p10): calendar links`
- [ ] 3. `app/calendar/planner.py`: open todos (due-dated first) × free slots within `PLAN_HOURS`; slot length = `task_durations` (tool `set_task_duration`, "this takes 10 minutes", also written to notes as `~10m`) > `~Nm` in notes > `PLAN_SLOT_MINUTES` → proposal. `/plan [days]` and tool `propose_schedule`; `create_calendar_events(proposal_id)` after confirm; links recorded; task id in the event description; task notes stamped with the slot; the slot feeds the deadline analytics as first activity. — `feat(p10): schedule todos into free slots`
- [ ] 4. `app/calendar/sync.py` every 5 min: Habitica completed → append ` (done)`; event ends with `(done)` and task open → `score_task(up)` with `source=calendar`. Undone → done only; the bot never removes a suffix or un-completes a task; never touches unlinked events. — `feat(p10): done isomorphism, undone to done only`
- [ ] 5. Post-slot check-in: 10 min after a scheduled slot ends, ask **Done / Partly / Not yet**; Done scores the task, Partly records `started` and offers to reschedule the remainder, Not yet offers to reschedule. — `feat(p10): post-slot check-in`

**Tests**

- [ ] Planner never overlaps busy slots or leaves working hours; due-dated todos first; a stated 10-minute duration yields a 10-minute slot. — `test(p10): planner`
- [ ] Check-in: fires once per slot; Partly writes a `started` event once; Not yet proposes a new slot. — `test(p10): post-slot check-in`
- [ ] Sync with `FakeCalendar` + `FakeHabiticaClient`: rename once, not twice; `(done)` scores the task once; unlinked events untouched; nothing is ever reverted. — `test(p10): sync rules`

**Acceptance**

```gherkin
Given three open todos and a busy calendar tomorrow
When I send /plan 2 and confirm
Then three events exist in free slots within working hours, linked to the todos
When I complete one todo in Habitica
Then its event title ends with (done) within 5 minutes
When I add (done) to another event's title in Google Calendar
Then its todo is completed in Habitica within 5 minutes
```

**Done when**: all boxes ticked · lint + tests green · PR open · STOP.

**Notes & deviations**: _none yet_

---

## Phase 11 — Svelte dashboard (maybe, later)

Not planned. Only if PNG charts turn out to be not enough. If green-lit: read-only FastAPI JSON endpoints over the analytics functions, one SvelteKit page. Analytics functions already return plain data, so nothing needs to change underneath.

---

## Backlog / found along the way

_Things noticed during a phase that are out of its scope. Add a line, don't fix it in the current PR._

- Growth suggestions ("you're solid on X, try Y") — AI-side extension of the coach, after real data exists.
- `/voices` — browse the Fish Audio voice library and pick one from the bot.
- Qwen3-TTS as a second `speak()` backend, to compare with Fish Audio.
- Multi-user — not planned; schema already carries `user_id`.
- Telegram webhook route handles the update inline before answering 200; once LLM turns take seconds (P03), answer immediately and process in a background task so Telegram does not redeliver.
- Outbound Telegram delivery to an allowlisted id that never pressed Start raises from the scheduled job; catch and log per chat when reminders arrive (P06).
- Once webhooks are live (P05), a task the bot scores produces a `source=bot` event from its own collection round and, seconds later, a `source=webhook` one from Habitica. The receiver should skip the event when the latest `task_snapshots` row already reflects the payload's state (same `completed` / counters / value), the way it already skips the scheduled round's duplicate.
- `rapidfuzz` fallback for matching RU/FR task references against English titles, if the model proves unreliable at it (CLAUDE.md F2 allows it; not needed on the two models tried).
