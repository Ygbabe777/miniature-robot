"""A tiny dependency-free PDF writer (A4, Helvetica, text + one line chart). Enough for a strategy report that opens everywhere; nothing is rendered by a browser."""
from __future__ import annotations

import textwrap

W, H, M = 595, 842, 44


def _esc(s: str) -> str:
    return s.encode("cp1252", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Doc:
    def __init__(self, title: str):
        self.title, self.pages, self.cur, self.y = title, [], [], H - M
        self._new()

    def _new(self) -> None:
        if self.cur:
            self.pages.append(self.cur)
        self.cur, self.y = [], H - M

    def _need(self, h: float) -> None:
        if self.y - h < M:
            self._new()

    def text(self, s: str, size: float = 10, bold: bool = False, indent: float = 0, gap: float = 3) -> None:
        width = max(20, int((W - 2 * M - indent) / (size * 0.52)))
        for line in (textwrap.wrap(s, width) or [""]):
            self._need(size + gap)
            self.y -= size + gap
            self.cur.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {M + indent:.1f} {self.y:.1f} Td ({_esc(line)}) Tj ET")

    def heading(self, s: str) -> None:
        self.y -= 6
        self.text(s, 12.5, bold=True, gap=4)

    def rule(self) -> None:
        self._need(6)
        self.y -= 4
        self.cur.append(f"0.6 G {M} {self.y:.1f} m {W - M} {self.y:.1f} l S")

    def table(self, rows: list[list[str]], widths: list[float], size: float = 9) -> None:
        for r in rows:
            self._need(size + 4)
            self.y -= size + 4
            x = M
            for cell, w in zip(r, widths):
                self.cur.append(f"BT /F1 {size} Tf {x:.1f} {self.y:.1f} Td ({_esc(str(cell)[:int(w / (size * 0.5))])}) Tj ET")
                x += w

    def chart(self, title: str, ys: list[float], height: float = 110) -> None:
        if len(ys) < 2:
            return
        self.text(title, 9, bold=True)
        self._need(height + 10)
        top = self.y - 4
        base = top - height
        lo, hi = min(min(ys), 0.0), max(max(ys), 0.0)
        span = (hi - lo) or 1.0
        pw = W - 2 * M
        pts = [(M + i / (len(ys) - 1) * pw, base + (v - lo) / span * height) for i, v in enumerate(ys[:: max(1, len(ys) // 400)] or ys)]
        zero = base + (0 - lo) / span * height
        self.cur.append(f"0.85 G {M} {base:.1f} {pw} {height} re S 0.6 G [3 3] 0 d {M} {zero:.1f} m {W - M} {zero:.1f} l S [] 0 d 0 G 1.1 w")
        self.cur.append(f"{pts[0][0]:.1f} {pts[0][1]:.1f} m " + " ".join(f"{x:.1f} {y:.1f} l" for x, y in pts[1:]) + " S 1 w")
        self.cur.append(f"BT /F1 7 Tf {M + 2} {base + 2:.1f} Td ({lo:,.0f}) Tj ET BT /F1 7 Tf {M + 2} {top - 8:.1f} Td ({hi:,.0f}) Tj ET")
        self.y = base - 8

    def render(self) -> bytes:
        pages = self.pages + ([self.cur] if self.cur else [])
        objs: list[bytes] = []
        n = len(pages)
        kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n))
        objs += [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
                 b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                 b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        for i, p in enumerate(pages):
            foot = f"BT /F1 7 Tf {M} 24 Td ({_esc(self.title[:80])}  -  pag. {i + 1}/{n}) Tj ET"
            body = ("\n".join(p) + "\n" + foot).encode("latin-1", "replace")
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * i} 0 R >>".encode())
            objs.append(b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream")
        out = bytearray(b"%PDF-1.4\n")
        offs = []
        for i, o in enumerate(objs, 1):
            offs.append(len(out))
            out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        x = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
        return bytes(out)
