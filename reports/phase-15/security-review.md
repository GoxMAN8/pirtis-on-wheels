# Phase 15 · adversarial review of the sign-in and the deploy kit

An independent review, briefed with the code and not with our conclusions. It was asked
to report, not repair, and it sent nothing to the live deployment. It reproduced
findings 1, 2, 3 and 6 against a local server. Every fix below was written test-first
(red, then green) and then probed again on the live app as a stranger.

**Headline:** no way was found for a stranger to reach the API or any stored data.

| # | Severity | Finding | What was done |
|---|---|---|---|
| 1 | high | Memory exhaustion through `/api/login`. Each guess runs scrypt (16 MiB), with no cap on parallel runs, and the lockout was checked before the hash but counted after it. About 30 parallel guesses would crash the 0.5 GiB container. | **Fixed.** A guess is counted under the lock before it is hashed, and at most two hashes run at once. A test runs 12 parallel guesses: at most 5 reach the hash, at most 2 at a time. |
| 2 | medium-high | Anyone could lock a named account out for good (5 guesses every 15 minutes). | **Fixed.** The lock is on the address the guesses came from, never the account. A test shows the owner still signs in from elsewhere. |
| 3 | medium | Open redirect after sign-in: `next=/<tab>/evil.example` passed the regex, and the browser drops the tab, giving `//evil.example`. | **Fixed.** `js/next.js` decides on the parsed URL's origin. A Node test covers the tab trick, `//`, a backslash, a full URL and `javascript:`. |
| 4 | medium | Terraform's state holds the storage account's keys; a refresh would also write the Container App secrets into it. The kit's comments said otherwise. | **Confirmed** with a refresh-only plan: it would read the secrets back. The state is now written owner-only (`umask 077`; the existing files were set to mode 600), and the claims are corrected in `main.tf`, the Makefile, the deploy README and SPEC §9 (D-86). A remote, encrypted backend is the next step. |
| 5 | medium | No ceiling on model spend: one global limit of 30 per minute, no daily cap, and a replay counted once. | **Fixed.** Each account gets its own minute, and everyone shares a daily budget (2,000 units, about $1). A replay costs 2 units. Tests cover both. |
| 6 | low-medium | `/static/fonts/..%2fadmin.html` served any static file to a stranger: openness was judged on the raw path, the file lookup on the decoded one. | **Fixed.** Openness is judged on the decoded path, refusing `..` and `\`. Tested locally, and on the live app all four variants now redirect to sign-in. |
| 7 | low | Sessions outlived a new password, and no command rotated the session key. | **Fixed.** The signature covers the password hash, so a new password ends the old sessions (tested). `make rotate-session` signs everyone out. |
| 8 | low | Removing the last account never reached the cloud. | **Fixed.** The secret goes back to `unset`, and the app refuses to start (fails closed). |
| 9 | low | Secret values passed as `az` arguments are visible in `ps` on the deploying machine while the command runs. | **Kept, written down** (SPEC §12). It is a single-user laptop, and the values never reach a file or the repo. |
| 10 | low | A negative `Content-Length` made the server read until the client hung up. | **Fixed.** Anything but digits is refused with 400 (tested). Live, the ingress drops such a request before it reaches the app. |
| 11 | low | The deployed image was built from uncommitted code. | **Fixed.** The image tag now says `-dirty` when it should. The live app and job run an image built from the phase's commit (tag `9b9bb75`). |

**Checked and found sound by the review:**
- the cookie HMAC, compared in constant time; names that can't inject a separator
- expiry and removed accounts are enforced
- a wrong password and an unknown name get the same work and the same answer
- fails closed on placeholders and short keys
- `SameSite=Strict` plus an Origin check
- exact-match API routes; no header injection
- the allow-list build context; a non-root, digest-pinned image; registry admin off; no insecure ingress
- ignored state and account files; CSP

**Not examined by the review:**
- the agent's own prompt-injection surface (covered by the red-team cases, SPEC §6)
- whether `azurecontainerapps.io` is on the Public Suffix List (relevant to cookie tossing between neighbouring apps)
