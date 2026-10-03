"""pyqtgraph pieces for the dashboard: a candlestick item and a time axis labelled in CT."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pyqtgraph as pg
from PySide6 import QtCore, QtGui

from wall2.charts.svg import ChartBar
from wall2.core.clock import CT

BAR_SECONDS = 300


class CTAxis(pg.AxisItem):  # type: ignore[misc]
    """X axis in epoch seconds, labelled HH:MM Central Time regardless of the PC's time zone."""

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:  # noqa: N802
        return [datetime.fromtimestamp(v, CT).strftime("%H:%M") for v in values]


class CandlestickItem(pg.GraphicsObject):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        self._bars: list[ChartBar] = []
        self._picture = QtGui.QPicture()

    def set_bars(self, bars: list[ChartBar]) -> None:
        self._bars = bars
        self._picture = QtGui.QPicture()
        p = QtGui.QPainter(self._picture)
        w = BAR_SECONDS * 0.3
        for b in bars:
            x = b.start.timestamp() + BAR_SECONDS / 2
            color = QtGui.QColor("#1a7f37" if b.close >= b.open else "#c62828")
            p.setPen(pg.mkPen(color))
            p.drawLine(QtCore.QPointF(x, b.low), QtCore.QPointF(x, b.high))
            p.setBrush(pg.mkBrush(color))
            p.drawRect(QtCore.QRectF(x - w, b.open, 2 * w, b.close - b.open))
        p.end()
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter: QtGui.QPainter, *args: Any) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return QtCore.QRectF(self._picture.boundingRect())
