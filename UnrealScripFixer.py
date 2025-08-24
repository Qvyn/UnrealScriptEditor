import os
import re
import sys
import pathlib
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable, Dict

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QFont, QColor, QTextCharFormat, QSyntaxHighlighter
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QPlainTextEdit, QLabel, QMessageBox,
    QSplitter, QCheckBox, QStyleFactory, QTabWidget, QTextBrowser, QComboBox
)
from PyQt5.QtGui import QDesktopServices

# Try to use an embedded web view for docs (best UX). Falls back gracefully.
try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView  # type: ignore
    WEBENGINE_AVAILABLE = True
except Exception:
    WEBENGINE_AVAILABLE = False


# =============================================================================
# UnrealScript Rules Pack (doc-driven, safe)
# =============================================================================
RULES: Dict = {
  "keywords": {
    "declarations": ["class","extends","struct","enum","var","const","native","replication","defaultproperties","cpptext","state","event","function","operator","simulated","static","final","abstract","private","protected","public","repnotify","config","transient","within","implements"],
    "types_core": ["bool","byte","int","float","string","name","vector","rotator"],
    "flow": ["if","else","while","for","switch","case","break","return","goto","continue"],
    "net_fn_modifiers": ["client","server","reliable","unreliable","demorecording"],
    "class_modifiers": ["abstract","native","nativereplication","placeable","transient","config","perobjectconfig","hidecategories","showcategories","dependson","within","implements"],
    "function_specifiers": ["static","simulated","final","private","protected","public","native","iterator","latent","event","exec","operator","preoperator","postoperator"],
    "param_specifiers": ["out","optional","coerce"]
  },
  "property_specifiers": ["config","globalconfig","const","editconst","editinline","export","native","transient","repnotify","localized"],
  "blocks": {
    "cpptext": {"must_open_brace_next_line": True, "close_before_next_top_level": True},
    "defaultproperties": {"open": "{", "close": "}", "allow_only_assign_and_subobjects": True},
    "structdefaultproperties": {"open": "{", "close": "}", "context": "inside struct"},
    "struct": {"open": "{", "close": "}", "must_close_before_next_top_level": True},
    "enum":   {"open": "{", "close": "}", "must_close_before_next_top_level": True},
    "replication": {"open": "{", "close": "}"}
  },
  "line_rules": {
    "assign_requires_semicolon": True,
    "var_decl_requires_semicolon": True,
    "control_paren_balance": {"targets": ["if","while","for"], "single_line_only": True}
  },
  "regex": {
    "top_level_tokens": r"(?m)^\s*(class|function|event|state|defaultproperties|var|struct|enum|cpptext|replication)\b",
    "control_line": r"^\s*(if|while|for)\s*\(",
    "identifier": r"[A-Za-z_]\w*"
  },
  "docs": {
    "home": "https://docs.unrealengine.com/udk/Three/UnrealScriptHome.html",
    "language_ref": "https://docs.unrealengine.com/udk/Three/UnrealScriptReference.html",
    "defaultproperties": "https://docs.unrealengine.com/udk/Three/UnrealScriptDefaultProperties.html",
    "replication": "https://docs.unrealengine.com/udk/Three/ReplicationHome.html",
    "states": "https://docs.unrealengine.com/udk/Three/UnrealScriptStates.html",
    "structs": "https://docs.unrealengine.com/udk/Three/UnrealScriptStructs.html",
    "enums": "https://docs.unrealengine.com/udk/Three/UnrealScriptEnums.html"
  }
}

TOP_LEVEL_TOKENS = re.compile(RULES["regex"]["top_level_tokens"], re.I)
CONTROL_LINE = re.compile(RULES["regex"]["control_line"], re.I)


# =============================================================================
# Issue model
# =============================================================================
@dataclass
class Issue:
    kind: str
    message: str
    line: int                # 1-based line number
    span: Tuple[int, int]    # (start_offset, end_offset)
    fix_preview: Optional[str] = None
    apply_fn: Optional[Callable[[str], str]] = None  # present only if safe fix exists


# =============================================================================
# Syntax highlighter
# =============================================================================
class USHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules: List[Tuple[re.Pattern, QTextCharFormat]] = []

        kw_color = QColor(110, 165, 255)
        type_color = QColor(200, 160, 255)
        comment_color = QColor(130, 175, 130)
        string_color = QColor(230, 170, 140)

        def mkfmt(color: QColor, bold=False, italic=False):
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            if bold:   fmt.setFontWeight(QFont.DemiBold)
            if italic: fmt.setFontItalic(True)
            return fmt

        keywords_re = r"\b(" + "|".join(map(re.escape, RULES["keywords"]["declarations"])) + r")\b"
        types_re = r"\b(" + "|".join(map(re.escape, RULES["keywords"]["types_core"])) + r")\b"
        specifiers_re = r"\b(" + "|".join(map(re.escape, RULES["keywords"]["function_specifiers"] + RULES["keywords"]["class_modifiers"] + RULES["property_specifiers"])) + r")\b"

        self.rules.append((re.compile(keywords_re, re.I), mkfmt(kw_color, bold=True)))
        self.rules.append((re.compile(types_re, re.I), mkfmt(type_color)))
        self.rules.append((re.compile(specifiers_re, re.I), mkfmt(QColor(150, 200, 255))))
        self.rules.append((re.compile(r"//[^\n]*"), mkfmt(comment_color, italic=True)))
        self.rules.append((re.compile(r"/\*.*?\*/", re.S), mkfmt(comment_color, italic=True)))
        self.rules.append((re.compile(r"'[^'\n]*'"), mkfmt(string_color)))
        self.rules.append((re.compile(r'"[^"\n]*"'), mkfmt(string_color)))

        self.error_ranges: List[Tuple[int, int]] = []

    def highlightBlock(self, text):
        block_pos = self.currentBlock().position()
        for pattern, fmt in self.rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)

        for (s, e) in self.error_ranges:
            bs = block_pos
            be = block_pos + len(text)
            if e <= bs or s >= be:
                continue
            start_in_block = max(0, s - bs)
            end_in_block = min(len(text), e - bs)
            if end_in_block > start_in_block:
                fmt = QTextCharFormat()
                fmt.setUnderlineColor(QColor(255, 95, 95))
                fmt.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
                self.setFormat(start_in_block, end_in_block - start_in_block, fmt)

    def setErrorRanges(self, ranges: List[Tuple[int, int]]):
        self.error_ranges = ranges
        self.rehighlight()


# =============================================================================
# Heuristics (Strict + Extended; all rule-bounded)
# =============================================================================
def find_cpptext_missing_brace(doc: str) -> List[Issue]:
    issues = []
    lines = doc.splitlines(True)
    offs = 0
    for i, line in enumerate(lines):
        if re.search(r"\bcpptext\b", line, re.I):
            if '{' in line:
                offs += len(line); continue
            # skip blank/comment lines
            j = i + 1
            while j < len(lines) and re.match(r"^\s*(//.*)?$", lines[j]):
                j += 1
            next_line = lines[j] if j < len(lines) else ""
            if not re.match(r"^\s*\{", next_line):
                start = offs
                end = offs + len(line)
                line_no = i + 1
                def apply(doc_in: str, anchor=line_no):
                    dlines = doc_in.splitlines(True)
                    idx = anchor - 1
                    dlines.insert(idx + 1, "{\n")
                    return "".join(dlines)
                issues.append(Issue(
                    kind="cpptext-brace",
                    message=f"Missing '{{' after 'cpptext' at line {line_no}.",
                    line=line_no, span=(start, end),
                    apply_fn=apply
                ))
        offs += len(line)
    return issues


def safe_close_balance(doc: str) -> List[Issue]:
    """If there are more { than } overall, insert a single } before next top-level token or EOF."""
    issues = []
    open_count = 0
    for ch in doc:
        if ch == '{': open_count += 1
        elif ch == '}': open_count -= 1
    if open_count < 0:
        issues.append(Issue(
            kind="brace-balance",
            message="Too many '}' braces found.",
            line=1, span=(0, min(50, len(doc))),
            apply_fn=None
        ))
    elif open_count > 0:
        def apply(doc_in: str):
            joined = doc_in
            insert_at = len(joined)
            m = re.search(RULES["regex"]["top_level_tokens"], joined, re.I)
            if m:
                insert_at = m.start()
            return joined[:insert_at] + "}\n" + joined[insert_at:]
        issues.append(Issue(
            kind="brace-balance",
            message=f"Unbalanced braces: more '{{' than '}}'.",
            line=1, span=(0, min(50, len(doc))),
            apply_fn=apply
        ))
    return issues


def check_defaultproperties(doc: str) -> List[Issue]:
    issues = []
    for m in re.finditer(r"\bdefaultproperties\b", doc, re.I):
        tail = doc[m.end():]
        if not re.match(r"\s*\{", tail):
            start = max(0, m.start()-5)
            end = min(len(doc), m.end()+5)
            line_no = doc.count("\n", 0, m.start()) + 1
            def apply(doc_in: str, pos=m.end()):
                return doc_in[:pos] + " {\n}\n" + doc_in[pos:]
            issues.append(Issue(
                kind="defaultprops-brace",
                message=f"'defaultproperties' at line {line_no} should be followed by '{{...}}'.",
                line=line_no, span=(start, end),
                apply_fn=apply
            ))
    return issues


def check_semicolons_strict(doc: str) -> List[Issue]:
    """Deterministic semicolon insertion for var decls and simple assignments."""
    issues = []
    lines = doc.splitlines(True)
    offs = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (not stripped or stripped.startswith("//") or stripped.endswith("{") or
            stripped.endswith("}") or stripped.startswith("/*")):
            offs += len(line); continue

        var_decl = re.match(r"^(var(\s+\w+|\([^)]*\))*)\s+[\w\[\]]+(\s*=\s*[^;]+)?$", stripped, re.I)
        assign   = re.match(r"^[A-Za-z_]\w*\s*=\s*[^;]+$", stripped)
        if var_decl or assign:
            if not stripped.endswith(";"):
                start = offs
                end = offs + len(line)
                line_no = i + 1
                def apply(doc_in: str, anchor=line_no):
                    d = doc_in.splitlines(True)
                    idx = anchor - 1
                    if idx < len(d):
                        if d[idx].rstrip().endswith(";"):
                            return doc_in
                        parts = re.split(r"(//.*)$", d[idx])
                        if len(parts) == 3:
                            parts[0] = parts[0].rstrip("\n").rstrip() + ";"
                            d[idx] = parts[0] + parts[1] + "\n"
                        else:
                            d[idx] = d[idx].rstrip("\n").rstrip() + ";\n"
                    return "".join(d)
                issues.append(Issue(
                    kind="semicolon-missing",
                    message=f"Likely missing ';' at line {line_no}.",
                    line=line_no, span=(start, end),
                    apply_fn=apply
                ))
        offs += len(line)
    return issues


# -------- Extended helpers (conservative) --------
def extended_control_paren_balance(doc: str) -> List[Issue]:
    """Insert a missing ')' for single-line if/while/for when opens - closes == 1."""
    issues = []
    lines = doc.splitlines(True)
    offs = 0
    for i, line in enumerate(lines):
        if not CONTROL_LINE.search(line):
            offs += len(line); continue
        # Single-line only
        opens = line.count('(')
        closes = line.count(')')
        if opens - closes == 1:
            start = offs
            end = offs + len(line)
            line_no = i + 1
            def apply(doc_in: str, anchor=line_no):
                d = doc_in.splitlines(True)
                idx = anchor - 1
                s = d[idx].rstrip("\n")
                m = re.search(r"//", s)
                if m:
                    insert_at = m.start()
                    s = s[:insert_at].rstrip() + ")" + " " + s[insert_at:]
                else:
                    s = s.rstrip() + ")"
                d[idx] = s + "\n"
                return "".join(d)
            issues.append(Issue(
                kind="paren-control-close",
                message=f"Control statement may be missing a ')' at line {line_no}.",
                line=line_no, span=(start, end),
                apply_fn=apply
            ))
        offs += len(line)
    return issues


def extended_struct_enum_closer(doc: str) -> List[Issue]:
    """Ensure struct/enum blocks have a closing } before next top-level token or EOF."""
    issues = []
    text = doc
    for m in re.finditer(r"(?im)^\s*(struct|enum)\b[^\n]*\{", text):
        start_block = m.end()
        count = 1
        pos = start_block
        while True:
            nxt = re.search(r"[{}]", text[pos:])
            if not nxt:
                break
            c = text[pos + nxt.start()]
            pos = pos + nxt.start() + 1
            if c == '{': count += 1
            else: count -= 1
            if count == 0:
                break
        if count != 0:
            insert_at = len(text)
            tl = re.search(RULES["regex"]["top_level_tokens"], text[pos:], re.I)
            if tl:
                insert_at = pos + tl.start()
            line_no = text.count("\n", 0, m.start()) + 1
            def apply(doc_in: str, ia=insert_at):
                return doc_in[:ia] + "}\n" + doc_in[ia:]
            issues.append(Issue(
                kind="struct-enum-close",
                message=f"Missing '}}' to close {m.group(1)} block starting at line {line_no}.",
                line=line_no, span=(m.start(), m.end()),
                apply_fn=apply
            ))
    return issues


def extended_unmatched_close_paren(doc: str) -> List[Issue]:
    """
    More ')' than '(' overall: remove the first truly unmatched ')' outside
    comments/strings. One issue per scan.
    """
    issues: List[Issue] = []
    i, n, depth, line = 0, len(doc), 0, 1

    def skip_line_comment(j: int) -> int:
        while j < n and doc[j] != '\n':
            j += 1
        return j

    def skip_block_comment(j: int) -> int:
        j += 2
        nonlocal line
        while j + 1 < n and not (doc[j] == '*' and doc[j+1] == '/'):
            if doc[j] == '\n': line += 1
            j += 1
        return min(j + 2, n)

    def skip_string(j: int, quote: str) -> int:
        j += 1
        nonlocal line
        while j < n:
            if doc[j] == '\\':
                j += 2; continue
            if doc[j] == quote:
                return j + 1
            if doc[j] == '\n': line += 1
            j += 1
        return j

    while i < n:
        c = doc[i]
        if c == '\n':
            line += 1; i += 1; continue
        if c == '/' and i + 1 < n and doc[i+1] == '/':
            i = skip_line_comment(i + 2); continue
        if c == '/' and i + 1 < n and doc[i+1] == '*':
            i = skip_block_comment(i); continue
        if c in ("'", '"'):
            i = skip_string(i, c); continue

        if c == '(':
            depth += 1; i += 1; continue
        if c == ')':
            if depth == 0:
                idx = i; line_no = line
                def apply(doc_in: str, pos=idx):
                    return doc_in[:pos] + doc_in[pos+1:]
                issues.append(Issue(
                    kind="paren-extra-close",
                    message=f"Unmatched ')' at line {line_no}.",
                    line=line_no, span=(idx, idx+1),
                    apply_fn=apply
                ))
                return issues
            depth -= 1; i += 1; continue
        i += 1
    return issues


def extended_unmatched_open_paren(doc: str) -> List[Issue]:
    """
    More '(' than ')' overall: remove the LAST truly unmatched '(' outside
    comments/strings. One issue per scan. (Separate toggle controls use.)
    """
    issues: List[Issue] = []
    n = len(doc)
    i = 0
    line = 1
    stack: List[Tuple[int, int]] = []  # (pos, line)

    def skip_line_comment(j: int) -> int:
        while j < n and doc[j] != '\n':
            j += 1
        return j

    def skip_block_comment(j: int) -> int:
        j += 2
        nonlocal line
        while j + 1 < n and not (doc[j] == '*' and doc[j+1] == '/'):
            if doc[j] == '\n': line += 1
            j += 1
        return min(j + 2, n)

    def skip_string(j: int, quote: str) -> int:
        j += 1
        nonlocal line
        while j < n:
            if doc[j] == '\\':
                j += 2; continue
            if doc[j] == quote:
                return j + 1
            if doc[j] == '\n': line += 1
            j += 1
        return j

    while i < n:
        c = doc[i]
        if c == '\n':
            line += 1; i += 1; continue
        if c == '/' and i + 1 < n and doc[i+1] == '/':
            i = skip_line_comment(i + 2); continue
        if c == '/' and i + 1 < n and doc[i+1] == '*':
            i = skip_block_comment(i); continue
        if c in ("'", '"'):
            i = skip_string(i, c); continue

        if c == '(':
            stack.append((i, line)); i += 1; continue
        if c == ')':
            if stack: stack.pop()
            i += 1; continue
        i += 1

    if not stack:
        return issues

    idx, line_no = stack[-1]  # rightmost unmatched '('

    def apply(doc_in: str, pos=idx):
        return doc_in[:pos] + doc_in[pos+1:]

    issues.append(Issue(
        kind="paren-extra-open",
        message=f"Unmatched '(' at line {line_no}.",
        line=line_no, span=(idx, idx+1),
        apply_fn=apply
    ))
    return issues


# =============================================================================
# Scanning (mode-aware; unmatched '(' controlled by its own toggle)
# =============================================================================
def scan_doc_for_issues(doc: str, extended: bool = False) -> List[Issue]:
    issues: List[Issue] = []
    # Strict-safe
    issues += find_cpptext_missing_brace(doc)
    issues += safe_close_balance(doc)
    issues += check_defaultproperties(doc)
    issues += check_semicolons_strict(doc)

    # Global paren imbalance (informational only, never auto-fix)
    open_p = doc.count('(')
    close_p = doc.count(')')
    if open_p != close_p:
        diff = open_p - close_p
        msg = "More '(' than ')'." if diff > 0 else "More ')' than '('."
        issues.append(Issue(
            kind="paren-balance",
            message=f"Unbalanced parentheses: {msg}",
            line=1, span=(0, min(50, len(doc))),
            apply_fn=None
        ))

    # Extended (conservative)
    if extended:
        issues += extended_control_paren_balance(doc)
        issues += extended_struct_enum_closer(doc)
        issues += extended_unmatched_close_paren(doc)

    # De-dup
    dedup = {}
    res = []
    for it in issues:
        key = (it.kind, it.line, it.message, it.span)
        if key not in dedup:
            dedup[key] = True
            res.append(it)
    return res


# =============================================================================
# Modern UI helpers (dark theme)
# =============================================================================
def apply_modern_style(app: QApplication):
    app.setStyle(QStyleFactory.create("Fusion"))
    from PyQt5.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(30, 34, 40))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(22, 25, 30))
    palette.setColor(QPalette.AlternateBase, QColor(34, 38, 45))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(40, 45, 52))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.Highlight, QColor(70, 120, 255))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { font-family: "Segoe UI", "Inter", "Roboto", sans-serif; font-size: 11pt; }
        QSplitter::handle { background: #2b2f36; }
        QListWidget { border: 1px solid #3a3f46; border-radius: 8px; padding: 6px; }
        QPlainTextEdit, QTextBrowser { border: 1px solid #3a3f46; border-radius: 8px; padding: 8px;
                                       selection-background-color: #3d68ff; }
        QPushButton {
            background: #3b4250; border: 1px solid #4a5161; border-radius: 10px; padding: 6px 12px;
        }
        QPushButton:hover { background: #465066; }
        QPushButton:pressed { background: #323846; }
        QCheckBox { padding: 0 6px; }
        QLabel#status { color: #b8c1d1; }
        QTabWidget::pane { border: 1px solid #3a3f46; border-radius: 8px; }
        QTabBar::tab { padding: 6px 10px; }
        QComboBox { border: 1px solid #3a3f46; border-radius: 8px; padding: 4px 8px; }
    """)


# =============================================================================
# GUI
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Unreal Script Editor — Batch")
        self.resize(1500, 940)

        self.current_path: Optional[str] = None
        self.original_text: str = ""
        self.scanned_folder: Optional[str] = None
        self.folder_results: Dict[str, int] = {}  # file_path -> issue_count

        # ---------- Top controls ----------
        open_file_btn = QPushButton("Open .uc")
        open_file_btn.clicked.connect(self.open_file)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self.open_folder)

        rescan_folder_btn = QPushButton("Rescan Folder")
        rescan_folder_btn.clicked.connect(self.rescan_folder)
        rescan_file_btn = QPushButton("Scan Current")
        rescan_file_btn.clicked.connect(self.scan_now)

        self.strict_cb = QCheckBox("Strict")
        self.strict_cb.setChecked(True)
        self.extended_cb = QCheckBox("Extended fixes")
        self.extended_cb.setChecked(False)
        self.unmatched_open_cb = QCheckBox("Unmatched '(' fixer")
        self.unmatched_open_cb.setChecked(False)

        for cb in (self.strict_cb, self.extended_cb, self.unmatched_open_cb):
            cb.stateChanged.connect(self._on_mode_changed)

        self.confirm_each = QCheckBox("Prompt before fixes")
        self.confirm_each.setChecked(True)

        self.apply_selected_btn = QPushButton("Apply Selected")
        self.apply_selected_btn.clicked.connect(self.apply_selected)
        self.apply_selected_btn.setEnabled(False)

        self.apply_all_btn = QPushButton("Apply All")
        self.apply_all_btn.clicked.connect(self.apply_all)
        self.apply_all_btn.setEnabled(False)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save)
        save_as_btn = QPushButton("Save As…")
        save_as_btn.clicked.connect(self.save_as)
        fix_save_all_btn = QPushButton("Fix All & Save All…")
        fix_save_all_btn.clicked.connect(self.fix_and_save_all)

        top_bar = QHBoxLayout()
        for w in (
            open_file_btn, open_folder_btn, rescan_folder_btn, rescan_file_btn,
            self.strict_cb, self.extended_cb, self.unmatched_open_cb,
            self.confirm_each,
            self.apply_selected_btn, self.apply_all_btn, save_btn, save_as_btn, fix_save_all_btn
        ):
            top_bar.addWidget(w)
        top_bar.addStretch(1)

        # ---------- Left: files + issues ----------
        self.files_list = QListWidget()
        self.files_list.itemSelectionChanged.connect(self.on_file_selected)
        self.files_list.setMinimumWidth(430)

        self.issue_list = QListWidget()
        self.issue_list.currentRowChanged.connect(self.on_issue_selected)

        left_split = QSplitter(Qt.Vertical)
        left_split.addWidget(self.files_list)
        left_split.addWidget(self.issue_list)
        left_split.setSizes([520, 400])

        # ---------- Right: Editor + Docs (tabs) ----------
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Consolas" if sys.platform.startswith("win") else "Monospace", 11)
        font.setStyleHint(QFont.Monospace)
        self.editor.setFont(font)

        # Docs tab (embedded if possible)
        self.docs_widget = QWidget()
        dv_layout = QVBoxLayout(self.docs_widget)
        self.docs_picker = QComboBox()
        self.docs_picker.addItem("UnrealScript Home", RULES["docs"]["home"])
        self.docs_picker.addItem("Language Reference", RULES["docs"]["language_ref"])
        self.docs_picker.addItem("defaultproperties", RULES["docs"]["defaultproperties"])
        self.docs_picker.addItem("Replication", RULES["docs"]["replication"])
        self.docs_picker.addItem("States", RULES["docs"]["states"])
        self.docs_picker.addItem("Structs", RULES["docs"]["structs"])
        self.docs_picker.addItem("Enums", RULES["docs"]["enums"])

        dv_layout.addWidget(self.docs_picker)

        if WEBENGINE_AVAILABLE:
            self.docs_view = QWebEngineView()
            self.docs_picker.currentIndexChanged.connect(self._on_docs_picker_changed)
            # initial load (live-first)
            self._load_doc_url_live_first(self.docs_picker.currentData())
            dv_layout.addWidget(self.docs_view, 1)
        else:
            # Lightweight fallback: QTextBrowser + external open for links
            self.docs_view = QTextBrowser()
            self.docs_view.setOpenExternalLinks(True)
            self.docs_picker.currentIndexChanged.connect(self._load_docs_fallback)
            dv_layout.addWidget(self.docs_view, 1)
            self._load_docs_fallback()  # initial

        right_tabs = QTabWidget()
        right_tabs.addTab(self.editor, "Editor")
        right_tabs.addTab(self.docs_widget, "Docs")

        # ---------- Status ----------
        self.status_label = QLabel("Open a folder to batch scan, or open a single .uc file.")
        self.status_label.setObjectName("status")

        # ---------- Main split ----------
        main_split = QSplitter(Qt.Horizontal)
        main_split.addWidget(left_split)
        main_split.addWidget(right_tabs)
        main_split.setStretchFactor(1, 4)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top_bar)
        layout.addWidget(main_split)
        layout.addWidget(self.status_label)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self.setCentralWidget(central)

        self.highlighter = USHighlighter(self.editor.document())
        self.issues: List[Issue] = []

    # ---------- Docs: live-first (WebEngine) with local-only fallback ----------
    def _on_docs_picker_changed(self, *_):
        target = self.docs_picker.currentData()
        self._load_doc_url_live_first(target)

    def _load_doc_url_live_first(self, url: str):
        """
        WebEngine path: try live URL first; if the load fails (offline/blocked/404),
        fall back to a local HTML file under ./docs_udk/ (if present).
        """
        live = QUrl(url)
        # disconnect any previous handler to avoid multi-firing
        try:
            self.docs_view.loadFinished.disconnect()
        except Exception:
            pass

        def _after_load(ok: bool, attempted_url=url):
            if ok:
                self.status_label.setText("Docs: Live site loaded.")
                return
            # live failed → try local fallback
            local_qurl = self._local_doc_qurl(attempted_url)
            if local_qurl is not None:
                self.docs_view.setUrl(local_qurl)
                self.status_label.setText("Docs: Live site unavailable — showing local copy.")
            else:
                # leave the failed page and inform the user
                self.status_label.setText("Docs: Live site unavailable and no local copy found.")

        self.docs_view.loadFinished.connect(_after_load)
        self.docs_view.setUrl(live)

    # ---------- Docs: non-WebEngine fallback UI ----------
    def _load_docs_fallback(self):
        url = self.docs_picker.currentData()
        # Show the live link; if a local file exists, show that link too.
        local_qurl = self._local_doc_qurl(url)
        local_link = (f'<p>Local copy: <a href="{local_qurl.toString()}">{local_qurl.toString()}</a></p>'
                      if local_qurl is not None else '')
        html = (f'<h3>Docs</h3>'
                f'<p><a href="{url}">{url}</a></p>'
                f'{local_link}')
        self.docs_view.setHtml(html)

    def _local_doc_qurl(self, url: str) -> Optional[QUrl]:
        """
        Resolve a local docs file if present in ./docs_udk/.
        We deliberately do NOT use this unless live load fails (WebEngine path).
        Tries:
          - docs_udk/<full host+path>  (mirrored structure)
          - docs_udk/<basename>.html   (flat saved file)
        """
        root = pathlib.Path("./docs_udk")
        # full host+path layout
        rel = url.replace("https://", "").replace("http://", "")
        p1 = root / rel
        if p1.exists():
            return QUrl.fromLocalFile(str(p1.resolve()))
        # flat basename layout
        base = pathlib.Path(url).name
        p2 = root / base
        if p2.exists():
            return QUrl.fromLocalFile(str(p2.resolve()))
        return None

    # ---------- Mode helpers ----------
    def _on_mode_changed(self):
        # Strict and Extended are mutually exclusive
        if self.sender() is self.strict_cb and self.strict_cb.isChecked():
            self.extended_cb.setChecked(False)
        elif self.sender() is self.extended_cb and self.extended_cb.isChecked():
            self.strict_cb.setChecked(False)
        self.scan_now()

    def _extended_mode(self) -> bool:
        return self.extended_cb.isChecked()

    def _unmatched_open_enabled(self) -> bool:
        return self.unmatched_open_cb.isChecked()

    # ---------- File ops ----------
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open UnrealScript (.uc)", "", "UnrealScript (*.uc);;All Files (*)")
        if not path:
            return
        self.load_file(path)
        self.files_list.clearSelection()

    def load_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Open Failed", f"Couldn't open file:\n{path}\n\n{e}")
            return
        self.current_path = path
        self.original_text = txt
        self.editor.setPlainText(txt)
        self.status_label.setText(f"Loaded: {path}")
        self.scan_now()

    def save(self):
        if not self.current_path:
            return self.save_as()
        try:
            with open(self.current_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(self.editor.toPlainText())
            self.status_label.setText(f"Saved to: {self.current_path}")
            self._after_successful_save(prior_path=self.current_path)
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", f"Couldn't save file:\n{self.current_path}\n\n{e}")

    def save_as(self):
        if not self.editor.toPlainText().strip():
            QMessageBox.information(self, "Nothing to Save", "There is no text to save.")
            return
        prior = self.current_path
        initial = os.path.basename(prior) if prior else "fixed.uc"
        path, _ = QFileDialog.getSaveFileName(self, "Save As", initial, "UnrealScript (*.uc);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", errors="replace") as f:
                f.write(self.editor.toPlainText())
            self.status_label.setText(f"Saved to: {path}")
            self.current_path = path
            self._after_successful_save(prior_path=prior)
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", f"Couldn't save file:\n{path}\n\n{e}")

    # ---------- Folder scan ----------
    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder Containing .uc Files")
        if not folder:
            return
        self.scanned_folder = folder
        self.scan_folder(folder)

    def rescan_folder(self):
        if not self.scanned_folder:
            QMessageBox.information(self, "No Folder", "Open a folder first.")
            return
        self.scan_folder(self.scanned_folder)

    def scan_folder(self, folder: str):
        self.files_list.clear()
        self.folder_results.clear()
        count_files = 0
        count_problem_files = 0

        for root, _, files in os.walk(folder):
            for fn in files:
                if not fn.lower().endswith(".uc"):
                    continue
                count_files += 1
                full = os.path.join(root, fn)
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        txt = f.read()
                except Exception:
                    continue
                issues = scan_doc_for_issues(txt, extended=self._extended_mode())
                # optional unmatched '(' fixer:
                if self._unmatched_open_enabled():
                    issues += extended_unmatched_open_paren(txt)
                # de-dup small pass
                dedup = {}
                uniq = []
                for it in issues:
                    key = (it.kind, it.line, it.message, it.span)
                    if key not in dedup:
                        dedup[key] = True; uniq.append(it)
                if uniq:
                    count_problem_files += 1
                    rel = os.path.relpath(full, folder)
                    self.folder_results[full] = len(uniq)
                    item = QListWidgetItem(f"{rel}  —  {len(uniq)} issue(s)")
                    item.setData(Qt.UserRole, full)
                    self.files_list.addItem(item)

        if count_problem_files == 0:
            self.files_list.addItem(QListWidgetItem("✅ No files with detectable issues."))

        self.status_label.setText(f"Scanned {count_files} .uc file(s) in '{folder}'. {count_problem_files} file(s) have issues.")

    # ---------- Scan current file ----------
    def scan_now(self):
        doc = self.editor.toPlainText()
        issues = scan_doc_for_issues(doc, extended=self._extended_mode())
        if self._unmatched_open_enabled():
            issues += extended_unmatched_open_paren(doc)

        # de-dup
        dedup = {}
        self.issues = []
        for it in issues:
            key = (it.kind, it.line, it.message, it.span)
            if key not in dedup:
                dedup[key] = True
                self.issues.append(it)

        self.issue_list.clear()
        if not self.issues:
            self.issue_list.addItem(QListWidgetItem("✅ No issues detected in current file."))
            self.apply_selected_btn.setEnabled(False)
            self.apply_all_btn.setEnabled(False)
            self.highlighter.setErrorRanges([])
            return

        ranges = []
        for it in self.issues:
            tag = "🛠️" if it.apply_fn else "🔍"
            item = QListWidgetItem(f"{tag} [{it.kind}] Line {it.line}: {it.message}")
            item.setData(Qt.UserRole, bool(it.apply_fn))
            self.issue_list.addItem(item)
            ranges.append(it.span)

        self.highlighter.setErrorRanges(ranges)
        self.apply_all_btn.setEnabled(True)

        current = self.issue_list.currentRow()
        if current >= 0:
            auto = self.issue_list.item(current).data(Qt.UserRole)
            self.apply_selected_btn.setEnabled(bool(auto))
            self.apply_selected_btn.setToolTip("Apply fix" if auto else "Manual review only")
        else:
            self.apply_selected_btn.setEnabled(False)
            self.apply_selected_btn.setToolTip("Select an issue")

        if self.current_path:
            self.status_label.setText(f"{os.path.basename(self.current_path)}: {len(self.issues)} issue(s) detected.")
        else:
            self.status_label.setText(f"{len(self.issues)} issue(s) detected.")

    # ---------- Selection handlers ----------
    def on_file_selected(self):
        items = self.files_list.selectedItems()
        if not items:
            return
        path = items[0].data(Qt.UserRole)
        if path:
            self.load_file(path)

    def on_issue_selected(self, row: int):
        if row < 0 or row >= len(self.issues):
            self.apply_selected_btn.setEnabled(False)
            self.apply_selected_btn.setToolTip("Select an issue")
            return
        issue = self.issues[row]
        cursor = self.editor.textCursor()
        cursor.setPosition(issue.span[0])
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

        auto = self.issue_list.item(row).data(Qt.UserRole)
        self.apply_selected_btn.setEnabled(bool(auto))
        self.apply_selected_btn.setToolTip("Apply fix" if auto else "Manual review only")

        if issue.fix_preview:
            self.status_label.setText(f"Preview available. Use 'Apply Selected' to confirm. ({issue.kind})")
        else:
            self.status_label.setText(issue.message)

    # ---------- Apply fixes (no pruning until Save) ----------
    def apply_selected(self):
        row = self.issue_list.currentRow()
        if row < 0 or row >= len(self.issues):
            return
        issue = self.issues[row]
        if not issue.apply_fn:
            return
        if self.confirm_each.isChecked():
            ok = QMessageBox.question(
                self, "Apply Fix?",
                f"{issue.message}\n\nApply this change?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if ok != QMessageBox.Yes:
                return
        new_text = issue.apply_fn(self.editor.toPlainText())
        self.editor.setPlainText(new_text)
        self._post_apply_refresh(save_backup=True)

    def apply_all(self):
        if not self.issues:
            return
        auto_issues = [i for i in self.issues if i.apply_fn]
        if not auto_issues:
            return
        if self.confirm_each.isChecked():
            ok = QMessageBox.question(
                self, "Apply All Fixes?",
                f"Apply {len(auto_issues)} auto-fixable change(s)?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if ok != QMessageBox.Yes:
                return
        text = self.editor.toPlainText()
        for it in auto_issues:
            try: text = it.apply_fn(text)
            except Exception: pass
        self.editor.setPlainText(text)
        self._post_apply_refresh(save_backup=True)

    def _post_apply_refresh(self, save_backup: bool):
        if self.current_path and save_backup:
            try:
                bak_path = self.current_path + ".bak"
                if not os.path.exists(bak_path):
                    with open(bak_path, "w", encoding="utf-8", errors="replace") as f:
                        f.write(self.original_text)
            except Exception as e:
                QMessageBox.warning(self, "Backup Failed", f"Couldn't write backup: {e}")
        self.scan_now()

    # ---------- Batch: Fix All & Save All ----------
    def fix_and_save_all(self):
        if not self.scanned_folder:
            QMessageBox.information(self, "No Folder", "Open a folder first to use batch fix/save.")
            return

        paths = []
        for i in range(self.files_list.count()):
            it = self.files_list.item(i)
            p = it.data(Qt.UserRole)
            if p:
                paths.append(p)
        if not paths:
            QMessageBox.information(self, "Nothing To Do", "There are no files with issues in the list.")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "Choose Output Folder for Fixed Files")
        if not out_dir:
            return

        mode_name = "Extended" if self._extended_mode() else "Strict"
        if self._unmatched_open_enabled():
            mode_name += " + Unmatched '(' fixer"
        ok = QMessageBox.question(
            self, "Fix & Save All",
            f"Mode: {mode_name}\nProcess {len(paths)} file(s), apply all auto-fixes, and save to:\n\n{out_dir}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        total = 0
        fixed_to_zero = 0
        still_has_issues = 0

        for src_path in paths:
            try:
                with open(src_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception:
                continue

            text = self._apply_autofixes(text)

            rel = os.path.relpath(src_path, self.scanned_folder)
            dest_path = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            try:
                with open(dest_path, "w", encoding="utf-8", errors="replace") as f:
                    f.write(text)
            except Exception:
                continue

            remaining = self._count_issues_for_text(text)
            self._prune_or_update_entry(src_path, remaining)

            total += 1
            if remaining == 0: fixed_to_zero += 1
            else: still_has_issues += 1

        self.status_label.setText(
            f"Batch saved {total} file(s) → {fixed_to_zero} clean, {still_has_issues} still have issues."
        )

    def _apply_autofixes(self, text: str) -> str:
        extended = self._extended_mode()
        issues = scan_doc_for_issues(text, extended=extended)
        if self._unmatched_open_enabled():
            issues += extended_unmatched_open_paren(text)
        for it in issues:
            if it.apply_fn:
                try: text = it.apply_fn(text)
                except Exception: pass
        return text

    def _count_issues_for_text(self, text: str) -> int:
        issues = scan_doc_for_issues(text, extended=self._extended_mode())
        if self._unmatched_open_enabled():
            issues += extended_unmatched_open_paren(text)
        # de-dup
        dedup = {}
        c = 0
        for it in issues:
            key = (it.kind, it.line, it.message, it.span)
            if key not in dedup:
                dedup[key] = True; c += 1
        return c

    # ---------- List maintenance & prune-on-save ----------
    def _ensure_no_files_message(self):
        if self.files_list.count() == 0:
            self.files_list.addItem(QListWidgetItem("✅ No files with detectable issues."))

    def _remove_no_files_message(self):
        for i in range(self.files_list.count()):
            if self.files_list.item(i).text().startswith("✅ No files"):
                self.files_list.takeItem(i)
                break

    def _prune_or_update_entry(self, path: Optional[str], remaining_issues: int):
        if not path or not self.scanned_folder:
            return
        row_idx = -1
        for i in range(self.files_list.count()):
            it = self.files_list.item(i)
            p = it.data(Qt.UserRole)
            if p and os.path.normcase(p) == os.path.normcase(path):
                row_idx = i
                break

        if remaining_issues == 0:
            if row_idx != -1:
                self.files_list.takeItem(row_idx)
            if path in self.folder_results:
                del self.folder_results[path]
            self._ensure_no_files_message()
        else:
            rel = os.path.relpath(path, self.scanned_folder)
            label = f"{rel}  —  {remaining_issues} issue(s)"
            if row_idx == -1:
                self._remove_no_files_message()
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, path)
                self.files_list.addItem(item)
            else:
                self.files_list.item(row_idx).setText(label)
            self.folder_results[path] = remaining_issues

    def _after_successful_save(self, prior_path: Optional[str]):
        current_text = self.editor.toPlainText()
        num_issues = self._count_issues_for_text(current_text)
        if prior_path and prior_path != self.current_path:
            self._prune_or_update_entry(prior_path, 0)
        self._prune_or_update_entry(self.current_path, num_issues)


# =============================================================================
# Main
# =============================================================================
def main():
    app = QApplication(sys.argv)
    apply_modern_style(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
