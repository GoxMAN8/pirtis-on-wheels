# Deploy

One container image runs both workloads:
- **the playground**, the caller simulator and admin console (the default command)
- **the gate job**, `gate-job`, run nightly and by hand

Design and decisions: SPEC §9 (D-80, D-81).

## The image

Build it from the repo root. The build context is an allow-list in `.dockerignore`
(`python/`, `fixtures/`, `tests/human/`); `.env` never enters it.

```bash
docker build -f deploy/Dockerfile -t pirtis .
```

Azure runs `linux/amd64`. On an Apple-silicon Mac, check that build too:

```bash
docker buildx build --platform linux/amd64 -f deploy/Dockerfile -t pirtis:amd64 --load .
```

Run the playground on <http://127.0.0.1:8000>, keeping calls in `python/var/`. The key
is read from `python/.env` and never baked in.

```bash
docker run --rm --env-file python/.env -v "$PWD/python/var:/data" -p 127.0.0.1:8000:8000 pirtis
```

Run the nightly gate (the given cases and the red team, 3 runs each) against the same
data. It prints one JSON line and exits 1 if the gate fails.

```bash
docker run --rm --env-file python/.env -v "$PWD/python/var:/data" pirtis gate-job
```

`gate-job --suites all` runs every suite, like `make gate`; `--id a,b` narrows it to
those cases, and `--no-extract` skips the extractor eval.

| Variable | In the image | Meaning |
|---|---|---|
| `LLM_API_KEY` | not set: pass it at run time (a Container App secret in Azure) | the model key |
| `LLM_BASE_URL` | not set: the OpenAI default | Azure OpenAI's v1 endpoint, if used |
| `PIRTIS_HASH_SALT` | not set: a fixed development salt | the salt for phone hashes; set a secret one in Azure |
| `PIRTIS_DATA_DIR` | `/data` | where records are kept: the mounted file share in Azure |
| `PIRTIS_PROMOTE_DIR` | empty | a promoted case is downloaded, not written into the image |
| `PIRTIS_HOST`, `PORT` | `0.0.0.0`, `8000` | where the playground listens |
| `PIRTIS_RATE_PER_MIN` | not set: 30 | the playground's per-minute request cap |
| `PIRTIS_GATE_SUITES` | not set: `nightly` | the gate job's default suites |
