# Jarvis: a Hinglish voice assistant with a safe action layer

A Windows voice assistant that understands mixed Hindi-English commands and acts on your PC, with permission tiers, confirmation for risky actions, an audit log and undo.


## How it works

```mermaid
flowchart LR
    Mic --> Wake["openWakeWord<br/>(offline, 'hey Jarvis')"] --> STT["STT<br/>(Google / Whisper)"]
    STT --> LLM["LLM tool-calling<br/>(Groq)"]
    Mem[("ChromaDB<br/>memory")] <--> LLM
    LLM --> Guard{"Safe action layer<br/>tiers · confirm · log · undo"}
    Guard --> Tools["PC tools"]
    LLM --> TTS["TTS<br/>(edge-tts)"]
```

## Safe action layer (`safety.py`)

| Tier | Behavior | Examples |
|---|---|---|
| 0 | Auto-run | time, recall, file search |
| 1 | Auto-run, logged, undo where possible | open app, volume, screenshot, reminder, remember |
| 2 | Spoken read-back + keypress confirm (10 s, no answer = denied) | opening a file |
| 3 | Blocked (also the default for unknown tools) | anything not in the tier table |

- **Audit log:** every action is stored in SQLite (`jarvis_actions.db`) with transcript, tool, arguments, tier and result. View it with `python safety.py`.
- **Undo:** "undo that" reverses volume changes, screenshots (moved to a trash folder), reminders and remembered facts. Opened apps and searches are not undoable.

## Evaluation

Metric: task success, meaning the right tool and the right arguments, on 61 commands (36 Hinglish romanized, 15 Devanagari Hindi, 10 English, 6 should-do-nothing). Commands are in `eval/commands.jsonl`.

| Setup | Task success |
|---|---|
| LLM intent only (typed text) | 95.1% (58/61) |
| Google STT (hi-IN) + LLM | 93.4% (57/61) |
| Google STT (en-IN) + LLM | 95.1% (58/61) |
| Whisper (local) + LLM | not yet run |

Reproduce: `python eval_hinglish.py text`, then `record`, then `audio --stt google-hi`.

Remaining failures are mostly recording cut-offs on trailing words/names and one STT mishearing ("paint" → "पेट"); see `eval/results_*.json` for details.

Limitations: the command set was written by one person and is cleaner than real speech; single speaker; one accent.

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
$env:GROQ_API_KEY="your-key"
python jarvis.py
```

Say "hey Jarvis", wait for the beep, then speak. Say "stop" after the beep to quit.

Tests: `pip install pytest` then `python -m pytest tests -q`.

## Roadmap

- Local-only mode (Ollama + local STT/TTS) with a per-component toggle
- Sarvam STT backend in the eval
- Voice-only confirmation for tier 2 (currently keypress, on purpose, so a misheard "yes" can't approve an action)
