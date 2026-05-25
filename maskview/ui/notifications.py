from PyQt6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


class _NotifWidget(QFrame):
    """Single dismissable notification card."""

    dismissed = pyqtSignal()

    _COLORS = {
        "warning": ("#3d2c00", "#ffb347", "#5a4000"),
        "error":   ("#3d0e0e", "#ff6b6b", "#5a1a1a"),
        "info":    ("#1a2a3a", "#5fa0d4", "#253a50"),
    }

    def __init__(self, title: str, message: str, level: str = "warning", parent=None):
        super().__init__(parent)
        bg, fg, border = self._COLORS.get(level, self._COLORS["warning"])
        self.setFixedWidth(310)
        self.setObjectName("notif")
        self.setStyleSheet(
            f"#notif {{ background: {bg}; border: 1px solid {border}; border-radius: 5px; }}"
            "QLabel { background: transparent; border: none; }"
            "QPushButton { background: transparent; border: none; }"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {fg}; font-size: 12px; font-weight: bold;")
        header.addWidget(title_lbl, stretch=1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setStyleSheet(
            "QPushButton { color: #888; font-size: 10px; }"
            "QPushButton:hover { color: #fff; }"
        )
        close_btn.clicked.connect(self.dismissed)
        header.addWidget(close_btn)
        layout.addLayout(header)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("color: #ccc; font-size: 11px;")
        layout.addWidget(msg_lbl)

        self.adjustSize()


class NotifManager(QObject):
    """Stacked slide-in notifications anchored to the top-right of a parent widget."""

    _MARGIN  = 10
    _SPACING = 8
    _TOP     = 10

    def __init__(self, parent_widget: QWidget):
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._notifs: list[_NotifWidget] = []

    def show(self, title: str, message: str, level: str = "warning"):
        notif = _NotifWidget(title, message, level, self._parent)
        notif.dismissed.connect(lambda n=notif: self._dismiss(n))

        y        = self._next_y()
        x_shown  = self._parent.width() - notif.width() - self._MARGIN
        x_hidden = self._parent.width()

        notif.move(x_hidden, y)
        notif.show()
        notif.raise_()

        anim = QPropertyAnimation(notif, b"pos")
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(QPoint(x_hidden, y))
        anim.setEndValue(QPoint(x_shown, y))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

        self._notifs.append(notif)

    def reposition(self):
        """Call when the parent widget resizes."""
        for notif in self._notifs:
            x = self._parent.width() - notif.width() - self._MARGIN
            notif.move(x, notif.y())

    def _dismiss(self, notif: _NotifWidget):
        if notif in self._notifs:
            self._notifs.remove(notif)
        notif.setParent(None)
        self._restack()

    def _next_y(self) -> int:
        if not self._notifs:
            return self._TOP
        last = self._notifs[-1]
        return last.y() + last.height() + self._SPACING

    def _restack(self):
        y = self._TOP
        for notif in self._notifs:
            notif.move(notif.x(), y)
            y += notif.height() + self._SPACING
