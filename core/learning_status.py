"""Structured deep_learn progress: topic, current subtopic, and a 0..1
fraction — for anything that wants more than the plain status strings
`core.research.run_deep_learn`'s `progress` callback already provides.
Currently used by the HUD's voice-orb `learning` state and its subtopic
caption (ODIN-HUD.md §5.3/§6.4).

Same module-level-singleton shape as core.store.get_store()/
core.undo.get_journal(): a settable callback rather than an import-time
dependency, so tests can swap it out and non-HUD entry points (main.py's
console loop) simply never set one — report() is then just a no-op.
"""
from collections.abc import Callable

_callback: Callable[[str, str, float], None] | None = None


def set_callback(callback: Callable[[str, str, float], None] | None) -> None:
    global _callback
    _callback = callback


def report(topic: str, subtopic: str, progress: float) -> None:
    if _callback is not None:
        _callback(topic, subtopic, progress)
