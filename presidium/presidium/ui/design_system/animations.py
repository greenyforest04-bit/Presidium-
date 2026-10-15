from __future__ import annotations
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QPoint, QSize, QParallelAnimationGroup, QSequentialAnimationGroup
from PySide6.QtWidgets import QWidget
from typing import Optional


def fade_in(widget: QWidget, duration: int = 200, start: float = 0.0, end: float = 1.0) -> QPropertyAnimation:
    widget.setWindowOpacity(start)
    anim = QPropertyAnimation(widget, b"windowOpacity")
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    return anim


def slide_up(widget: QWidget, distance: int = 20, duration: int = 250) -> QPropertyAnimation:
    widget.move(widget.x(), widget.y() + distance)
    anim = QPropertyAnimation(widget, b"pos")
    anim.setDuration(duration)
    anim.setStartValue(QPoint(widget.x(), widget.y() + distance))
    anim.setEndValue(QPoint(widget.x(), widget.y()))
    anim.setEasingCurve(QEasingCurve.OutBack)
    return anim


def spring_scale(widget: QWidget, from_scale: float = 0.95, to_scale: float = 1.0, duration: int = 300) -> QPropertyAnimation:
    orig_size = widget.size()
    start_size = QSize(int(orig_size.width() * from_scale), int(orig_size.height() * from_scale))
    anim = QPropertyAnimation(widget, b"size")
    anim.setDuration(duration)
    anim.setStartValue(start_size)
    anim.setEndValue(orig_size)
    anim.setEasingCurve(QEasingCurve.OutBack)
    return anim


def shimmer(widget: QWidget, duration: int = 1500) -> QPropertyAnimation:
    anim = QPropertyAnimation(widget, b"styleSheet")
    anim.setDuration(duration)
    return anim


def press_button(widget: QWidget, duration: int = 100) -> QPropertyAnimation:
    anim = QPropertyAnimation(widget, b"geometry")
    anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve.InQuad)
    return anim


class AnimationGroup(QParallelAnimationGroup):
    pass
