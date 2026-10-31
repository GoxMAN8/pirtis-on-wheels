# Human evaluation: the protocol

People call the receptionist from the caller simulator, each following a scenario card,
and rate every call on the same five items. The ratings show how the agent sounds to a
person, and they calibrate the kit's judge: the same questions go to the judge, and we
report how often it agrees with people (Cohen's κ).

## Before you start

- Run the playground: `cd python && make playground`, then open http://127.0.0.1:8000.
- Pick a short rater code: your initials (two to four letters). Use the same one every
  time. No names.
- Use only the seed personas on the cards. Never your own name, phone or address.
- About 30–45 minutes: as many cards as you like, in any order. A card can be done by
  more than one person; that is how we measure agreement between raters.

## Each call

1. On the simulator's first screen, under *Test with a scenario card*, pick a card. It
   shows your goal, what you know, and one twist to throw in.
2. Press *Call as …*. Speak (tap to talk, or hold) or type. Play the caller naturally: hedge,
   correct yourself, cut in. Bring the twist in when it fits.
3. When the call ends, or whenever you're done, rate it. *Rate this call* opens the form.

## The rubric

| Item | The question | Scale |
|---|---|---|
| Goal | Did the call reach the card's outcome? | yes / no |
| Natural | Did the receptionist sound like a capable person on the phone? | 1–5 |
| Numbers and dates | Were prices, dates and figures easy to follow when heard? | 1–5 |
| One question at a time | Did each reply ask at most one thing? | 1–5 |
| Turn-taking | Did it wait, check in and cope with interruptions well? | 1–5 |

1 is poor, 3 is acceptable, 5 is as good as a person. A note is optional; say what
stood out. A call that should become a test case can also be flagged with 👎 on the line
that went wrong.

## Afterwards

- The admin console's *Human eval* view shows each scenario's results and the rubric.
- `cd python && make calibrate` asks the kit's judge the same questions about the same
  calls and saves the agreement (and κ between raters who rated the same call). It
  spends a little model credit: one judge call per rated item per receptionist turn.
