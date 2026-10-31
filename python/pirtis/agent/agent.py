"""The receptionist.

decide runs four steps and returns what to do next:

1. turn gate (turn.py)      code only: wait while the caller is still talking
2. understand (understand.py) one model call: the call so far as a validated CallState
3. policy (policy.py)        code only: identify, ask, search, offer, and book on a yes
4. speech (speech.py)        code only: the words, with numbers and dates spoken

The model listens; code talks. handlers, in actions.py, carry the actions out.
"""

import logging

from pirtis.agent.policy import Handoff, Sorry, asked_again, next_step
from pirtis.agent.speech import render, tone
from pirtis.agent.turn import gate
from pirtis.agent.understand import UnderstandError, extract
from pirtis.api.client import Client as ApiClient
from pirtis.api.types import ApiError
from pirtis.llm.client import Client as LlmClient
from pirtis.transcript.types import Action, Transcript

log = logging.getLogger(__name__)


def decide(t: Transcript, api: ApiClient, llm: LlmClient) -> list[Action]:
    """Returns the receptionist's next actions given the transcript so far. Never raises:
    a caller on the phone hears an apology or a colleague, never silence."""
    gated = gate(t)
    if gated is not None:
        return gated
    voice = tone(t)
    try:
        units = api.list_units()
        state = extract(t, llm, units)
        return render(next_step(t, state, api, units), voice)
    except UnderstandError as e:
        log.warning("understanding failed: %s", e)
        if asked_again(t):  # the second time in a row, a person takes over
            return render(Handoff("front_desk", "two misunderstandings in a row"), voice)
        return render(Sorry(), voice)
    except ApiError as e:
        log.warning("api error: %s", e)
        return render(Handoff("bookings_support", f"api error {e.status} {e.code}"), voice)
    except Exception:  # anything else is our bug; the caller still gets a person
        log.exception("decide failed")
        return render(Handoff("bookings_support", "internal error"), voice)
