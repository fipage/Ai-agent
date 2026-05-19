# Minimal AI Agent

A tiny local agent built from scratch. It has a planning loop and safe tools:

- `calculate` — safe arithmetic only
- `remember` — save text to `memory.txt`
- `recall` — read memory
- `time` — show current timestamp

## Run

```bash
python agent.py "посчитай 12 * (7 + 3)"
python agent.py "запомни мой проект называется Atlas"
python agent.py "вспомни память"
python agent.py "какое сейчас время"
```

## Extend

Add more tools in `__post_init__`, then update `plan()` so the agent knows when to use them.
