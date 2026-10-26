"""Helpers for resolving user-supplied paths within trusted directories."""
import os


def resolve_within(path, roots, *, must_exist=False):
    """Return a canonical path only when it stays inside one of ``roots``."""
    if not isinstance(path, (str, os.PathLike)):
        return None
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    candidate = os.path.realpath(
        os.path.expandvars(os.path.expanduser(raw)))
    if must_exist and not os.path.exists(candidate):
        return None
    for root in roots:
        trusted = os.path.realpath(os.path.expanduser(os.fspath(root)))
        try:
            common = os.path.commonpath((trusted, candidate))
        except (OSError, ValueError):
            continue
        if os.path.normcase(common) == os.path.normcase(trusted):
            return candidate
    return None


def resolve_user_path(path, *, must_exist=False):
    """Resolve a path only if its canonical target is in the current user's home."""
    return resolve_within(path, (os.path.expanduser("~"),),
                          must_exist=must_exist)
