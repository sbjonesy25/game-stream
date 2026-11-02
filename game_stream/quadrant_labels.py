"""Reusable text-only badges above native VLC video; never copy video frames."""
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QColor
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


def label_rect(video, size, corner):
    x = video.right() - size.width() - 7 if corner.endswith('right') else video.left() + 8
    y = video.bottom() - size.height() - 7 if corner.startswith('bottom') else video.top() + 8
    return QRect(x, y, size.width(), size.height())


def paint_label(painter, rect, number, font):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(font)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 170))
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(QColor('white'))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(number))
    painter.restore()


class NumberBadge(QWidget):
    def __init__(self, wall, number):
        super().__init__(wall, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.BypassWindowManagerHint | Qt.WindowType.WindowDoesNotAcceptFocus |
                         Qt.WindowType.WindowTransparentForInput)
        self.number = number
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
        paint_label(painter, self.rect(), self.number, self.label_font)
