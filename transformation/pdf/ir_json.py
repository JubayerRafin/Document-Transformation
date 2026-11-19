"""BabelDOC's document model as JSON-ready data, without recursion.

dataclasses.asdict() recurses through the model and deep-copies every value that is not a
dataclass, list, tuple or dict. Dense pages (a timetable of 350 cells, a 50-page printer manual)
hold such a value with a long chain inside, and the deep copy exceeded Python's recursion limit:
"maximum recursion depth exceeded", the whole translation failed (2026-11-19). Here the model is
walked with an explicit stack; values that are not plain data become their type name.
"""
import dataclasses
from types import SimpleNamespace
import json

PLAIN = (str, int, float, bool, type(None))


def to_data(root):
    """A dict/list/str/number copy of a dataclass model, built iteratively."""
    def convert(value):
        if isinstance(value, PLAIN):
            return value, None
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            out = {}
            return out, [(out, field.name, getattr(value, field.name)) for field in dataclasses.fields(value)]
        if isinstance(value, (list, tuple)):
            out = [None] * len(value)
            return out, [(out, index, item) for index, item in enumerate(value)]
        if isinstance(value, dict):
            out = {}
            return out, [(out, str(key), item) for key, item in value.items()]
        if isinstance(value, (bytes, bytearray)):
            return value.hex(), None
        return f'<{type(value).__name__}>', None

    result, children = convert(root)
    stack = list(reversed(children or []))
    while stack:
        container, key, value = stack.pop()
        converted, more = convert(value)
        container[key] = converted
        if more:
            stack.extend(reversed(more))
    return result


def dumps(document):
    return json.dumps(to_data(document), ensure_ascii=False)


def ir_copy(value):
    """copy.deepcopy for the document model that shares, instead of copying, values that are not
    plain data (BabelDOC's lazily parsed graphics instructions, LazyPassthroughInstruction): copying
    them recursed past Python's limit on dense pages. They are read, never changed, by the engine."""
    import copy
    memo, stack, seen = {}, [value], set()
    while stack:
        item = stack.pop()
        if isinstance(item, PLAIN) or id(item) in seen:
            continue
        seen.add(id(item))
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            stack.extend(getattr(item, field.name) for field in dataclasses.fields(item))
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.extend(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, SimpleNamespace):
            stack.extend(vars(item).values())
        else:
            memo[id(item)] = item
    return copy.deepcopy(value, memo)
