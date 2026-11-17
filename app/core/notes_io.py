"""
Notebook import / export as plain Markdown files (no Qt).

Import: .md / .markdown / .txt files or whole folders (e.g. an Obsidian vault). Sub-folders become
notebook folders, the file name becomes the title, YAML front matter `tags:` become #tags, and
images / files the note points to (Markdown links or Obsidian ![[embeds]]) are copied into the
note's attachments. [[wiki links]] already use the same syntax, so they keep working.

Export: one .md per note in a folder tree that mirrors the notebook folders; attachments are copied
next to them and the links rewritten so the files open correctly in any Markdown app.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from app.config import DATA_DIR

ATTACH_ROOT = DATA_DIR / "attachments"
TEXT_EXT = {".md", ".markdown", ".txt"}
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_EMBED = re.compile(r"!\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
_MD_LINK = re.compile(r"(!?\[[^\]]*\]\()(<[^>]+>|[^)\s]+)(\))")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _unique(folder: Path, name: str) -> Path:
    path, n = folder / name, 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def _front_matter(text: str) -> tuple[str, list[str]]:
    m = _FRONT.match(text)
    if not m:
        return text, []
    tags: list[str] = []
    block = m.group(1)
    inline = re.search(r"^tags:\s*\[(.*?)\]\s*$", block, re.M)
    if inline:
        tags = [t.strip(" '\"#") for t in inline.group(1).split(",")]
    else:
        listed = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n?)+)", block, re.M)
        if listed:
            tags = [t.strip(" -'\"#") for t in listed.group(1).splitlines()]
        single = re.search(r"^tags:\s*([^\[\n]+)$", block, re.M)
        if single and not tags:
            tags = [t.strip(" '\"#") for t in re.split(r"[,\s]+", single.group(1))]
    return text[m.end():], [re.sub(r"\s+", "-", t) for t in tags if t]


def _find_file(name: str, base: Path, root: Path) -> Path | None:
    name = name.strip()
    for candidate in (base / name, root / name):
        if candidate.is_file():
            return candidate
    target = Path(name).name.lower()
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower() == target:
                return Path(dirpath) / f
    return None


def _rel(path: Path) -> str:
    rel = path.relative_to(DATA_DIR).as_posix()
    return f"<{rel}>" if " " in rel else rel


def import_markdown(db, paths: list[str], index) -> list[int]:
    """Creates notes from files/folders. index(note_id, content) refreshes tags & links. Returns new note ids."""
    created: list[int] = []
    for raw in paths:
        src = Path(raw)
        if src.is_dir():
            root = src
            files = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXT
                           and not any(part.startswith(".") for part in p.relative_to(src).parts))
        elif src.is_file() and src.suffix.lower() in TEXT_EXT:
            root, files = src.parent, [src]
        else:
            continue
        for f in files:
            try:
                text = f.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = f.read_text(encoding="cp1256", errors="replace")
            body, front_tags = _front_matter(text)
            folder_id = None
            if src.is_dir():
                parts = [src.name, *f.relative_to(src).parent.parts]
                folder_id = db.folder_by_path(parts)
            note_id = db.add_note("", f.stem, "import", str(f), is_inbox=False)
            folder = ATTACH_ROOT / str(note_id)

            def copy_in(found: Path) -> str:
                folder.mkdir(parents=True, exist_ok=True)
                dest = _unique(folder, found.name)
                shutil.copy2(found, dest)
                return _rel(dest)

            def embed(m):
                found = _find_file(m.group(1), f.parent, root)
                if not found:
                    return m.group(0)
                if found.suffix.lower() in TEXT_EXT:
                    return f"[[{found.stem}]]"
                return f"![{found.stem}]({copy_in(found)})"

            def md_link(m):
                target = m.group(2).strip("<>")
                if re.match(r"^[a-z]+:", target, re.I) or target.startswith("#"):
                    return m.group(0)
                found = _find_file(target.replace("%20", " "), f.parent, root)
                if not found or found.suffix.lower() in TEXT_EXT:
                    return m.group(0)
                return f"{m.group(1)}{copy_in(found)}{m.group(3)}"

            body = _MD_LINK.sub(md_link, body)  # Markdown links first: the embeds below produce links too
            body = _EMBED.sub(embed, body)
            if front_tags:
                body = body.rstrip() + "\n\n" + " ".join("#" + t for t in front_tags) + "\n"
            db.update_note(note_id, content=body, folder_id=folder_id)
            index(note_id, body)
            created.append(note_id)
    return created


def _safe_name(name: str) -> str:
    name = _BAD.sub("_", name).strip(" .")[:120]
    return name or "note"


def export_markdown(db, notes: list[dict], target: Path, read_content) -> int:
    """Writes notes as .md files under target (folders mirrored). read_content(note) -> text or None."""
    folders = {f["id"]: f for f in db.list_folders()}

    def folder_path(folder_id) -> Path:
        parts = []
        while folder_id and folder_id in folders:
            parts.insert(0, _safe_name(folders[folder_id]["name"]))
            folder_id = folders[folder_id]["parent_id"]
        return Path(*parts) if parts else Path()

    written = 0
    for note in notes:
        content = read_content(note)
        if content is None:
            continue
        sub = target / folder_path(note.get("folder_id"))
        sub.mkdir(parents=True, exist_ok=True)
        out = _unique(sub, _safe_name(note["title"] or f"note-{note['id']}") + ".md")
        attach = ATTACH_ROOT / str(note["id"])
        if attach.is_dir():
            dest_dir = target / "attachments" / str(note["id"])
            shutil.copytree(attach, dest_dir, dirs_exist_ok=True)
            rel_prefix = os.path.relpath(target / "attachments", sub).replace("\\", "/")
            content = re.sub(r"\]\((<?)attachments/", lambda m: f"]({m.group(1)}{rel_prefix}/", content)
        header = f"# {note['title']}\n\n" if note["title"] else ""
        out.write_text(header + content, encoding="utf-8")
        written += 1
    return written
