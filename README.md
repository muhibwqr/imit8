# imit8

A local computer-use agent that lives in your dock. Hit the icon, it asks **wyd?**,
you type a task, it drives your actual mouse and keyboard until it's done.

Every task you run is remembered as a *flow*. The second time around imit8 tells you
**"you used this flow 3 times! run it again?"** — and because it recorded exactly what
it did, one click replays the whole thing with no model in the loop, so it's instant.

```
┌────────────────────────────────────────────────────────┐
│  wyd?                                                  │
│                                                        │
│  ↻ open spotify and play lofi   · used 4 times, run…   │
│  ↻ start my standup notes       · used 2 times, run…   │
│    clear my downloads folder    · used once, run …     │
│                                                        │
│  next · start my standup notes · Mon 09:00             │
│  enter to run · esc to hide · click a flow to replay   │
└────────────────────────────────────────────────────────┘
```

## Install

One command on macOS (or Linux) — clones into `~/.imit8/src`, installs into its own venv,
puts `imit8` on your PATH, and builds the dock app:

```bash
curl -fsSL https://raw.githubusercontent.com/muhibwqr/imit8/main/install.sh | bash
export OPENROUTER_API_KEY=sk-or-...     # https://openrouter.ai/settings/keys
imit8                                    # opens the spotlight
```

From a clone instead: `pip install -e .` then `./scripts/install_macos_app.sh`
(macOS) or `cp scripts/imit8.desktop ~/.local/share/applications/` (Linux).

On macOS, open Imit8.app once, then right-click the dock icon → Options → Keep in Dock, and
grant it **Screen Recording** and **Accessibility** in System Settings → Privacy & Security —
without those it can't see or click anything.

Three ways to trigger it: the dock/app icon, the tray icon, or the global hotkey —
**ctrl+option+space** on macOS, **ctrl+alt+space** elsewhere (same physical keys).

## Use it

```bash
imit8                        # spotlight window
imit8 run "open spotify and play lofi"
imit8 flows                  # every flow, with run counts and averages
imit8 replay 3               # replay flow 3 from its recorded trace
imit8 forget 3
```

## Schedule it

`schedule…` in the spotlight (or the tray, or `imit8 calendar`) opens a week grid — drag across
cells to pick 30-minute slots, drag over blue cells to clear them. The next two runs are always
visible under the flow chips, so you can see what imit8 is about to do on its own.

```bash
imit8 schedule "clear my downloads folder" --days mon wed fri --at 09:00 17:30
imit8 schedule "back up my notes" --once 2026-01-04T18:00   # one-off
imit8 schedules              # every schedule with its next run
imit8 unschedule 2
imit8 calendar               # the drag-to-select grid
```

A scheduled task replays its recorded flow when it has one, and falls back to the full agent
loop when it doesn't.

## Use it from another agent

`imit8 mcp` speaks MCP on stdio, so an agent session can hand work to your desktop without
leaving its own loop — including creating schedules:

```json
{
  "mcpServers": {
    "imit8": { "command": "imit8", "args": ["mcp"] }
  }
}
```

Tools: `imit8_run`, `imit8_replay`, `imit8_flows`, `imit8_forget`, `imit8_schedule`,
`imit8_schedules`, `imit8_unschedule`, `imit8_set_schedule_enabled`. The same surface is
importable directly:

```python
from imit8 import api

api.run("open spotify and play lofi")  # replays the recorded flow if there is one
api.schedule("clear my downloads", days=["fri"], times=["18:00"])
api.schedule("post the standup note", once="2026-01-04T09:00")
api.flows()
```

## How it works

```
screenshot ──► OpenRouter (vision + tool calls) ──► click / type / key / scroll / drag
     ▲                                                          │
     └──────────────────── look again ◄─────────────────────────┘
                                │
                          done(success, summary)
                                │
                     flow store (~/.imit8/imit8.db)
                     fingerprint · run count · action trace
```

- **`imit8/computer.py`** — capture and input. Screenshots are downscaled to 1280px wide and the
  model's coordinates are mapped back to physical pixels, so retina and odd resolutions just work.
- **`imit8/agent.py`** — the loop. One tool call per turn, only the last 3 screenshots stay in
  context (that's most of the speed), and every executed action is appended to a trace.
- **`imit8/flows.py`** — SQLite flow memory. Tasks are normalized ("Open the Spotify app, please!"
  → `open spotify app`) and fuzzy-matched, so re-phrasings count as the same flow.
- **`imit8/ui.py`** — the spotlight, flow chips, upcoming schedules, and tray/hotkey triggers.
- **`imit8/schedule.py`** / **`imit8/scheduler.py`** — weekly slots and one-off runs in the same
  database, polled by a background thread.
- **`imit8/calendar_view.py`** — the drag-to-select week grid.
- **`imit8/api.py`** / **`imit8/mcp.py`** — the agent-facing surface: plain functions, and a
  dependency-free stdio MCP server over them.

### Replay vs. agent

A flow becomes replayable (↻) after one successful run. Replay fires the recorded actions
directly — no screenshots, no tokens, sub-second for short flows. Use it for flows that start
from a predictable state (launcher-driven ones are ideal). If the screen has drifted, stop it
and run the task normally; the fresh trace overwrites the stale one.

## Config

`~/.imit8/config.json`, or env vars:

| var | default | what |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | required |
| `IMIT8_MODEL` | `google/gemini-2.5-flash` | any vision + tool-calling model on OpenRouter |
| `IMIT8_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint |
| `IMIT8_HOME` | `~/.imit8` | config + flow/schedule database |

Other knobs in `config.json`: `max_steps`, `screenshot_width`, `action_delay`, and
`verify_done` (take one more screenshot and make the model re-confirm before a task counts as
successful — on by default; it's what stops a stray click from being recorded as a working flow).

## Safety

imit8 drives your real machine. It's told to refuse destructive prompts and never invent
credentials, but it is not sandboxed — read the task before you run it, keep the stop button
in reach, and slam the mouse into a screen corner to trip pyautogui's failsafe.

## Develop

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

MIT.
