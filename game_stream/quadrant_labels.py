"""Reusable text-only badges above native VLC video; never copy video frames."""
from functools import lru_cache
from math import ceil
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QColor, QImage
from PySide6.QtWidgets import QWidget

CORNERS = ('top-left', 'top-right', 'bottom-left', 'bottom-right')


def label_font(pixels):
    font = QFont('Sans Serif')
    font.setPixelSize(pixels)
    font.setBold(True)
    return font


def label_size(font):
    metrics = QFontMetrics(font)
    return QSize(max(metrics.horizontalAdvance(str(i)) for i in range(1, 5)) + 12, metrics.height() + 8)


def label_rect(video, size, corner, offset_x=8, offset_y=8):
    right = video.right() - size.width() + 1
    bottom = video.bottom() - size.height() + 1
    x = right - offset_x if corner.endswith('right') else video.left() + offset_x
    y = bottom - offset_y if corner.startswith('bottom') else video.top() + offset_y
    x = max(video.left(), min(x, right))
    y = max(video.top(), min(y, bottom))
    return QRect(x, y, size.width(), size.height())


@lru_cache(maxsize=16)
def _label_image(width, height, number, pixels, ratio):
    # Composite the small badge once, so opacity applies to the whole badge.
    # This cache contains text only, never stream frames.
    image = QImage(ceil(width * ratio), ceil(height * ratio), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    rect = QRect(0, 0, width, height)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(label_font(pixels))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 170))
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(QColor('white'))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(number))
    painter.end()
    return image


def paint_label(painter, rect, number, font):
    image = _label_image(rect.width(), rect.height(), number, font.pixelSize(), painter.device().devicePixelRatioF())
    painter.drawImage(rect.topLeft(), image)


class NumberBadge(QWidget):
    def __init__(self, wall, number):
        super().__init__(wall, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.BypassWindowManagerHint | Qt.WindowType.WindowDoesNotAcceptFocus |
                         Qt.WindowType.WindowTransparentForInput)
        self.number = number
        self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet('background: transparent; border: none;')
        self.configure(wall.quadrant_label_size)

    def configure(self, pixels):
        self.label_font = label_font(pixels)
        self.setFixedSize(label_size(self.label_font))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(self.opacity)
        paint_label(painter, self.rect(), self.number, self.label_font)

    def set_opacity(self, opacity):
        if self.opacity != opacity:
            self.opacity = opacity
            self.update()
