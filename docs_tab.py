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
list (with a result count and the section/page of each hit) while searching;
a content column on the right with back/forward and the rendered page, capped
to a readable line length.

Reading polish (DESIGN typography): the content column sets a comfortable
body size and line height, real vertical rhythm around headings/paragraphs/
lists/tables, a light callout box for each page's "What this page covers"
intro, and images/diagrams that scale down to the current content width at
every window size (so a horizontal scrollbar never appears).

Public surface used by the app shell:
  • DocsTab(parent)
  • .open_page(page_id, anchor=None) — used by Help (?) buttons elsewhere.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QUrl, QEvent
from PyQt6.QtGui import (
    QDesktopServices, QTextCharFormat, QColor, QTextCursor, QTextDocument,
    QTextBlockFormat, QPixmap,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QTextBrowser, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QStackedWidget, QSizePolicy, QStyledItemDelegate, QStyle,
)

import design_tokens as dt
import docs_nav

# Images never display wider than this (keeps the reading column legible even
# on a very wide window), AND never wider than the live content width (so they
# shrink to fit a narrow window rather than forcing a horizontal scrollbar).
MAX_IMG_WIDTH = 720
# Readable line length (DESIGN "~70-80 characters"): cap the content column
# rather than stretching Markdown prose across a whole wide window.
CONTENT_MAX_WIDTH = 760
# Comfortable reading body size + line height (DESIGN typography) — larger than
# the app's dense-UI default so long-form prose reads easily.
BODY_PX = 15
BODY_LINE_HEIGHT = 150          # percent, QTextBlockFormat proportional height

_HIGHLIGHT_BG = QColor("#FDE68A")   # matches the app's existing warning-chip yellow
_CALLOUT_PREFIX = "What this page covers"


class _NoFocusDelegate(QStyledItemDelegate):
    """Strips the native dotted keyboard-focus rectangle from nav/results rows.
    The selected-row background (accent ghost, from the stylesheet) is the
    visible focus indicator we keep instead — matching DESIGN, which has no
    dotted focus outlines."""

    def paint(self, painter, option, index):
        if option.state & QStyle.StateFlag.State_HasFocus:
            option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)


class _ContentBrowser(QTextBrowser):
    """QTextBrowser whose images scale to the current content width.

    Every referenced image (screenshot/diagram) is loaded at its full pixel
    size once, then displayed scaled to `min(MAX_IMG_WIDTH, current content
    width)`. On resize the displayed copies are rebuilt so nothing ever
    exceeds the column — no horizontal scrollbar appears at any window size.

    Deliberately does NOT delegate to the base QTextBrowser.loadResource()
    for images: that base call loads AND caches the resource (full size)
    into the document's resource cache as a side effect, before returning —
    so returning a scaled pixmap built from its result still leaves the
    unscaled one cached under this URL, and the document displays that
    instead (verified empirically). Loading the file directly here avoids
    the unscaled version ever being cached."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # url (as written in the Markdown) -> full-size source pixmap, so a
        # resize can rebuild each displayed copy from the original.
        self._img_src: dict[str, QPixmap] = {}
        # No horizontal scrollbar ever: images scale to fit, and long prose/
        # code wraps at the widget width.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def prepare_for_new_page(self) -> None:
        """Forget the previous page's images before its Markdown is replaced."""
        self._img_src.clear()

    def _target_width(self) -> int:
        doc = self.document()
        avail = self.viewport().width() - int(doc.documentMargin() * 2) - 8
        return max(120, min(MAX_IMG_WIDTH, avail))

    def _display_pixmap(self, pm: QPixmap) -> QPixmap:
        target = self._target_width()
        if pm.width() > target:
            return pm.scaledToWidth(target, Qt.TransformationMode.SmoothTransformation)
        return pm

    def loadResource(self, rtype, url: QUrl):
        if rtype == int(QTextDocument.ResourceType.ImageResource):
            local_url = self.document().baseUrl().resolved(url) if url.isRelative() else url
            path = local_url.toLocalFile()
            if path and os.path.isfile(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    self._img_src[url.toString()] = pm
                    return self._display_pixmap(pm)
        return super().loadResource(rtype, url)

    def _rescale_images(self) -> None:
        if not self._img_src:
            return
        doc = self.document()
        for url_str, pm in self._img_src.items():
            doc.addResource(QTextDocument.ResourceType.ImageResource,
                            QUrl(url_str), self._display_pixmap(pm))
        doc.markContentsDirty(0, doc.characterCount())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._rescale_images()


def _apply_typography(browser: QTextBrowser) -> None:
    """Give the rendered Markdown real vertical rhythm and a readable line
    height. Qt's Markdown importer ignores block margins/line-height from a
    default stylesheet (verified — only inline/char styling and table borders
    get through), so this walks the document's blocks and sets block formats
    directly, the same two-pass way `_style_code_runs` restyles code.

    Collect every block's (position, heading level, is-callout) FIRST, then
    mutate via a cursor — mutating block formats while iterating the live
    block list can re-layout and invalidate the walk."""
    doc = browser.document()
    plan: list[tuple[int, int, bool]] = []   # (position, heading level, is callout)
    block = doc.begin()
    while block.isValid():
        level = block.blockFormat().headingLevel()
        is_callout = block.text().strip().startswith(_CALLOUT_PREFIX)
        plan.append((block.position(), level, is_callout))
        block = block.next()

    cursor = QTextCursor(doc)
    for pos, level, is_callout in plan:
        bf = QTextBlockFormat()
        if is_callout:
            bf.setBackground(QColor(dt.accent_ghost()))
            bf.setLeftMargin(16); bf.setRightMargin(16)
            bf.setTopMargin(14); bf.setBottomMargin(16)
            bf.setLineHeight(BODY_LINE_HEIGHT,
                             QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        elif level == 1:
            bf.setTopMargin(4); bf.setBottomMargin(16)
            bf.setLineHeight(125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        elif level == 2:
            bf.setTopMargin(30); bf.setBottomMargin(10)
            bf.setLineHeight(128, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        elif level >= 3:
            bf.setTopMargin(22); bf.setBottomMargin(8)
            bf.setLineHeight(128, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        else:
            bf.setTopMargin(0); bf.setBottomMargin(12)
            bf.setLineHeight(BODY_LINE_HEIGHT,
                             QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setPosition(pos)
        cursor.mergeBlockFormat(bf)


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
        self._last_hits: list = []
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
        self._search_box.setClearButtonEnabled(True)   # the (×) clear button
        self._search_box.setMinimumHeight(38)
        self._search_box.setMinimumWidth(320)
        self._search_box.setStyleSheet(dt.qss_input())
        self._search_box.textChanged.connect(self._on_search_changed)
        self._search_box.returnPressed.connect(self._open_first_result)
        self._search_box.installEventFilter(self)      # Esc → back to the menu
        header.addWidget(self._search_box)
        root.addLayout(header)

        body = QHBoxLayout(); body.setSpacing(14)

        # left: nav tree <-> (result count + results list) (stacked)
        left_col = QVBoxLayout(); left_col.setSpacing(6)
        self._result_count = QLabel("")
        self._result_count.setWordWrap(True)
        self._result_count.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                         f"font-size: {dt.SIZE_SM}px; font-weight: 600;")
        self._result_count.setVisible(False)
        left_col.addWidget(self._result_count)

        self._left_stack = QStackedWidget()
        self._left_stack.setFixedWidth(240)
        self._nav_tree = QTreeWidget()
        self._nav_tree.setHeaderHidden(True)
        self._nav_tree.setStyleSheet(_nav_qss())
        self._nav_tree.setItemDelegate(_NoFocusDelegate(self._nav_tree))
        # No tree indentation column: on macOS the native style paints a blue
        # selection mark into a child's branch/indent cell, which the item
        # stylesheet can't reach (it leaves a stray blue bracket next to the
        # selected page). With no indent column there's nothing to paint; child
        # pages are instead indented by a small text prefix, and sections stay
        # legible as bold headers.
        self._nav_tree.setIndentation(0)
        self._nav_tree.setRootIsDecorated(False)
        self._nav_tree.itemClicked.connect(self._on_nav_item_clicked)
        self._page_items: dict[str, QTreeWidgetItem] = {}
        for section in self._nav.sections:
            sec_item = QTreeWidgetItem([section.title])
            sec_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = sec_item.font(0); f.setBold(True); sec_item.setFont(0, f)
            self._nav_tree.addTopLevelItem(sec_item)
            for page in section.pages:
                child = QTreeWidgetItem(["    " + page.title])
                child.setData(0, Qt.ItemDataRole.UserRole, page.id)
                sec_item.addChild(child)
            sec_item.setExpanded(True)
        self._left_stack.addWidget(self._nav_tree)

        self._results_list = QListWidget()
        self._results_list.setWordWrap(True)
        self._results_list.setStyleSheet(_nav_qss())
        self._results_list.setItemDelegate(_NoFocusDelegate(self._results_list))
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
            f"font-family: {dt.FONT_UI}; font-size: {BODY_PX}px; color: {dt.text_primary()}; }}")
        self._browser.document().setDefaultStyleSheet(
            f"table {{ border-collapse: collapse; }} "
            f"td, th {{ border: 1px solid {dt.border_strong()}; padding: 6px 10px; }} "
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
        self._browser.prepare_for_new_page()
        self._browser.document().setBaseUrl(QUrl.fromLocalFile(page_dir + os.sep))
        self._browser.setMarkdown(text)
        _apply_typography(self._browser)
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
    def eventFilter(self, obj, event):
        if obj is self._search_box and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._clear_search()
                return True
        return super().eventFilter(obj, event)

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
            self._last_hits = []
            self._result_count.setVisible(False)
            self._left_stack.setCurrentWidget(self._nav_tree)
            self._browser.setExtraSelections([])
            return
        hits = docs_nav.search(self._search_index, self._current_query)
        self._last_hits = hits
        self._results_list.clear()
        for hit in hits:
            label = f"{hit.title}\n{hit.section}  ·  {hit.snippet}"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, hit.page_id)
            self._results_list.addItem(it)
        n = len(hits)
        if n:
            self._result_count.setText(
                f"{n} result{'s' if n != 1 else ''} for “{self._current_query}”")
        else:
            self._result_count.setText(f"No results for “{self._current_query}”")
        self._result_count.setVisible(True)
        self._left_stack.setCurrentWidget(self._results_list)

    def _open_first_result(self) -> None:
        """Enter in the search box opens the first result."""
        if self._last_hits:
            self.open_page(self._last_hits[0].page_id)
            self._highlight(self._current_query)

    def _clear_search(self) -> None:
        """Esc (or the × button) empties the search and returns to the menu."""
        self._search_box.clear()
        self._nav_tree.setFocus()

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
