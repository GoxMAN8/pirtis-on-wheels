"""The receptionist.

decide runs four steps and returns what to do next:

1. turn gate (turn.py)      code only: wait while the caller is still talking
2. understand (understand.py) one model call: the call so far as a validated CallState
3. policy (policy.py)        code only: identify, ask, search, offer, and book on a yes
4. speech (speech.py)        code only: the words, with numbers and dates spoken

The model listens; code talks. handlers, in actions.py, carry the actions out.
"""

import logging

from pirtis.agent.policy import Handoff, next_step
from pirtis.agent.speech import render
from pirtis.agent.turn import gate
from pirtis.agent.understand import UnderstandError, extract
from pirtis.api.client import Client as ApiClient
from pirtis.api.types import ApiError
from pirtis.llm.client import Client as LlmClient
from pirtis.transcript.types import Action, Transcript, say

log = logging.getLogger(__name__)

SORRY = "Sorry, I didn't catch that. Could you say it again?"


def decide(t: Transcript, api: ApiClient, llm: LlmClient) -> list[Action]:
    """Returns the receptionist's next actions given the transcript so far. Never raises:
    a caller on the phone hears an apology or a colleague, never silence."""
    gated = gate(t)
    if gated is not None:
        return gated
    try:
        units = api.list_units()
        state = extract(t, llm, units)
        return render(next_step(t, state, api, units))
    except UnderstandError as e:
        log.warning("understanding failed: %s", e)
        return [say(SORRY, "neutral", 1.0)]
    except ApiError as e:
        log.warning("api error: %s", e)
        return render(Handoff("bookings_support", f"api error {e.status} {e.code}"))
    except Exception:  # anything else is our bug; the caller still gets a person
        log.exception("decide failed")
        return render(Handoff("bookings_support", "internal error"))
