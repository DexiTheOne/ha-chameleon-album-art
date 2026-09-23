"""Light ordering for Random-scene palette assignment."""

from __future__ import annotations

import random


def randomized_light_order(light_entities: list[str], previous: list[str] | None = None) -> list[str]:
    """Shuffle a copy, avoiding the previous order when there is a choice."""
    order = list(light_entities)
    random.shuffle(order)
    if len(order) > 1 and (order == previous or (previous is None and order == light_entities)):
        order = order[1:] + order[:1]
    return order
