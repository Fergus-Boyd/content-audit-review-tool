"""Widget state helpers for the Streamlit app.

These exist because of one Streamlit behaviour that is easy to get wrong and
hard to see. A keyed widget's identity does not change when its label,
options or bounds change, and a widget only pushes a value to the browser
when its key was ASSIGNED during that run. Deleting a key assigns nothing, so
the browser keeps its own copy of the old value and sends it straight back on
the next interaction.

The visible result is a reset that looks like it worked and then silently
undoes itself. Worse, Streamlit's own test tools cannot reproduce it: their
"frontend" is rebuilt from the server every run, so there is no stale copy to
resend, and a test asserting the value is empty passes while the real app is
broken.

So resetting works by changing the key rather than clearing it. Each filter
carries a number; the key includes it; bumping the number makes a brand new
widget that has never held anything. Streamlit culls the state behind the old
keys on the next run.

No Streamlit import here, so every function can be tested with a plain
dictionary.
"""

from __future__ import annotations

from typing import Iterable, MutableMapping

GENERATION_KEY = "widget_generations"


def _generations(state: MutableMapping) -> dict:
    if GENERATION_KEY not in state:
        state[GENERATION_KEY] = {}
    return state[GENERATION_KEY]


def generation(state: MutableMapping, name: str) -> int:
    """The current number for one filter. Starts at zero."""
    return _generations(state).get(name, 0)


def bump(state: MutableMapping, name: str) -> int:
    """Give a filter a new number, which gives it new widgets."""
    generations = _generations(state)
    generations[name] = generations.get(name, 0) + 1
    # Reassign so Streamlit sees the mapping as written this run.
    state[GENERATION_KEY] = generations
    return generations[name]


def widget_key(state: MutableMapping, name: str, part: str) -> str:
    """The session key for one widget belonging to one filter.

    A filter can own several widgets: a values box, two number boxes, an
    exclude checkbox. They all share the filter's number, so one bump clears
    the whole filter at once."""
    return f"f_{name}_{part}_{generation(state, name)}"


def clear_filter(state: MutableMapping, name: str) -> None:
    """Forget everything one filter holds."""
    bump(state, name)


def clear_filters(state: MutableMapping, names: Iterable[str]) -> None:
    for name in names:
        bump(state, name)
