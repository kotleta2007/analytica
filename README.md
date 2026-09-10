# Habitica Analytica

Telemetry, analytics and coaching for [Habitica](https://habitica.com), through a
Telegram bot you can talk to — by text or by voice, in English, Russian or French.

Self-hosted on your own VPS. Habitica only exposes the *current* streak and prunes
task history for non-subscribers, so this snapshots the account continuously and
receives its webhooks: you own the full record, and you get subscriber-grade
analytics from a free account.

## The thesis: smooth transitions

Habitica gives you three task types and leaves the transitions to you. This app is
about the transitions.

```
aspiration  →  first todo  →  habit  →  daily  →  second nature
"I want to     "Go to the      "Workout"      "Workout        (retire the daily;
 start          gym once"       scored 2-3×     Mon/Wed/Fri"    it no longer needs
 lifting"                       a week                          tracking)
```

You bring an aspiration; the bot discusses it with you and you agree on a starter
set of todos and habits. From then on plain statistics (polars) watch the telemetry
and propose the next transition when the data supports it — habit stable → make it
a daily? daily solid for months → retire it? daily failing → demote it? Every
transition is proposed by numbers, explained by a model, and executed only after you
press a button.

Statistics find the patterns; the LLM explains them. The LLM never does the finding.

## Running it locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
cp .env.example .env      # fill in what you have; groups are validated lazily
uv sync
./scripts/dev.sh          # migrate, then serve on http://127.0.0.1:8000
curl http://127.0.0.1:8000/health
# {"ok":true,"db":true}
```

The database is one SQLite file at `data/analytica.db` (WAL mode). Backing it up is
copying the file.

## Tests

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests never touch the network: external services are covered by fakes in
`tests/fakes/` and recorded fixtures in `tests/fixtures/`.

## Where things are

`CLAUDE.md` is the specification — architecture, feature specs, conventions.
`GAMEPLAN.md` is the phase-by-phase plan and its current state.
