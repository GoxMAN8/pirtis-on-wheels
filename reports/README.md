# Test and gate reports

The raw output behind every number in `BUILD_LOG.md`, one folder per phase. Each file
opens with the phase, the commit it was produced at, and the command that produced it.
Test lists are re-runnable: check out that commit and run the command.

| File | What it is |
|---|---|
| `pytest.txt` | every test by name with its result (`uv run pytest -vv`) |
| `gate-*.txt` | the harness table for a gate run: per fixture, result, model calls, tokens, time, and every failure reason |
| `live-*.txt` | a targeted live run, summarised from its trace files: what the model extracted, the API calls, what was said, the judge's verdicts |
| `*-demo.txt` | a zero-token run of new code on real data; the script that produced it sits beside it |
| `browser-checks.txt`, `screens/` | what was checked in a real browser, every defect found there and its fix; screenshots of the final state |
| `palette.txt` | the chart colours run through the palette validator, both themes |
| `privacy-scan.txt` | every admin read endpoint scanned for names, phones and customer ids, with live data stored |
| `cases-runs.txt`, `loop-runs.txt` | every run of a set of cases, from their trace files: what was said and each failure's reason |
| `variants-review.md` | the generated variants and the kit's verdict on each, as the reviewer sees it |
| `reliability.txt`, `reliability.json` | pass^k for k = 1 to 5 per suite, from five runs of every case |
| `image.txt` | the container image: size, layers, what it holds (checked inside it), the amd64 run |
| `container-checks.txt` | the playground run from the image: pages, a real call, a restart, its log streams |
| `gate-job.txt` | the gate job's one line, the gate run and turn records it saved |
| `infra.txt` | the cloud resources Terraform manages, a plan against the live deployment, the state's protection, the platform properties checked |
| `cloud-checks.txt` | the live deployment probed from outside, as a stranger, and read back from its own logs and storage |
| `security-review.md` | an independent adversarial review's findings, and what was done with each |

| `replay-*.txt` | a fixture replayed with the model's answer scripted, zero tokens, to show what the receptionist says for a given reading; the script beside it |

Phase 1 produced documents only, so it has no folder. `after-16/` holds the work after
the final phase: the gate that failed once the prompt leak was closed (D-88, D-91), and
the replay that reproduced its reply, and the extractor eval that followed (D-92).
