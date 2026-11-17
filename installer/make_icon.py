"""Builds installer/studyhub.ico from the in-app logo (all Windows sizes, PNG-compressed), plus one icon per
app (installer/icons/<key>.ico) and installer/apps.json — the Start-menu shortcuts install.ps1 creates."""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.theme import app_icon_pixmap  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def png_bytes(size: int, draw=None) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    (draw or app_icon_pixmap)(size).save(buf, "PNG")
    return bytes(buf.data())


def write_ico(out: Path, draw=None) -> None:
    images = [(s, png_bytes(s, draw)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + entries + blobs)


def app_icons() -> None:
    """icons/<key>.ico for every app that gets a Start-menu shortcut, and apps.json describing them."""
    import ast
    import json
    from app.core import i18n
    from app.ui import apps
    folder = ROOT / "installer" / "icons"
    folder.mkdir(exist_ok=True)
    entries = []
    infos = [a for a in apps.BUILTIN if a.kind != "action"]
    for key, (color, group, desc) in apps.PLUGIN_STYLE.items():  # built-in plugins: glyph from their manifest
        init = ROOT / "app" / "plugins" / key / "__init__.py"
        tree = ast.parse(init.read_text(encoding="utf-8"))
        meta = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", "") == "PLUGIN" for t in n.targets))
        info = apps.AppInfo(key, meta["glyph"], color, f"nav_{key}", desc, group)
        info.extra["names"] = meta.get("name", {})  # plugin titles live in the plugin, not in i18n
        infos.append(info)
    for info in infos:
        write_ico(folder / f"{info.key}.ico", lambda size, i=info: apps.app_tile_pixmap(i.color, i.glyph, size))
        names = info.extra.get("names") or {}
        en = names.get("en") or i18n.tr_in("en", info.title_key)
        ar = names.get("ar") or i18n.tr_in("ar", info.title_key)
        entries.append({"key": info.key, "name": f"{en} - {ar}" if en != ar else en, "app_id": info.app_id,
                        "description": i18n.tr_in("en", info.desc_key)})
    (ROOT / "installer" / "apps.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written {len(entries)} app icons + apps.json")


def main() -> None:
    app = QApplication([])  # must stay referenced while drawing (else Qt crashes)
    images =[(s, png_bytes(s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))           # ICONDIR
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:                                  # ICONDIRENTRY (256 is stored as 0)
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out = ROOT / "installer" / "studyhub.ico"
    out.write_bytes(header + entries + blobs)
    print(f"written {out} ({out.stat().st_size} bytes, {len(images)} sizes)")
    app_icons()


if __name__ == "__main__":
    main()
