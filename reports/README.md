# Test and gate reports

The raw output behind every number in `BUILD_LOG.md`, one folder per phase. Each file
opens with the phase, the commit it was produced at, and the command that produced it.
Test lists are re-runnable: check out that commit and run the command.

| File | What it is |
|---|---|
| `pytest.txt` | every test by name with its result (`uv run pytest -vv`) |
| `gate-*.txt` | the harness table for a gate run: per fixture, result, model calls, tokens, time, and every failure reason |
| `live-*.txt` | a targeted live run, summarised from its trace files: what the model extracted, the API calls, what was said, the judge's verdicts |

Phase 1 produced documents only, so it has no folder.
