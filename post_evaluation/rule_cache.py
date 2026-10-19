"""Bounded process-local cache of successfully validated interpretations only."""
from collections import OrderedDict
from copy import deepcopy
import json
import os
from threading import Lock

from .semantic_rules import CONTRACT

_entries = OrderedDict()
_lock = Lock()


def cache_key(label, summary, source_hint, ranking, model):
    return json.dumps([CONTRACT, "named-inputs-v3", os.environ.get("PRODUCT_EVAL_DATA_DIR", "local_data"), model,
                       label, summary, source_hint, ranking], sort_keys=True, ensure_ascii=False)


def get(key):
    with _lock:
        if key not in _entries:
            return None
        _entries.move_to_end(key)
        return deepcopy(_entries[key])


def put(key, value):
    with _lock:
        _entries[key] = deepcopy(value)
        _entries.move_to_end(key)
        while len(_entries) > 128:
            _entries.popitem(last=False)
