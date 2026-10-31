"""Keeps the real generation model tests out of a run until their opt-in variable is set."""

from __future__ import annotations

import os

import pytest

REAL_GENERATION_OPT_IN = "NARRASTEP_RUN_REAL_GENERATION_TESTS"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect the tests marked `real_generation` unless the opt-in variable is exactly `1`.

    The summary of the run counts them as deselected. Other tests are left alone, and this never
    fails. With the opt-in the tests run, and they fail when the pinned files are missing, because
    a proof that quietly did not happen would read as a pass.
    """
    if os.environ.get(REAL_GENERATION_OPT_IN) == "1":
        return
    selected = [item for item in items if item.get_closest_marker("real_generation") is None]
    if len(selected) < len(items):
        config.hook.pytest_deselected(
            items=[item for item in items if item.get_closest_marker("real_generation")]
        )
        items[:] = selected
