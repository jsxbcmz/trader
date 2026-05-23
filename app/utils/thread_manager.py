from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore


def start_worker(
    parent: QtCore.QObject,
    worker: QtCore.QObject,
    *,
    on_progress: Callable[[dict], None] | None = None,
    on_finished: Callable[[dict], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_cleanup: Callable[[], None] | None = None,
) -> QtCore.QThread:
    """启动后台 Worker 线程并自动连接信号。

    Worker 必须提供以下信号和方法：
    - progressChanged(dict)
    - finished(dict)
    - errorOccurred(str)
    - run() slot

    返回创建的 QThread 实例，调用方可保存引用用于状态检查。
    """
    thread = QtCore.QThread(parent)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)

    if on_progress is not None:
        worker.progressChanged.connect(on_progress)
    if on_finished is not None:
        worker.finished.connect(on_finished)
    if on_error is not None:
        worker.errorOccurred.connect(on_error)

    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    if on_cleanup is not None:
        thread.finished.connect(on_cleanup)

    thread.start()
    return thread
