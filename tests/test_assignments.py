"""Random-scene light assignment tests."""

from __future__ import annotations

from unittest.mock import patch

from custom_components.chameleon.assignments import randomized_light_order


def test_random_order_keeps_every_light_and_does_not_edit_configuration():
    configured = ["light.one", "light.two", "light.three"]
    with patch("custom_components.chameleon.assignments.random.shuffle", side_effect=lambda values: values.reverse()):
        order = randomized_light_order(configured)

    assert order == ["light.three", "light.two", "light.one"]
    assert configured == ["light.one", "light.two", "light.three"]


def test_random_order_changes_on_consecutive_random_requests():
    configured = ["light.one", "light.two", "light.three"]
    with patch("custom_components.chameleon.assignments.random.shuffle", side_effect=lambda values: None):
        first = randomized_light_order(configured)
        second = randomized_light_order(configured, first)

    assert first != configured
    assert second != first
    assert set(first) == set(second) == set(configured)


def test_one_light_has_only_one_possible_assignment():
    assert randomized_light_order(["light.one"]) == ["light.one"]
