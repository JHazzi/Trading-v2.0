from __future__ import annotations

import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingestion" / "prices" / "yahoo_daily_v1.py"

CANONICAL_ADDITIONS = {
    "ARCX": "ARCX",
    "ARCA": "ARCX",
    "NYSE ARCA": "ARCX",
    "NYSEARCA": "ARCX",
    "PCX": "ARCX",
}
CALENDAR_ADDITIONS = {
    "ARCX": "XNYS",
}


def _dict_assignment(tree: ast.AST, name: str) -> tuple[ast.Assign, dict[str, str]]:
    matches: list[ast.Assign] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            matches.append(node)

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one assignment to {name}, found {len(matches)}"
        )

    node = matches[0]
    if not isinstance(node.value, ast.Dict):
        raise RuntimeError(f"{name} is not a dict literal")

    try:
        value = ast.literal_eval(node.value)
    except Exception as exc:
        raise RuntimeError(f"{name} is not a literal string dict") from exc

    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in value.items()
    ):
        raise RuntimeError(f"{name} is not a string-to-string dict")

    return node, value


def inspect_source(text: str) -> dict:
    tree = ast.parse(text)
    cnode, canonical = _dict_assignment(tree, "EXCHANGE_CANONICAL_MAP")
    knode, calendars = _dict_assignment(tree, "EXCHANGE_CALENDAR_MAP")

    canonical_conflicts = {
        key: canonical.get(key)
        for key, expected in CANONICAL_ADDITIONS.items()
        if key in canonical and canonical.get(key) != expected
    }
    calendar_conflicts = {
        key: calendars.get(key)
        for key, expected in CALENDAR_ADDITIONS.items()
        if key in calendars and calendars.get(key) != expected
    }

    missing_canonical = {
        key: value
        for key, value in CANONICAL_ADDITIONS.items()
        if key not in canonical
    }
    missing_calendar = {
        key: value
        for key, value in CALENDAR_ADDITIONS.items()
        if key not in calendars
    }

    if canonical_conflicts or calendar_conflicts:
        status = "conflict"
    elif not missing_canonical and not missing_calendar:
        status = "already_applied"
    else:
        status = "ready"

    return {
        "status": status,
        "canonical_existing": canonical,
        "calendar_existing": calendars,
        "missing_canonical": missing_canonical,
        "missing_calendar": missing_calendar,
        "canonical_conflicts": canonical_conflicts,
        "calendar_conflicts": calendar_conflicts,
        "canonical_node": cnode,
        "calendar_node": knode,
    }


def _insert_before_dict_close(
    lines: list[str],
    node: ast.Assign,
    additions: dict[str, str],
) -> list[str]:
    if not additions:
        return lines
    if node.end_lineno is None:
        raise RuntimeError("Python AST did not provide end_lineno")

    # Dict assignment is expected to end on a line containing only the closing
    # brace (possibly followed by whitespace/comment). We insert immediately
    # before it, preserving every pre-existing alias and comment.
    close_index = node.end_lineno - 1
    close_line = lines[close_index]
    if "}" not in close_line:
        raise RuntimeError(
            f"Could not locate closing brace for assignment ending line "
            f"{node.end_lineno}"
        )

    assignment_line = lines[node.lineno - 1]
    base_indent = assignment_line[: len(assignment_line) - len(assignment_line.lstrip())]
    item_indent = base_indent + "    "

    new_lines = [
        f'{item_indent}{key!r}: {value!r},\n'
        for key, value in additions.items()
    ]
    return lines[:close_index] + new_lines + lines[close_index:]


def patch_text(text: str) -> tuple[str, dict]:
    info = inspect_source(text)
    if info["status"] == "conflict":
        raise RuntimeError(
            "ARCA mapping conflict: "
            f"canonical={info['canonical_conflicts']} "
            f"calendar={info['calendar_conflicts']}"
        )
    if info["status"] == "already_applied":
        return text, info

    # Insert lower block first so original AST line numbers for the canonical
    # block remain valid.
    lines = text.splitlines(keepends=True)
    lines = _insert_before_dict_close(
        lines,
        info["calendar_node"],
        info["missing_calendar"],
    )
    lines = _insert_before_dict_close(
        lines,
        info["canonical_node"],
        info["missing_canonical"],
    )
    patched = "".join(lines)

    # Parse and verify exact semantic result after modification.
    after = inspect_source(patched)
    if after["status"] != "already_applied":
        raise RuntimeError(
            f"Patch verification failed; post-status={after['status']}"
        )
    return patched, after


def status() -> dict:
    if not TARGET.is_file():
        return {"status": "missing_target", "target": str(TARGET)}
    info = inspect_source(TARGET.read_text(encoding="utf-8"))
    return {
        "status": info["status"],
        "target": str(TARGET),
        "missing_canonical": info["missing_canonical"],
        "missing_calendar": info["missing_calendar"],
        "canonical_conflicts": info["canonical_conflicts"],
        "calendar_conflicts": info["calendar_conflicts"],
    }


def apply() -> dict:
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)
    before = TARGET.read_text(encoding="utf-8")
    patched, info = patch_text(before)
    changed = patched != before
    if changed:
        TARGET.write_text(patched, encoding="utf-8")
    return {
        "status": "applied" if changed else "already_applied",
        "target": str(TARGET),
        "verified": info["status"] == "already_applied",
    }


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()

    result = apply() if a.apply else status()
    import json
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
