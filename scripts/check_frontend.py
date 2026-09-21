import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "frontend")
errors = []
files_seen = set()

IMPORT_RE = re.compile(r"""import\s+\{[^}]*\}\s+from\s+["']([^"']+)["']""")
NAMED_RE = re.compile(r"""import\s*\{([^}]*)\}\s*from\s+["']([^"']+)["']""")
EXPORT_LIST_RE = re.compile(r"""export\s*\{([^}]*)\}""")
EXPORT_DECL_RE = re.compile(r"""export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)""")
DEF_RE = re.compile(r"""(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)""")


def exports_of(path: Path) -> set:
    names = set()
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        return names
    for m in EXPORT_DECL_RE.finditer(src):
        names.add(m.group(1))
    for m in EXPORT_LIST_RE.finditer(src):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            item = part.split(" as ")[0].strip()
            if item and item != "*":
                names.add(item)
    return names


def tempsrc_named_imports(src: str):
    return [(m[0].split(",")[0].strip(), m[1]) for m in []]


def main():
    js_files = sorted(ROOT.rglob("*.js"))
    exports_cache = {p.resolve(): exports_of(p) for p in js_files}

    for path in js_files:
        try:
            src = path.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"[read] {path.relative_to(ROOT)}: {e}")
            continue
        rel = path.relative_to(ROOT).as_posix()

        # named imports
        for m in NAMED_RE.finditer(src):
            names_part, spec = m.group(1), m.group(2)
            target = (path.parent / spec).resolve()
            if not target.exists():
                errors.append(f"[missing module] {rel} imports '{spec}' -> not found")
                continue
            if target not in exports_cache:
                errors.append(f"[module not js] {rel} imports '{spec}'")
                continue
            target_exports = exports_cache[target]
            for part in names_part.split(","):
                part = part.strip()
                if not part:
                    continue
                if " as " in part:
                    part = part.split(" as ")[0].strip()
                if part == "*" or part == "default":
                    continue
                if part not in target_exports:
                    errors.append(f"[bad export] {rel}: '{part}' is not exported by '{spec}'")

        # balance check
        for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
            if src.count(open_c) != src.count(close_c):
                errors.append(
                    f"[balance] {rel}: {open_c}={src.count(open_c)} vs {close_c}={src.count(close_c)}"
                )

    # check every frontend html references existing files
    for html in ROOT.rglob("*.html"):
        h = html.read_text(encoding="utf-8")
        for m in re.finditer(r"""(?:src|href)="(/[^"]+)""", h):
            ref = m.group(1)
            candidate = ROOT / ref.lstrip("/")
            if not candidate.exists():
                errors.append(f"[html ref] {html.relative_to(ROOT)} references missing '{ref}'")

    if errors:
        print(f"FOUND {len(errors)} ISSUE(S)")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print(f"OK: checked {len(js_files)} js modules + html refs, no issues")


if __name__ == "__main__":
    main()