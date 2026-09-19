# jev

A local computer-use agent that lives in your dock. Hit the icon, it asks **wyd?**,
you type a task, it drives your actual mouse and keyboard until it's done.

Every task you run is remembered as a *flow*. The second time around jev tells you
**"you used this flow 3 times! run it again?"** — and because it recorded exactly what
it did, one click replays the whole thing with no model in the loop, so it's instant.

```
┌────────────────────────────────────────────────────────┐
│  wyd?                                                  │
│                                                        │
│  ⚡ open spotify and play lofi   · used 4 times, run…  │
│  ⚡ start my standup notes       · used 2 times, run…  │
│     clear my downloads folder    · used once, run …    │
│                                                        │
│  enter to run · esc to hide · click a flow to replay   │
└────────────────────────────────────────────────────────┘
```

## Install

```bash
git clone https://github.com/<you>/jev && cd jev
pip install -e .
export OPENROUTER_API_KEY=sk-or-...     # https://openrouter.ai/settings/keys
jev                                      # opens the spotlight
```

### Put it in the dock

- **macOS:** `./scripts/install_macos_app.sh` builds `/Applications/Jev.app`. Open it once,
  then right-click the dock icon → Options → Keep in Dock. Grant **Screen Recording** and
  **Accessibility** to Jev.app in System Settings → Privacy & Security, or it can't see or
  click anything.
- **Linux:** `cp scripts/jev.desktop ~/.local/share/applications/`.

Three ways to trigger it: the dock/app icon, the tray icon, or the global hotkey
(`cmd+shift+space` on macOS, `ctrl+alt+space` elsewhere).

## Use it

```bash
jev                        # spotlight window
jev run "open spotify and play lofi"
jev flows                  # every flow, with run counts and averages
jev replay 3               # replay flow 3 from its recorded trace
jev forget 3
```

## How it works

```
screenshot ──► OpenRouter (vision + tool calls) ──► click / type / key / scroll / drag
     ▲                                                          │
     └──────────────────── look again ◄─────────────────────────┘
                                │
                          done(success, summary)
                                │
                     flow store (~/.jev/jev.db)
                     fingerprint · run count · action trace
```

- **`jev/computer.py`** — capture and input. Screenshots are downscaled to 1280px wide and the
  model's coordinates are mapped back to physical pixels, so retina and odd resolutions just work.
- **`jev/agent.py`** — the loop. One tool call per turn, only the last 3 screenshots stay in
  context (that's most of the speed), and every executed action is appended to a trace.
- **`jev/flows.py`** — SQLite flow memory. Tasks are normalized ("Open the Spotify app, please!"
  → `open spotify app`) and fuzzy-matched, so re-phrasings count as the same flow.
- **`jev/ui.py`** — the spotlight, flow chips, and tray/hotkey triggers.

### Replay vs. agent

A flow becomes replayable (⚡) after one successful run. Replay fires the recorded actions
directly — no screenshots, no tokens, sub-second for short flows. Use it for flows that start
from a predictable state (launcher-driven ones are ideal). If the screen has drifted, stop it
and run the task normally; the fresh trace overwrites the stale one.

## Config

`~/.jev/config.json`, or env vars:

| var | default | what |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | required |
| `JEV_MODEL` | `google/gemini-2.5-flash` | any vision + tool-calling model on OpenRouter |
| `JEV_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint |
| `JEV_HOME` | `~/.jev` | config + flow database |

Other knobs in `config.json`: `max_steps`, `screenshot_width`, `action_delay`.

## Safety

jev drives your real machine. It's told to refuse destructive prompts and never invent
credentials, but it is not sandboxed — read the task before you run it, keep the stop button
in reach, and slam the mouse into a screen corner to trip pyautogui's failsafe.

## Develop

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

MIT.
