from typing import Any, Iterable, Sequence

import jmespath

from .lib.types import DROP, KEEP, ApplyFunc, ConditionalCheck
from .lib.util import flatten_list


def get(
    source: dict[str, Any] | list[Any],
    key: str,
    default: Any = None,
    apply: ApplyFunc | Iterable[ApplyFunc] | None = None,
    only_if: ConditionalCheck | None = None,
    drop_level: DROP | None = None,
    flatten: bool = False,
) -> Any:
    """
    Gets a value from the source dictionary using a `.` syntax.
    Handles None-checking (instead of raising error, returns default).

    `key` notes:
     - Use `.` to chain gets
     - Index and slice into lists, e.g. `[0]`, `[-1]`, `[:1]`, etc.
     - Iterate through a list using `[*]`
     - Get multiple items using `[firstKey,secondKey]` syntax (outputs as a list)
       The keys within the tuple can also be chained with `.`

    Use `apply` to safely chain operations on a successful get.

    Use `only_if` to conditionally decide if the result should be kept + `apply`-ed.

    Use `drop_level` to specify conditional dropping if get results in None.

    Use `flatten` to flatten the final result (e.g. nested lists)
    """
    # Handle case where source is a list
    if isinstance(source, list):
        source = {"_": source}
        key = "_" + key

    res = _nested_get(source, key, default)

    if flatten and isinstance(res, list):
        res = flatten_list(res)

    if res is not None and only_if:
        res = res if only_if(res) else None

    if res is not None and apply:
        if not isinstance(apply, Iterable):
            apply = (apply,)
        for fn in apply:
            try:
                res = fn(res)
            except Exception as e:
                raise RuntimeError(f"`apply` call {fn} failed for value: {res} at key: {key}, {e}")
            if res is None:
                break

    if drop_level and res is None:
        res = drop_level
    return res


def _nested_get(source: dict[str, Any], key: str, default: Any = None) -> Any:
    """
    Expects `.`-delimited string and tries to get the item in the dict.

    If the dict contains an array, the correct index is expected, e.g. for a dict d:
        d.a.b[0]
      will try d['a']['b'][0], where b should be an array with at least 1 item.


    If [*] is passed, then that means get into each object in the list. E.g. for a list l:
        l[*].a.b
      will return the following: [d['a']['b'] for d in l]
    """
    if key.endswith("[*]"):
        key = key.removesuffix("[*]")
    has_tuple = "(" in key and ")" in key
    if has_tuple:
        key = key.replace("(", "[").replace(")", "]")
        res = jmespath.search(key, source)
        if isinstance(res, list):
            res = tuple(res)
    else:
        res = jmespath.search(key, source)
    if isinstance(res, list):
        res = [r if r is not None else default for r in res]
    if res is None:
        res = default
    return res


def _nested_set(
    source: dict[str, Any], tokenized_key_list: Sequence[str | int], target: Any
) -> dict[str, Any] | None:
    """
    Returns a copy of source with the replace if successful, else None.
    """
    res: Any = source
    try:
        for k in tokenized_key_list[:-1]:
            res = res[k]
        res[tokenized_key_list[-1]] = target
    except IndexError:
        return None
    return source


def _get_tokenized_keypath(key: str) -> tuple[str | int, ...]:
    """
    Returns a keypath with str and ints separated. Prefer tuples so it is hashable.

    E.g.: "a[0].b[-1].c" -> ("a", 0, "b", -1, "c")
    """
    tokenized_key = key.replace("[", ".").replace("]", "")
    keypath = tokenized_key.split(".")
    return tuple(int(k) if k.removeprefix("-").isnumeric() else k for k in keypath)


def drop_keys(source: dict[str, Any], keys_to_drop: Iterable[str]) -> dict[str, Any]:
    """
    Returns the dictionary with the requested keys set to `None`.

    If a key is a duplicate, then lookup fails so that key is skipped.

    DROP values are checked and handled here.
    """
    res = source
    seen_keys = set()
    for key in keys_to_drop:
        curr_keypath = _get_tokenized_keypath(key)
        if curr_keypath not in seen_keys:
            if v := _nested_get(res, key):
                # Check if value has a DROP object
                if isinstance(v, DROP):
                    # If "out of bounds", raise an error
                    if v.value > 0 or -1 * v.value > len(curr_keypath):
                        raise RuntimeError(f"Error: DROP level {v} at {key} is invalid")
                    curr_keypath = curr_keypath[: v.value]
                    # Handle case for dropping entire object
                    if len(curr_keypath) == 0:
                        return dict()
                if updated := _nested_set(res, curr_keypath, None):
                    res = updated
                seen_keys.add(curr_keypath)
        else:
            seen_keys.add(curr_keypath)
    return res


def impute_enum_values(source: dict[str, Any], keys_to_impute: set[str]) -> dict[str, Any]:
    """
    Returns the dictionary with the Enum values set to their corresponding `.value`
    """
    res = source
    for key in keys_to_impute:
        curr_val = _nested_get(res, key)
        if isinstance(curr_val, KEEP):
            literal_val = curr_val.value
            res = _nested_set(res, _get_tokenized_keypath(key), literal_val)  # type: ignore
    return res
