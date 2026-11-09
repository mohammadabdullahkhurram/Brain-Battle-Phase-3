"""
docs_tab.py
───────────
The in-app Documentation screen. Renders the same Markdown files under
`docs/` that GitHub renders, using Qt's built-in Markdown support
(`QTextDocument.setMarkdown`) via `QTextBrowser` — no QtWebEngine, no bundled
browser. Navigation/search/link logic lives in `docs_nav.py` (Qt-free,
headlessly testable); this module is the widget wrapping it.

Layout: an always-visible search box at the top; below it, a left nav tree
(collapsible sections, current page highlighted) that swaps for a results
list while searching; a content column on the right with back/forward and
the rendered page, capped to a readable line length.

Public surface used by the app shell:
  • DocsTab(parent)
  • .open_page(page_id, anchor=None) — used by Help (?) buttons elsewhere.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QTextCharFormat, QColor, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QTextBrowser, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QStackedWidget, QSizePolicy,
)

import design_tokens as dt
import docs_nav

# Images display at up to this logical width — keeps the reading column
# legible regardless of a source screenshot's/diagram's native pixel size.
MAX_IMG_WIDTH = 720
# Readable line length (DESIGN "~70-80 characters"): cap the content column
# rather than stretching Markdown prose across a whole wide window.
CONTENT_MAX_WIDTH = 760

_HIGHLIGHT_BG = QColor("#FDE68A")   # matches the app's existing warning-chip yellow


class _ContentBrowser(QTextBrowser):
    """QTextBrowser with images capped to MAX_IMG_WIDTH regardless of their
    source pixel size (PNG screenshots/diagrams are generated bigger, for
    sharpness, than the column they're displayed in).

    Deliberately does NOT delegate to the base QTextBrowser.loadResource()
    for images: that base call loads AND caches the resource (full size)
    into the document's resource cache as a side effect, before returning —
    so returning a scaled pixmap built from its result still leaves the
    unscaled one cached under this URL, and the document displays that
    instead (verified empirically). Loading the file directly here avoids
    the unscaled version ever being cached."""

    def loadResource(self, rtype, url: QUrl):
        if rtype == int(QTextDocument.ResourceType.ImageResource):
            from PyQt6.QtGui import QPixmap
            local_url = self.document().baseUrl().resolved(url) if url.isRelative() else url
            path = local_url.toLocalFile()
            if path and os.path.isfile(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    if pm.width() > MAX_IMG_WIDTH:
                        pm = pm.scaledToWidth(MAX_IMG_WIDTH, Qt.TransformationMode.SmoothTransformation)
                    return pm
        return super().loadResource(rtype, url)


def _style_code_runs(browser: QTextBrowser) -> None:
    """Give inline code / fenced code blocks a light-grey background + the
    DESIGN mono font (DESIGN: "code in a monospace font on a light grey
    background"). Qt's Markdown importer bakes inline QTextCharFormat runs
    rather than CSS classes for these — verified no selector-based stylesheet
    reaches them — so this walks the document's fragments directly and
    restyles any run already set to a monospace font family.

    Two passes, deliberately: collecting (start, end) ranges while iterating
    with a live QTextBlock/fragment iterator, THEN mutating via QTextCursor
    afterwards. Mutating the document DURING that iteration (merging a char
    format on each matching fragment as it's found) segfaults — the format
    merge can re-layout the block and invalidate the iterator mid-walk."""
    doc = browser.document()
    ranges: list[tuple[int, int]] = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                fmt = frag.charFormat()
                # NOTE: QTextCharFormat.fontFamily() (the legacy singular
                # accessor) segfaults in this PyQt6 binding when called here
                # — verified by bisection. Use fontFamilies() (plural) only.
                families = fmt.fontFamilies()
                is_mono = bool(families) and "monospace" in [str(f).lower() for f in families]
                if is_mono:
                    ranges.append((frag.position(), frag.position() + frag.length()))
            it += 1
        block = block.next()

    if not ranges:
        return
    new_fmt = QTextCharFormat()
    new_fmt.setFontFamilies([dt.FONT_MONO])
    new_fmt.setBackground(QColor(dt.canvas_bg()))
    new_fmt.setFontPointSize(max(8, dt.SIZE_SM - 1))
    cursor = QTextCursor(doc)
    for start, end in ranges:
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(new_fmt)


def _nav_qss() -> str:
    return f"""
        QTreeWidget {{
            background: {dt.card_bg()}; border: 1px solid {dt.border()};
            border-radius: {dt.RADIUS_MD}px; color: {dt.text_primary()};
            font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; outline: none;
            padding: 4px;
        }}
        QTreeWidget::item {{ padding: 6px 4px; border-radius: {dt.RADIUS_SM}px; }}
        QTreeWidget::item:selected {{ background: {dt.accent_ghost()}; color: {dt.accent()}; }}
        QListWidget {{
            background: {dt.card_bg()}; border: 1px solid {dt.border()};
            border-radius: {dt.RADIUS_MD}px; color: {dt.text_primary()};
            font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; outline: none;
            padding: 4px;
        }}
        QListWidget::item {{ padding: 8px 6px; border-radius: {dt.RADIUS_SM}px; }}
        QListWidget::item:selected {{ background: {dt.accent_ghost()}; color: {dt.accent()}; }}
    """


class DocsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"DocsTab {{ background: {dt.canvas_bg()}; }}")
        self._nav = docs_nav.load_nav()
        self._search_index = docs_nav.build_search_index(self._nav)
        self._history: list[tuple[str, str | None]] = []
        self._history_pos = -1
        self._current_query = ""
        self._build()
        first = self._nav.all_pages()
        if first:
            self.open_page(first[0].id)

    # ── construction ──
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(24, 20, 24, 20); root.setSpacing(12)

        header = QHBoxLayout(); header.setSpacing(12)
        title = QLabel("Documentation")
        title.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                            f"font-size: 22px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch()
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search documentation…")
        self._search_box.setMinimumHeight(38)
        self._search_box.setMinimumWidth(320)
        self._search_box.setStyleSheet(dt.qss_input())
        self._search_box.textChanged.connect(self._on_search_changed)
        header.addWidget(self._search_box)
        root.addLayout(header)

        body = QHBoxLayout(); body.setSpacing(14)

        # left: nav tree <-> search results (stacked)
        left_col = QVBoxLayout(); left_col.setSpacing(0)
        self._left_stack = QStackedWidget()
        self._left_stack.setFixedWidth(240)
        self._nav_tree = QTreeWidget()
        self._nav_tree.setHeaderHidden(True)
        self._nav_tree.setStyleSheet(_nav_qss())
        self._nav_tree.itemClicked.connect(self._on_nav_item_clicked)
        self._page_items: dict[str, QTreeWidgetItem] = {}
        for section in self._nav.sections:
            sec_item = QTreeWidgetItem([section.title])
            sec_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = sec_item.font(0); f.setBold(True); sec_item.setFont(0, f)
            self._nav_tree.addTopLevelItem(sec_item)
            for page in section.pages:
                child = QTreeWidgetItem([page.title])
                child.setData(0, Qt.ItemDataRole.UserRole, page.id)
                sec_item.addChild(child)
            sec_item.setExpanded(True)
        self._left_stack.addWidget(self._nav_tree)

        self._results_list = QListWidget()
        self._results_list.setWordWrap(True)
        self._results_list.setStyleSheet(_nav_qss())
        self._results_list.itemClicked.connect(self._on_result_clicked)
        self._left_stack.addWidget(self._results_list)
        left_col.addWidget(self._left_stack)
        body.addLayout(left_col)

        # right: back/forward + content
        right_col = QVBoxLayout(); right_col.setSpacing(8)
        nav_row = QHBoxLayout(); nav_row.setSpacing(6)
        self._back_btn = QPushButton("←")
        self._forward_btn = QPushButton("→")
        for b in (self._back_btn, self._forward_btn):
            b.setFixedSize(34, 34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(dt.qss_secondary_button() + " QPushButton { padding: 0; }")
        self._back_btn.clicked.connect(self._go_back)
        self._forward_btn.clicked.connect(self._go_forward)
        nav_row.addWidget(self._back_btn); nav_row.addWidget(self._forward_btn)
        self._breadcrumb = QLabel("")
        self._breadcrumb.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                       f"font-size: {dt.SIZE_SM}px;")
        nav_row.addWidget(self._breadcrumb); nav_row.addStretch()
        right_col.addLayout(nav_row)

        content_row = QHBoxLayout()
        self._browser = _ContentBrowser()
        self._browser.setOpenLinks(False)
        self._browser.setOpenExternalLinks(False)
        self._browser.anchorClicked.connect(self._on_link_clicked)
        self._browser.setMaximumWidth(CONTENT_MAX_WIDTH)
        self._browser.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background: {dt.card_bg()}; border: 1px solid {dt.border()}; "
            f"border-radius: {dt.RADIUS_LG}px; padding: 18px 24px; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px; color: {dt.text_primary()}; }}")
        self._browser.document().setDefaultStyleSheet(
            f"table {{ border-collapse: collapse; }} "
            f"td, th {{ border: 1px solid {dt.border_strong()}; padding: 4px 8px; }} "
            f"a {{ color: {dt.accent()}; }}")
        # Stretch factor 1 with no competing item: the browser claims all
        # available width up to its maximumWidth cap (so it still shrinks
        # gracefully on a narrow window), and any surplus beyond the cap is
        # simply left blank rather than stretching prose across the window.
        content_row.addWidget(self._browser, 1)
        right_col.addLayout(content_row, 1)
        body.addLayout(right_col, 1)

        root.addLayout(body, 1)

    # ── navigation ──
    def open_page(self, page_id: str, anchor: str | None = None, record_history: bool = True) -> None:
        page = self._nav.page_by_id(page_id)
        if page is None:
            return
        text = docs_nav.read_page(page)
        page_dir = os.path.dirname(docs_nav.page_path(page))
        self._browser.document().setBaseUrl(QUrl.fromLocalFile(page_dir + os.sep))
        self._browser.setMarkdown(text)
        _style_code_runs(self._browser)
        self._breadcrumb.setText(f"{page.section} / {page.title}")
        self._highlight_nav_item(page_id)
        if anchor:
            self._scroll_to_anchor(anchor)
        if self._current_query:
            self._highlight(self._current_query)
        if record_history:
            self._push_history(page_id, anchor)
        self._update_nav_buttons()

    def _push_history(self, page_id: str, anchor: str | None) -> None:
        self._history = self._history[:self._history_pos + 1]
        self._history.append((page_id, anchor))
        self._history_pos = len(self._history) - 1

    def _go_back(self) -> None:
        if self._history_pos > 0:
            self._history_pos -= 1
            page_id, anchor = self._history[self._history_pos]
            self.open_page(page_id, anchor, record_history=False)
            self._update_nav_buttons()

    def _go_forward(self) -> None:
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            page_id, anchor = self._history[self._history_pos]
            self.open_page(page_id, anchor, record_history=False)
            self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        self._back_btn.setEnabled(self._history_pos > 0)
        self._forward_btn.setEnabled(self._history_pos < len(self._history) - 1)

    def _highlight_nav_item(self, page_id: str) -> None:
        for i in range(self._nav_tree.topLevelItemCount()):
            sec = self._nav_tree.topLevelItem(i)
            for j in range(sec.childCount()):
                child = sec.child(j)
                is_current = child.data(0, Qt.ItemDataRole.UserRole) == page_id
                child.setSelected(is_current)
                if is_current:
                    self._nav_tree.scrollToItem(child)

    def _scroll_to_anchor(self, slug: str) -> None:
        doc = self._browser.document()
        block = doc.begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0:
                if docs_nav.slugify(block.text()) == slug:
                    cursor = QTextCursor(block)
                    self._browser.setTextCursor(cursor)
                    self._browser.ensureCursorVisible()
                    return
            block = block.next()

    # ── events ──
    def _on_nav_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        page_id = item.data(0, Qt.ItemDataRole.UserRole)
        if page_id:
            self.open_page(page_id)

    def _on_link_clicked(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        path_part = url.path()
        anchor = url.fragment()
        if not path_part:
            if anchor:
                self._scroll_to_anchor(anchor)
            return
        current = self._nav.page_by_id(self._current_page_id())
        if current is None:
            return
        current_dir = os.path.dirname(docs_nav.page_path(current))
        resolved = os.path.normpath(os.path.join(current_dir, path_part))
        rel = os.path.relpath(resolved, docs_nav.DOCS_DIR)
        target = self._nav.page_by_file(rel)
        if target is not None:
            self.open_page(target.id, anchor or None)

    def _current_page_id(self) -> str | None:
        if 0 <= self._history_pos < len(self._history):
            return self._history[self._history_pos][0]
        return None

    # ── search ──
    def _on_search_changed(self, text: str) -> None:
        self._current_query = text.strip()
        if not self._current_query:
            self._left_stack.setCurrentWidget(self._nav_tree)
            self._browser.setExtraSelections([])
            return
        hits = docs_nav.search(self._search_index, self._current_query)
        self._results_list.clear()
        for hit in hits:
            it = QListWidgetItem(f"{hit.title}\n{hit.snippet}")
            it.setData(Qt.ItemDataRole.UserRole, hit.page_id)
            self._results_list.addItem(it)
        self._left_stack.setCurrentWidget(self._results_list)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        page_id = item.data(Qt.ItemDataRole.UserRole)
        if page_id:
            self.open_page(page_id)
            self._highlight(self._current_query)

    def _highlight(self, query: str) -> None:
        if not query:
            self._browser.setExtraSelections([])
            return
        from PyQt6.QtWidgets import QTextEdit
        selections = []
        doc = self._browser.document()
        cursor = QTextCursor(doc)
        fmt = QTextCharFormat()
        fmt.setBackground(_HIGHLIGHT_BG)
        while True:
            cursor = doc.find(query, cursor)
            if cursor.isNull():
                break
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            selections.append(sel)
        self._browser.setExtraSelections(selections)
        if selections:
            self._browser.setTextCursor(selections[0].cursor)
            self._browser.ensureCursorVisible()


__all__ = ["DocsTab"]
