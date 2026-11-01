"""The rules on the held-out variants, at zero tokens: every variant the kit's API kept
passes the kit's checks and decides as its source does, when the caller is heard right
(pirtis/ops/variant_replay.py). The live model and judge are `make heldout`'s part."""

from pirtis.ops.variant_replay import replay


def test_the_rules_hold_on_every_held_out_variant() -> None:
    out = replay()
    assert len(out) >= 50
    failed = [f"{o.variant}: {o.why}" for o in out if not o.passed]
    assert not failed, failed
