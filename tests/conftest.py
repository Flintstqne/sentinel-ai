import gc
import os

import pytest

# Must be set before any Qt import so tests never touch the real desktop.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path_factory, monkeypatch):
    """No test may read or write the real user's config, data or log directories.

    Tests that exercise failure paths log deliberate tracebacks; without this they landed in
    the real ~/.local/state/sentinel-ai/sentinel.log next to genuine entries.
    """
    base = tmp_path_factory.mktemp("xdg")
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(base / key.lower()))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setDoubleClickInterval(60)
    yield app
    # Release everything while the QApplication still exists. Qt objects (pixmaps in module
    # caches, widgets and timers held by finished tests) destroyed after it crash the
    # interpreter at exit ("pure virtual method called"), after every test has passed.
    from PySide6.QtCore import QThreadPool

    from sentinel_ai.ui.icons import clear_pixmap_caches

    QThreadPool.globalInstance().waitForDone(5000)
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
    clear_pixmap_caches()
    gc.collect()
    app.processEvents()
