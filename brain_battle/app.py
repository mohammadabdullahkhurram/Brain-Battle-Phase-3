import os
os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
os.environ["QT_FFMPEG_DISABLE_HW_DECODING"] = "1"

# Configure logging BEFORE the heavy imports so import-time messages
# (e.g. export_module's encoder detection) reach the console handler.
import logging
from brain_battle.support.log_setup import init_logging
init_logging()
log = logging.getLogger(__name__)

from pathlib import Path
import sys
import datetime as _dt


from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QSizePolicy, QFileDialog,
    QListWidget, QListWidgetItem, QScrollArea,
    QSplitter, QSlider, QComboBox, QLineEdit,
    QGroupBox, QProgressBar, QStatusBar, QToolBar,
    QSpacerItem, QMessageBox, QStackedWidget
)
from PyQt6.QtCore import Qt, QTimer, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon, QPixmap, QPainter

from brain_battle.ui.record_flow import RecordFlow
from brain_battle.version import __version__
import brain_battle.data.session_history as session_history


def _supplementary_qss():
    """Theme-aware QSS supplementing dt.qss_app() — covers widgets that
    aren't specifically styled by the main stylesheet factories (sliders,
    list widgets, progress bars, combo boxes, group boxes, etc.)."""
    import brain_battle.ui.design_tokens as dt
    return f"""
        QSlider::groove:horizontal {{ height: 3px; background: {dt.border_strong()}; border-radius: 1px; }}
        QSlider::sub-page:horizontal {{ background: {dt.accent()}; height: 3px; border-radius: 1px; }}
        QSlider::handle:horizontal {{ background: {dt.accent()}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
        QListWidget {{ background-color: {dt.card_bg()}; border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_MD}px; color: {dt.text_primary()}; font-family: {dt.FONT_UI}; font-size: 12px; outline: none; padding: 4px; }}
        QListWidget::item {{ padding: 8px 12px; border-radius: {dt.RADIUS_SM}px; }}
        QListWidget::item:selected {{ background-color: {dt.accent_ghost()}; color: {dt.accent()}; }}
        QProgressBar {{ background-color: {dt.card_bg()}; border: 1px solid {dt.border()}; height: 4px; text-align: center; color: transparent; border-radius: 2px; }}
        QProgressBar::chunk {{ background-color: {dt.accent()}; border-radius: 2px; }}
    """ + dt.qss_combo()

MIN_WINDOW = (1180, 720)   # smallest usable window (below the 1280×800 DESIGN
#                            target, for the owner's smaller laptop); the Review
#                            page pins its transport and scrolls vertically.


class BrainBattleApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Brain Battle  v{__version__}")
        self.setMinimumSize(*MIN_WINDOW)
        self._restore_or_fill_window()
        self._build()

    def _restore_or_fill_window(self):
        """First launch fills the available screen; after that we remember the
        last window size/position."""
        from PyQt6.QtCore import QSettings
        s = QSettings("Constructor University", "Brain Battle")
        geo = s.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        else:
            self.resize(1440, 900)

    def closeEvent(self, event):
        # Confirm before quitting mid-recording (DESIGN §4 safety).
        try:
            if getattr(self, "record_flow", None) is not None and self.record_flow.is_recording():
                reply = QMessageBox.question(
                    self, "Quit while recording?",
                    "A recording is in progress. Quit anyway? Everything recorded so "
                    "far is kept, but the rest of the session will not happen.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    event.ignore(); return
        except Exception:
            pass
        try:
            from PyQt6.QtCore import QSettings
            QSettings("Constructor University", "Brain Battle").setValue(
                "window/geometry", self.saveGeometry())
        except Exception:
            pass
        super().closeEvent(event)

    def _build(self):
        from brain_battle.ui.sidebar_shell import SidebarShell
        import brain_battle.data.app_settings as app_settings

        self._restyle_app()   # apply the light global stylesheet once

        central = QWidget(); central.setObjectName("AppRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        capture_dir = app_settings.load().capture_dir_resolved()
        self._shell = SidebarShell(capture_dir=capture_dir)

        # The three DESIGN screens: Sessions (home) → New recording (guided flow)
        # → Review & Label (one merged PyAV-driven screen), plus Settings.
        self.record_flow = RecordFlow()
        self.record_flow.session_saved.connect(self._on_session_saved)
        self.record_flow.exit_flow.connect(self._go_to_sessions)
        from brain_battle.ui.review_label_screen import ReviewLabelScreen
        self._review_page = ReviewLabelScreen()
        self._review_page.back_to_sessions.connect(self._go_to_sessions)
        from brain_battle.ui.sessions_screen import SessionsScreen
        self._sessions_page = SessionsScreen(capture_dir)
        self._sessions_page.new_recording.connect(self._go_to_new_recording)
        self._sessions_page.open_session.connect(self._open_session_in_review)
        from brain_battle.ui.settings_screen import SettingsScreen
        self._settings_page = SettingsScreen()
        self._settings_page.capture_dir_changed.connect(self._on_capture_dir_changed)
        self._settings_page.labeller_changed.connect(self._on_labeller_changed)
        self._settings_page.names_hidden_changed.connect(self._apply_names_hidden)
        self._settings_page.help_requested.connect(lambda: self.open_help("settings"))
        from brain_battle.ui.docs_screen import DocsScreen
        self._docs_page = DocsScreen()

        self._sessions_page.help_requested.connect(lambda: self.open_help("sessions"))
        self.record_flow.help_requested.connect(self.open_help)
        self._review_page.help_requested.connect(lambda: self.open_help("review-and-label"))

        self._sessions_page.export_session.connect(self._export_session)
        self.IDX_SESSIONS = self._shell.add_page("sessions", "Sessions", "home", self._sessions_page)
        # New recording — the sidebar's one primary button — opens the
        # full-screen guided flow (no sidebar).
        self.IDX_RECORD   = self._shell.add_page("record", "New recording", "record",
                                                 self.record_flow, hide_sidebar=True, primary=True)
        self.IDX_REVIEW   = self._shell.add_page("review", "Review & Label", "layers",
                                                 self._review_page, collapse_on_active=True)
        # Documentation sits just above Settings in the bottom nav group.
        self.IDX_DOCS     = self._shell.add_page("docs", "Documentation", "book",
                                                 self._docs_page, bottom=True)
        self.IDX_SETTINGS = self._shell.add_page("settings", "Settings", "settings",
                                                 self._settings_page, bottom=True)

        # Compat shim for any code that walked up to a QTabWidget.
        class _ShellTabShim:
            def __init__(self, shell): self._shell = shell
            def setCurrentIndex(self, idx: int): self._shell.set_current_index(idx)
            def currentIndex(self): return self._shell.current_index()
        self.tabs = _ShellTabShim(self._shell)

        # Refresh the Sessions library whenever the user navigates to it, and
        # (re)enter the guided flow at step 1 when New recording is opened.
        self._shell.page_changed.connect(self._on_page_changed)

        # Who is labelling + the quick "Hide names" switch live in the sidebar.
        settings = app_settings.load()
        self._shell.set_labeller(settings.labeller_name)
        self._shell.identity_clicked.connect(self._edit_identity)
        self._shell.names_hidden_changed.connect(self._on_hide_names_toggled)
        self._review_page.labeller_changed.connect(self._on_labeller_changed)
        self._apply_names_hidden(settings.hide_names_on_screen)

        root.addWidget(self._shell, 1)
        self._shell.set_current_index(self.IDX_SESSIONS)   # app opens on Sessions

        # Populate the Sessions library once the window is up. Any recording
        # that was interrupted (crashed mid-session) last time is surfaced by
        # the interrupted banner at the top of that screen — there is no
        # separate recovery popup anymore.
        QTimer.singleShot(0, self._sessions_page.refresh)

    def _on_page_changed(self, idx):
        self._shell.refresh_folder_card()
        if idx == self.IDX_SESSIONS:
            self._sessions_page.refresh()
        elif idx == self.IDX_RECORD:
            # Entering New recording (re)starts the guided flow at step 1.
            self.record_flow.start()
        elif idx == self.IDX_SETTINGS:
            self._settings_page.refresh()

    # ── who is labelling / hiding names ──
    def _edit_identity(self):
        """The sidebar's "Your name" row: open Settings at that field."""
        self._shell.set_current_index(self.IDX_SETTINGS)
        self._settings_page.focus_labeller_name()

    def _on_labeller_changed(self, name):
        """"Your name" was set (in Settings, or the first time Review & Label
        asked) — tell every place that shows or uses it."""
        self._shell.set_labeller(name)
        self._review_page.set_labeller(name)
        self._sessions_page.set_labeller(name)
        self._settings_page.refresh()

    def _on_hide_names_toggled(self, hidden):
        import brain_battle.data.app_settings as app_settings
        s = app_settings.load(); s.hide_names_on_screen = bool(hidden); s.save()
        self._apply_names_hidden(hidden)
        self._settings_page.refresh()

    def _apply_names_hidden(self, hidden):
        self._shell.set_names_hidden(hidden)
        self._sessions_page.set_names_hidden(hidden)
        self._review_page.set_names_hidden(hidden)

    def _export_session(self, session_root):
        """Sessions → ⋯ → Export for analysis: export without opening it."""
        from brain_battle.data.export_module import ExportOptionsDialog
        ExportOptionsDialog(session_root, parent=self).exec()
        self._sessions_page.refresh()

    def _go_to_new_recording(self):
        self._shell.set_current_index(self.IDX_RECORD)

    def _go_to_sessions(self):
        self._shell.set_current_index(self.IDX_SESSIONS)

    # ── Help (?) buttons — Sessions / each record-flow step / Review & Label /
    # Settings all open the Documentation tab at that screen's user-guide page.
    _HELP_PAGES = {
        "sessions": "sessions",
        "review-and-label": "review-and-label",
        "settings": "settings",
        # record_flow emits an ANCHOR, not a page id, one per guided-flow step —
        # all four live on the single "new-recording" page (DESIGN: one page,
        # §2-§5). Anything else passed through is treated as a page id.
        "step-1-get-ready": ("new-recording", "step-1-get-ready"),
        "step-2-recording": ("new-recording", "step-2-recording"),
        "step-3-saved": ("new-recording", "step-3-saved"),
    }

    def open_help(self, key: str) -> None:
        target = self._HELP_PAGES.get(key, key)
        if isinstance(target, tuple):
            page_id, anchor = target
        else:
            page_id, anchor = target, None
        self._docs_page.open_page(page_id, anchor)
        self._shell.set_current_index(self.IDX_DOCS)

    def _on_capture_dir_changed(self, path):
        """Settings' [Change folder…] — repoint the folder card + Sessions
        library at the new recordings folder (existing recordings stay put;
        the guided flow reloads app_settings on its own next start())."""
        self._shell.set_capture_dir(path)
        self._sessions_page.set_capture_dir(path)

    def keyPressEvent(self, event):
        # Ctrl/⌘ + Shift + H: hide / show boxer names on screen, from anywhere.
        mods = event.modifiers()
        if event.key() == Qt.Key.Key_H and (mods & Qt.KeyboardModifier.ShiftModifier) and \
                (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)):
            self._shell.set_names_hidden(not self._shell.names_hidden(), emit=True)
            return
        super().keyPressEvent(event)

    def _open_session_in_review(self, session_root):
        self._review_page.load_session_folder(session_root)
        self._shell.set_current_index(self.IDX_REVIEW)

    def _on_session_saved(self, session_root):
        self._review_page.load_session_folder(session_root)
        self._shell.set_current_index(self.IDX_REVIEW)
        # Keep the library current for the next time Sessions is shown.
        self._sessions_page.refresh()

    def _restyle_app(self):
        """Apply the light global stylesheet once."""
        import brain_battle.ui.design_tokens as dt
        self.setStyleSheet(dt.qss_app() + _supplementary_qss())


def main():
    """Launch the Brain Battle desktop app. Entry point for
    ``python3 -m brain_battle``, the ``brain-battle`` console script, and the
    ``brain_battle_main.py`` launcher shim."""
    import traceback as _tb

    # Light mode only — no theme preference to load (DESIGN.md).
    app = QApplication(sys.argv); app.setApplicationName("Brain Battle")
    from brain_battle.ui.fonts import register_fonts
    register_fonts()   # bundled Inter; falls back to the system font if absent
    def _excepthook(exc_type, exc_value, exc_tb):
        log.critical("UNHANDLED EXCEPTION", exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _excepthook
    try:
        window = BrainBattleApp(); window.show()
    except Exception:
        # No input() here: a windowed launch on Windows (pythonw / a shortcut)
        # has no console to read from, and would hang or crash a second time.
        log.critical("could not start the app", exc_info=True)
        _tb.print_exc(); sys.exit(1)
    def _cleanup():
        # Stop EEG streams + camera threads + loader threads before Qt
        # tears down widgets — prevents "QThread destroyed while still
        # running" crash on exit.
        try: window.record_flow.shutdown()
        except Exception: pass
        try: window._review_page.shutdown()
        except Exception: pass
        try: window._sessions_page.shutdown()
        except Exception: pass
    app.aboutToQuit.connect(_cleanup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()