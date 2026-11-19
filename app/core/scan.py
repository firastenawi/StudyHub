"""
Document scanning from a phone photo, with Qt only (no OpenCV / numpy):

  find_page   where the sheet of paper is: the largest bright region of a small grey copy, its four extreme
              corners. Falls back to a frame slightly inside the photo when nothing page-like is found.
  straighten  perspective correction of those four corners into a flat rectangle (QTransform.quadToQuad)
  looks       original | document (white paper, dark ink: divide by a blurred copy of itself, which flattens
              shadows and uneven light; then deepen the ink) | grey
"""
from __future__ import annotations

import math
from collections import deque

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPolygonF, QTransform

MAX_SIDE = 3200      # bigger photos are scaled down first (plenty for A4 text, keeps it fast)
DETECT_WIDTH = 160


def limit(img: QImage) -> QImage:
    if max(img.width(), img.height()) > MAX_SIDE:
        return img.scaled(QSize(MAX_SIDE, MAX_SIDE), Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    return img


def default_quad(w: float, h: float, inset: float = 0.08) -> list[QPointF]:
    dx, dy = w * inset, h * inset
    return [QPointF(dx, dy), QPointF(w - dx, dy), QPointF(w - dx, h - dy), QPointF(dx, h - dy)]


def _otsu(hist: list[int], total: int) -> int:
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_b = w_b = 0
    best, best_t = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if not w_b:
            continue
        w_f = total - w_b
        if not w_f:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, best_t = between, t
    return best_t


def _area(q: list[QPointF]) -> float:
    return abs(sum(q[i].x() * q[(i + 1) % 4].y() - q[(i + 1) % 4].x() * q[i].y() for i in range(4))) / 2


def find_page(img: QImage) -> tuple[list[QPointF], bool]:
    """Four corners (top-left, top-right, bottom-right, bottom-left) in img's pixels, and whether a page was
    really found (False: a default frame)."""
    w, h = img.width(), img.height()
    if w < 8 or h < 8:
        return default_quad(w, h), False
    small = img.scaledToWidth(DETECT_WIDTH, Qt.TransformationMode.SmoothTransformation).convertToFormat(
        QImage.Format.Format_Grayscale8)
    sw, sh = small.width(), small.height()
    bpl = small.bytesPerLine()
    ptr = small.constBits()
    ptr.setsize(small.sizeInBytes())
    data = bytes(ptr)
    px = [data[y * bpl:y * bpl + sw] for y in range(sh)]
    hist = [0] * 256
    for row in px:
        for v in row:
            hist[v] += 1
    thr = _otsu(hist, sw * sh)
    seen = bytearray(sw * sh)
    best: list[tuple[int, int]] = []
    for y0 in range(sh):
        row = px[y0]
        for x0 in range(sw):
            if row[x0] <= thr or seen[y0 * sw + x0]:
                continue
            comp = []
            q = deque([(x0, y0)])
            seen[y0 * sw + x0] = 1
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < sw and 0 <= ny < sh and not seen[ny * sw + nx] and px[ny][nx] > thr:
                        seen[ny * sw + nx] = 1
                        q.append((nx, ny))
            if len(comp) > len(best):
                best = comp
    if len(best) < 0.12 * sw * sh:
        return default_quad(w, h), False
    tl = min(best, key=lambda p: p[0] + p[1])
    br = max(best, key=lambda p: p[0] + p[1])
    tr = max(best, key=lambda p: p[0] - p[1])
    bl = min(best, key=lambda p: p[0] - p[1])
    sx, sy = w / sw, h / sh
    quad = [QPointF((x + 0.5) * sx, (y + 0.5) * sy) for x, y in (tl, tr, br, bl)]
    # the whole photo is "bright" (white desk) or the shape is degenerate -> not a page
    if _area(quad) < 0.12 * w * h or _area(quad) > 0.985 * w * h:
        return default_quad(w, h), False
    return quad, True


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def straighten(img: QImage, quad: list[QPointF]) -> QImage:
    """The quad, flattened into a rectangle (its size from the quad's edges)."""
    tl, tr, br, bl = quad
    w = max(8, int(max(_dist(tl, tr), _dist(bl, br))))
    h = max(8, int(max(_dist(tl, bl), _dist(tr, br))))
    t = QTransform()
    if not QTransform.quadToQuad(QPolygonF(quad), QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)]), t):
        return img.copy()
    out = QImage(w, h, QImage.Format.Format_RGB32)
    out.fill(QColor("white"))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setTransform(t)
    p.drawImage(0, 0, img)
    p.end()
    return out


def grey(img: QImage) -> QImage:
    return img.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(QImage.Format.Format_RGB32)


def document(img: QImage) -> QImage:
    """White paper, dark ink: each pixel divided by the local background (a heavily blurred copy), via
    QPainter's colour-dodge with the inverted background (dst / (1 - (1 - bg)) = dst / bg)."""
    g = grey(img)
    w, h = g.width(), g.height()
    small = g.scaled(max(1, w // 28), max(1, h // 28), Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    bg = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    bg.invertPixels()
    out = QImage(g)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_ColorDodge)
    p.drawImage(0, 0, bg)
    p.end()
    ink = QImage(out)  # multiply with itself: light greys fade to white, strokes get darker
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    p.drawImage(0, 0, ink)
    p.end()
    return out


LOOKS = ("document", "original", "grey")


def apply_look(img: QImage, look: str) -> QImage:
    if look == "document":
        return document(img)
    if look == "grey":
        return grey(img)
    return img


def save_pdf(img: QImage, path: str) -> None:
    """One A4 page (portrait or landscape like the scan), the image filling it with a small margin."""
    from PyQt6.QtGui import QPageLayout, QPageSize, QPdfWriter
    from PyQt6.QtCore import QMarginsF
    writer = QPdfWriter(path)
    writer.setResolution(300)
    landscape = img.width() > img.height()
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                     QPageLayout.Orientation.Landscape if landscape else QPageLayout.Orientation.Portrait,
                                     QMarginsF(8, 8, 8, 8), QPageLayout.Unit.Millimeter))
    p = QPainter(writer)
    area = QRectF(p.viewport())
    size = img.size().scaled(area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
    target = QRectF(area.x() + (area.width() - size.width()) / 2, area.y(), size.width(), size.height())
    p.drawImage(target, img)
    p.end()
