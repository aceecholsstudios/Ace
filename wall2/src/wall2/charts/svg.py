"""Per-trade chart image as a self-contained SVG (no plotting dependency): 5-min candles, VWAP,
9 EMA, the pullback trigger level, and entry/exit markers. Embedded in the daily report."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ChartBar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    ema: float | None
    vwap: float | None


@dataclass(frozen=True, slots=True)
class Marker:
    ts: datetime
    price: float
    label: str
    kind: str  # "entry" | "exit"


W, H, PAD_L, PAD_R, PAD_T, PAD_B = 720, 320, 52, 16, 46, 26
MIN_SLOTS = 24  # keeps candles a sensible width when there are only a few bars
UP, DOWN, VWAP_C, EMA_C, TRIG_C, ENTRY_C, EXIT_C, GRID = (
    "#1a7f37",
    "#c62828",
    "#7a5af8",
    "#e8a33d",
    "#6e6e73",
    "#0b6bcb",
    "#111111",
    "#d9d9de",
)


def render_trade_svg(
    bars: list[ChartBar],
    title: str,
    markers: list[Marker],
    trigger_level: float | None = None,
) -> str:
    if not bars:
        return ""
    lows = [b.low for b in bars] + [m.price for m in markers]
    highs = [b.high for b in bars] + [m.price for m in markers]
    for b in bars:
        lows += [v for v in (b.ema, b.vwap) if v is not None]
        highs += [v for v in (b.ema, b.vwap) if v is not None]
    if trigger_level is not None:
        lows.append(trigger_level)
        highs.append(trigger_level)
    lo, hi = min(lows), max(highs)
    pad = (hi - lo) * 0.06 or 0.5
    lo, hi = lo - pad, hi + pad
    n = max(len(bars), MIN_SLOTS)
    step = (W - PAD_L - PAD_R) / n
    t0 = bars[0].start

    def x(i: float) -> float:
        return PAD_L + step * (i + 0.5)

    def y(p: float) -> float:
        return PAD_T + (hi - p) / (hi - lo) * (H - PAD_T - PAD_B)

    def x_at(ts: datetime) -> float:
        """Center of the 5-min bar containing ts (an exit at 8:50 belongs to the 8:45 bar)."""
        i = int(((ts - t0).total_seconds() - 1) // 300)
        return x(min(max(i, 0), len(bars) - 1))

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'font-family="system-ui,sans-serif" font-size="11"><rect width="{W}" height="{H}" fill="#fff"/>',
        f'<text x="{PAD_L}" y="18" font-size="13" font-weight="600">{html.escape(title)}</text>',
    ]
    for k in range(5):
        p = lo + (hi - lo) * k / 4
        out.append(
            f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(p):.1f}" y2="{y(p):.1f}" stroke="{GRID}"/>'
            f'<text x="{PAD_L - 6}" y="{y(p) + 4:.1f}" text-anchor="end" fill="#6e6e73">{p:.2f}</text>'
        )
    for i, b in enumerate(bars):
        if i % 6 == 0:
            out.append(
                f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="middle" fill="#6e6e73">'
                f"{b.start:%H:%M}</text>"
            )
        c = UP if b.close >= b.open else DOWN
        bw = max(step * 0.6, 1.5)
        top, bot = y(max(b.open, b.close)), y(min(b.open, b.close))
        out.append(
            f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{y(b.high):.1f}" y2="{y(b.low):.1f}" stroke="{c}"/>'
            f'<rect x="{x(i) - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
            f'height="{max(bot - top, 1):.1f}" fill="{c}"/>'
        )
    for attr, color in (("vwap", VWAP_C), ("ema", EMA_C)):
        pts = [
            f"{x(i):.1f},{y(v):.1f}"
            for i, b in enumerate(bars)
            if (v := getattr(b, attr)) is not None
        ]
        if len(pts) > 1:
            out.append(
                f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.6"/>'
            )
    if trigger_level is not None:
        out.append(
            f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(trigger_level):.1f}" y2="{y(trigger_level):.1f}" '
            f'stroke="{TRIG_C}" stroke-dasharray="4 4"/>'
        )
    for m in markers:
        mx, my = x_at(m.ts), y(m.price)
        col = ENTRY_C if m.kind == "entry" else EXIT_C
        tri = (
            f"{mx:.1f},{my + 10:.1f} {mx - 6:.1f},{my + 20:.1f} {mx + 6:.1f},{my + 20:.1f}"
            if m.kind == "entry"
            else f"{mx:.1f},{my - 10:.1f} {mx - 6:.1f},{my - 20:.1f} {mx + 6:.1f},{my - 20:.1f}"
        )
        ty = my + 32 if m.kind == "entry" else my - 24
        anchor = "end" if mx > W - PAD_R - 40 else "start" if mx < PAD_L + 40 else "middle"
        out.append(
            f'<polygon points="{tri}" fill="{col}"/><text x="{mx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" '
            f'fill="{col}" font-weight="600">{html.escape(m.label)}</text>'
        )
    lx = PAD_L
    for i, (label, color, dash) in enumerate(
        (("VWAP", VWAP_C, ""), ("EMA 9", EMA_C, ""), ("trigger", TRIG_C, ' stroke-dasharray="4 4"'))
    ):
        out.append(
            f'<line x1="{lx + i * 78}" x2="{lx + i * 78 + 18}" y1="32" y2="32" stroke="{color}" '
            f'stroke-width="2"{dash}/><text x="{lx + i * 78 + 23}" y="36" fill="#6e6e73">{label}</text>'
        )
    out.append("</svg>")
    return "".join(out)
