import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from maskview.ui.main_window import MainWindow

_DARK_INDICATOR_CSS = """
    QToolTip {
        background-color: #2d2d2d;
        color: #ddd;
        border: 1px solid #555;
        padding: 4px 6px;
        font-size: 12px;
    }
    QCheckBox::indicator {
        width: 13px; height: 13px;
        border: 1.5px solid #555; border-radius: 2px; background: #252525;
    }
    QCheckBox::indicator:hover   { border-color: #2ce67f; }
    QCheckBox::indicator:checked { background: #2ce67f; border-color: #1ab864; }
    QCheckBox::indicator:disabled         { border-color: #2e2e2e; background: #181818; }
    QCheckBox::indicator:disabled:checked { border-color: #1a5e32; background: #14472a; }
    QRadioButton::indicator {
        width: 13px; height: 13px;
        border: 1.5px solid #555; border-radius: 7px; background: #252525;
    }
    QRadioButton::indicator:hover   { border-color: #2ce67f; }
    QRadioButton::indicator:checked { background: #2ce67f; border-color: #1ab864; }
"""


def _make_dark_palette(app: QApplication) -> QPalette:
    c = QColor
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window,          c("#1a1a1a"))
    pal.setColor(QPalette.ColorRole.WindowText,      c("#cccccc"))
    pal.setColor(QPalette.ColorRole.Base,            c("#141414"))
    pal.setColor(QPalette.ColorRole.AlternateBase,   c("#202020"))
    pal.setColor(QPalette.ColorRole.Text,            c("#cccccc"))
    pal.setColor(QPalette.ColorRole.BrightText,      c("#ffffff"))
    pal.setColor(QPalette.ColorRole.Button,          c("#2a2a2a"))
    pal.setColor(QPalette.ColorRole.ButtonText,      c("#cccccc"))
    pal.setColor(QPalette.ColorRole.Highlight,       c("#147a3f"))
    pal.setColor(QPalette.ColorRole.HighlightedText, c("#ffffff"))
    pal.setColor(QPalette.ColorRole.Mid,             c("#333333"))
    pal.setColor(QPalette.ColorRole.Dark,            c("#111111"))
    pal.setColor(QPalette.ColorRole.Shadow,          c("#000000"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       c("#555555"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, c("#555555"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, c("#555555"))
    return pal


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_make_dark_palette(app))
    app.setStyleSheet(_DARK_INDICATOR_CSS)

    win = MainWindow()

    screen = app.primaryScreen().availableGeometry()
    w = int(screen.width() * 0.75)
    h = int(screen.height() * 0.75)
    win.resize(w, h)
    win.move(
        screen.x() + (screen.width() - w) // 2,
        screen.y() + (screen.height() - h) // 2,
    )
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
