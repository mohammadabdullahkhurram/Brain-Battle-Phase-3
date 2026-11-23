"""
export_dialog.py — Export for analysis (the app's dialog)
──────────────────────────────────────────────────────────
DESIGN "Export": what is always included, the two video choices, where to
save; runs `export_module.SessionExportWorker` with a progress bar and ends
with "Show in Finder / Explorer". The export itself (what goes in the zip) is
`data/export_module.py`; this is only its screen.
"""
from __future__ import annotations

import os

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QProgressBar, QFileDialog

from brain_battle.ui.widgets import AppDialog
from brain_battle.data.export_module import SessionExportWorker, default_export_name

class ExportOptionsDialog(AppDialog):
    """Export for analysis (DESIGN "Export"): what is always included, two
    video choices (the 4 camera videos; also one combined video), where to
    save. Runs the export with a progress bar and ends with "Show in Finder /
    Explorer"."""

    ALWAYS = (
        "recording.xdf: everything on one clock, for EEGLAB, MNE or Python",
        "The same data as separate CSV files: both brain recordings, head movement, "
        "automatic markers, moments and notes, everyone\u2019s labels, and every "
        "camera\u2019s frame times",
        "A timing report (timing_quality.csv): frame rates, delays, jitter, dropped "
        "frames and packets, and the buzzer-test corrections for every device",
        "How much the labellers agree, the session details and the history",
    )

    def __init__(self, session_root, labels=None, phase_markers=None, parent=None):
        super().__init__(parent, width=600)
        import brain_battle.ui.design_tokens as dt
        import brain_battle.data.fighters as fighters
        import brain_battle.data.session_storage as session_storage
        from brain_battle.support.platform_utils import show_in_file_manager_label
        from PyQt6.QtWidgets import QCheckBox
        self._root = session_root
        self._labels = labels
        self._phases = phase_markers
        self._worker = None
        self.setWindowTitle("Export for analysis")
        self.setModal(True)
        self.set_cancel_result(0)

        manifest = session_storage._read_manifest(session_root) or {}
        self._dir = os.path.dirname(os.path.abspath(session_root))
        self._dest = os.path.join(self._dir, default_export_name(session_root))

        v = self.body
        title = QLabel("Export for analysis")
        title.setStyleSheet(dt.css_text(dt.SIZE_XL, 700))
        v.addWidget(title)
        self._name_lbl = QLabel(os.path.basename(self._dest))
        self._name_lbl.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary(), f"font-family: {dt.FONT_MONO};"))
        v.addWidget(self._name_lbl)

        head = QLabel("Always included")
        head.setStyleSheet(dt.css_text(dt.SIZE_SM, 700))
        v.addWidget(head)
        for text in self.ALWAYS:
            row = QLabel(f"<span style='color:{dt.success_color()};font-weight:700'>\u2713</span> "
                         f"{text}")
            row.setWordWrap(True)
            row.setStyleSheet(dt.css_text(dt.SIZE_SM, 400))
            v.addWidget(row)

        vh = QLabel("Videos")
        vh.setStyleSheet(dt.css_text(dt.SIZE_SM, 700))
        v.addWidget(vh)
        self._videos_cb = QCheckBox("The 4 camera videos")
        self._videos_cb.setChecked(True)
        self._videos_cb.setStyleSheet(dt.qss_checkbox())
        v.addWidget(self._videos_cb)
        self._merge_cb = QCheckBox("Also one combined video with all 4 cameras")
        self._merge_cb.setChecked(False)
        self._merge_cb.setStyleSheet(dt.qss_checkbox())
        v.addWidget(self._merge_cb)

        a, b = fighters.display(manifest, "A"), fighters.display(manifest, "B")
        legacy = fighters.info(manifest, "A")["name"] or fighters.info(manifest, "B")["name"]
        note = QLabel(f"Fighters appear only as their codes ({a}, {b}) and colours. No names are "
                      f"stored, so there is nothing to hide." if not legacy else
                      "This recording was made before fighter codes; it keeps the names it "
                      "was recorded with.")
        note.setWordWrap(True)
        note.setObjectName("exportCodesNote")
        note.setStyleSheet(f"#exportCodesNote {{ background: {dt.success_bg()}; "
                           f"color: {dt.success_color()}; border-radius: 12px; "
                           f"padding: 10px 12px; }}" + "")
        v.addWidget(note)

        dest_row = QHBoxLayout(); dest_row.setSpacing(10)
        save_lbl = QLabel("Save to")
        save_lbl.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.text_secondary()))
        self._dest_lbl = QLabel(self._dest)
        self._dest_lbl.setWordWrap(True)
        self._dest_lbl.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        change = QPushButton("Change\u2026"); change.setMinimumHeight(36)
        change.setStyleSheet(dt.qss_secondary_button(compact=True)); change.clicked.connect(self._pick_dest)
        dest_row.addWidget(save_lbl); dest_row.addWidget(self._dest_lbl, 1); dest_row.addWidget(change)
        v.addLayout(dest_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        v.addWidget(self._status)
        self._bar = QProgressBar(); self._bar.setRange(0, 100); self._bar.setValue(0)
        self._bar.setFixedHeight(6); self._bar.setTextVisible(False); self._bar.setVisible(False)
        v.addWidget(self._bar)

        btns = QHBoxLayout(); btns.setSpacing(10)
        self._reveal = QPushButton(show_in_file_manager_label()); self._reveal.setMinimumHeight(44)
        self._reveal.setStyleSheet(dt.qss_secondary_button()); self._reveal.setVisible(False)
        self._reveal.clicked.connect(self._reveal_export)
        btns.addWidget(self._reveal); btns.addStretch()
        self._cancel = QPushButton("Cancel"); self._cancel.setMinimumHeight(44)
        self._cancel.setStyleSheet(dt.qss_secondary_button()); self._cancel.clicked.connect(self.reject)
        self._go = QPushButton("Export"); self._go.setMinimumHeight(44)
        self._go.setStyleSheet(dt.qss_primary_button()); self._go.clicked.connect(self._start)
        btns.addWidget(self._cancel); btns.addWidget(self._go)
        v.addLayout(btns)

    def _pick_dest(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save export", self._dest, "Zip (*.zip)")
        if path:
            self._dest = path if path.endswith(".zip") else path + ".zip"
            self._dest_lbl.setText(self._dest)
            self._name_lbl.setText(os.path.basename(self._dest))

    def _start(self):
        self._go.setEnabled(False)
        self._merge_cb.setEnabled(False); self._videos_cb.setEnabled(False)
        self._bar.setVisible(True)
        self._worker = SessionExportWorker(self._root, self._labels, self._phases,
                                           self._dest, merge_video=self._merge_cb.isChecked(),
                                           include_videos=self._videos_cb.isChecked())
        self._worker.progress.connect(lambda pct, msg: (self._bar.setValue(pct), self._status.setText(msg)))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, path):
        import brain_battle.ui.design_tokens as dt
        self._status.setText(f"✓ Exported to {path}")
        self._status.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.success_color()))
        self._bar.setVisible(False)
        self._go.setVisible(False); self._reveal.setVisible(True)
        self._cancel.setText("Done")

    def _on_error(self, msg):
        import brain_battle.ui.design_tokens as dt
        self._status.setText(f"The export did not finish. {msg[:400]}")
        self._status.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.error_color()))
        self._bar.setVisible(False)
        self._go.setEnabled(True)
        self._merge_cb.setEnabled(True); self._videos_cb.setEnabled(True)

    def _reveal_export(self):
        from brain_battle.support.platform_utils import reveal_in_file_manager
        reveal_in_file_manager(self._dest)


__all__ = ["ExportOptionsDialog"]
