# Contributing

Thanks for looking. This is a small personal tool that turned out to work well enough to share;
contributions that keep it that way are welcome.

## Before you change the detector

The project's rule is **measure, don't guess**. Every setting in the state machine
(`adblock/detect/fusion.py`) is there because a recorded game showed the need for it, and every
change is replayed over the recorded sessions before it touches a live one:

```powershell
.\.venv\Scripts\python.exe tools\evaluate.py --scene --drive --sheet
```

reports *game-seconds hidden* (must stay near zero: hiding live play is the expensive mistake)
and *ad-seconds leaked* (the cheap one) for every session with trustworthy labels, and writes a
contact sheet around each detector transition so detector mistakes and label mistakes can be told
apart. Run it on a copy of the current `main` first, then on your change, and put both numbers in
the pull request. [docs/development.md](docs/development.md) explains the recordings, the labels
and the tools.

## Tests and style

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

The tests need no screen, no Chrome and no GPU; `tests/test_docs.py` fails when a setting or
command-line flag is missing from [docs/configuration.md](docs/configuration.md) or
`config.example.toml`, so document what you add.

Code style: plain Python 3.11, type hints, docstrings that say *why* a rule exists (ideally with
the live moment that caused it), no new dependencies without a reason. Dates in comments are the
day something was observed; keep them, they make the history traceable.

## Reporting a problem

Attach `logs\app.log` and the session folder's `timeline.jsonl` and `detector.csv` (both are small
text files; the frames are not needed unless asked). Say which service and network you were
watching, whether Chrome was fullscreen, and whether the image model was installed. If the app hid
live play, the time of the "That's the game" click is the most useful single fact.
