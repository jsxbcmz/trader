from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtWidgets

from core.data.database import init_databases
from .main_window import MainWindow


def main():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parents[1]

    init_databases(root)

    app = QtWidgets.QApplication(sys.argv)
    app.setOrganizationName("StockViewer")
    app.setApplicationName("StockViewer")
    w = MainWindow(root)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
