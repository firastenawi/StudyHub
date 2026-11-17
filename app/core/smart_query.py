"""
What the command palette understands besides searching (Arabic or English):

  calculator      12*7+3 · (2^10)/4 · sqrt(81) · 15% of 240
  conversions     5 GB to MB · 5 GB بالـ MB · 90 min to h · 3 km in m · 100 Mbps to MB/s
  number bases    0xff · 0b1010 · 255 to bin / hex / oct
  subnets         192.168.1.0/26 → network, broadcast, mask, first–last host, host count
  quick note      note: … / ملاحظة: … → saved to the notebook at once
  quick open      open last pdf / افتح آخر pdf · open downloads / افتح التنزيلات · ssh web-01

Everything here is pure parsing — nothing is evaluated with eval().
"""
from __future__ import annotations

import ast
import ipaddress
import math
import operator
import re

# ------------------------------------------------------------------ calculator
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda x: math.sin(math.radians(x)), "cos": lambda x: math.cos(math.radians(x)),
          "tan": lambda x: math.tan(math.radians(x)), "log": math.log10, "ln": math.log, "abs": abs,
          "round": round, "floor": math.floor, "ceil": math.ceil}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and len(node.args) == 1:
        return _FUNCS[node.func.id](_eval(node.args[0]))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    raise ValueError("not math")


def calculate(text: str):
    t = text.strip().replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*(?:of|من)\s*(\d+(?:\.\d+)?)", r"(\1/100*\2)", t)
    if not re.search(r"\d", t) or not re.fullmatch(r"[\d\s.+\-*/%()a-z]*", t) or not re.search(r"[+\-*/%(]|[a-z]", t):
        return None
    try:
        value = _eval(ast.parse(t, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            value = int(value)
        else:
            value = round(value, 10)
    return value


# ------------------------------------------------------------------ conversions
_BYTES = {"b": 1, "byte": 1, "bytes": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4,
          "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}
_BITS = {"bit": 1 / 8, "kbit": 1000 / 8, "mbit": 1e6 / 8, "gbit": 1e9 / 8}
_RATES = {"bps": 1 / 8, "kbps": 1e3 / 8, "mbps": 1e6 / 8, "gbps": 1e9 / 8, "b/s": 1, "kb/s": 1e3, "mb/s": 1e6,  # network: decimal
          "gb/s": 1e9}
_TIME = {"ms": 0.001, "s": 1, "sec": 1, "min": 60, "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
         "day": 86400, "days": 86400, "week": 604800, "ثانية": 1, "دقيقة": 60, "دقايق": 60, "ساعة": 3600, "يوم": 86400}
_LEN = {"mm": 0.001, "cm": 0.01, "m": 1, "km": 1000, "in": 0.0254, "ft": 0.3048, "mi": 1609.344}
_TABLES = [_BYTES | _BITS, _RATES, _TIME, _LEN]
_CONV = re.compile(r"^\s*([\d.]+)\s*([a-z/؀-ۿ]+)\s+(?:to|in|into|=|الى|إلى|لـ|ل|بالـ|بال|ب)\s*([a-z/؀-ۿ]+)\s*$")


def convert(text: str):
    m = _CONV.match(text.strip().lower())
    if not m:
        return None
    value, src, dst = float(m.group(1)), m.group(2), m.group(3)
    for table in _TABLES:
        if src in table and dst in table:
            out = value * table[src] / table[dst]
            out = int(out) if float(out).is_integer() else round(out, 6)
            return f"{m.group(1)} {src} = {out:,} {dst}" if isinstance(out, int) else f"{m.group(1)} {src} = {out} {dst}"
    return None


_BASES = {"bin": 2, "binary": 2, "ثنائي": 2, "hex": 16, "ست عشري": 16, "oct": 8, "dec": 10, "عشري": 10}


def bases(text: str):
    t = text.strip().lower()
    m = re.fullmatch(r"(0x[0-9a-f]+|0b[01]+|0o[0-7]+)", t)
    if m:
        n = int(m.group(1), 0)
        return f"{t} = {n}  ·  bin {n:b}  ·  hex {n:X}  ·  oct {n:o}"
    m = re.fullmatch(r"(\d+)\s+(?:to|in|الى|إلى|ل|بال)\s*(bin|binary|hex|oct|dec|ثنائي|عشري)", t)
    if m:
        n, base = int(m.group(1)), _BASES[m.group(2)]
        val = {2: f"{n:b}", 16: f"{n:X}", 8: f"{n:o}", 10: str(n)}[base]
        return f"{n} = {val} ({m.group(2)})"
    return None


def subnet(text: str):
    t = text.strip()
    if "/" not in t:
        return None
    try:
        net = ipaddress.ip_network(t, strict=False)
    except ValueError:
        return None
    if net.version == 4:
        hosts = max(0, net.num_addresses - 2) if net.prefixlen < 31 else net.num_addresses
        first = net.network_address + (1 if net.prefixlen < 31 else 0)
        last = net.broadcast_address - (1 if net.prefixlen < 31 else 0)
        return (f"{net.with_prefixlen}  ·  mask {net.netmask}  ·  broadcast {net.broadcast_address}  ·  "
                f"hosts {first} – {last}  ({hosts:,})")
    return f"{net.with_prefixlen}  ·  {net.num_addresses:,} addresses"


# ------------------------------------------------------------------ intents
_NOTE = re.compile(r"^\s*(?:note|ملاحظة|نوت)\s*[:：]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_OPEN_LAST_PDF = re.compile(r"^\s*(?:open\s+(?:the\s+)?last\s+pdf|افتح\s+(?:آخر|اخر)\s+(?:pdf|ملف\s*pdf))\s*$", re.IGNORECASE)
_OPEN = re.compile(r"^\s*(?:open|افتح|فتح)\s+(.+)$", re.IGNORECASE)
_SSH = re.compile(r"^\s*ssh\s+(.+)$", re.IGNORECASE)

FOLDER_WORDS = {"downloads": "download", "التنزيلات": "download", "تنزيلات": "download", "documents": "documents",
                "المستندات": "documents", "desktop": "desktop", "سطح المكتب": "desktop", "pictures": "pictures",
                "الصور": "pictures", "music": "music", "videos": "movies", "الفيديوهات": "movies"}


def parse(text: str) -> list[dict]:
    """Recognised meanings of the text: [{"type": ..., ...}] (the palette turns them into results)."""
    out = []
    m = _NOTE.match(text)
    if m and m.group(1).strip():
        out.append({"type": "note", "text": m.group(1).strip()})
    if _OPEN_LAST_PDF.match(text):
        out.append({"type": "last_pdf"})
    else:
        m = _OPEN.match(text)
        if m:
            what = m.group(1).strip().lower()
            if what in FOLDER_WORDS:
                out.append({"type": "folder", "which": FOLDER_WORDS[what]})
            else:
                out.append({"type": "open", "what": m.group(1).strip()})
    m = _SSH.match(text)
    if m:
        out.append({"type": "ssh", "host": m.group(1).strip()})
    for kind, fn in (("subnet", subnet), ("base", bases), ("convert", convert)):
        value = fn(text)
        if value:
            out.append({"type": kind, "value": value})
    value = calculate(text)
    if value is not None and not out:
        out.append({"type": "calc", "value": value, "expr": text.strip()})
    return out
