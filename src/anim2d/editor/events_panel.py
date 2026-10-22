"""Events window: the skeleton's events with their default values, and the current animation's
event keys, all editable in place."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .document import EditorError
from .form_widgets import section
from .state import EditorState

EVENT_COLUMNS = ("name", "int", "float", "string", "audio", "volume", "balance")
KEY_COLUMNS = ("time", "name", "int", "float", "string", "volume", "balance")


class EventsPanel(QWidget):
    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self._filling = False
        self.events = QTableWidget(0, len(EVENT_COLUMNS))
        self.events.setHorizontalHeaderLabels(["Event", "Int", "Float", "String", "Audio", "Volume", "Balance"])
        self.events.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.events.verticalHeader().hide()
        self.events.itemChanged.connect(self._event_edited)
        new, rename, delete = QPushButton("+ New"), QPushButton("Rename…"), QPushButton("Delete")
        new.clicked.connect(self.new_event)
        rename.clicked.connect(self.rename_event)
        delete.clicked.connect(self.delete_event)
        self.keys_title = section("Keys")
        self.keys = QTableWidget(0, len(KEY_COLUMNS))
        self.keys.setHorizontalHeaderLabels(["Time (s)", "Event", "Int", "Float", "String", "Volume", "Balance"])
        self.keys.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.keys.verticalHeader().hide()
        self.keys.itemChanged.connect(self._key_edited)
        self.keys.cellDoubleClicked.connect(lambda row, col: col == 0 and self.go_to(row))
        self.key_button = QPushButton("⚑ Key at current time")
        self.key_button.setToolTip("Key the event selected above at the timeline's time")
        go, delete_key = QPushButton("Go to"), QPushButton("Delete key")
        self.key_button.clicked.connect(self.key_event)
        go.clicked.connect(lambda: self.go_to(self.keys.currentRow()))
        delete_key.clicked.connect(self.delete_key)
        self.note = QLabel()
        self.note.setStyleSheet("color: #a9a9a9;")
        layout = QVBoxLayout(self)
        layout.addWidget(section("Events (default values)"))
        layout.addWidget(self.events, 1)
        row = QHBoxLayout()
        for b in (new, rename, delete):
            row.addWidget(b)
        row.addStretch(1)
        self.audio_button = QPushButton("🔊 Audio folder…")
        self.audio_button.setToolTip("Where the events' audio paths are found (saved with the project)")
        self.audio_button.clicked.connect(self.choose_audio_folder)
        self.test_button = QPushButton("▶ Play sound")
        self.test_button.clicked.connect(self.play_selected)
        row.addWidget(self.audio_button)
        row.addWidget(self.test_button)
        layout.addLayout(row)
        layout.addWidget(self.keys_title)
        layout.addWidget(self.keys, 1)
        row = QHBoxLayout()
        for b in (self.key_button, go, delete_key):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(self.note)
        for sig in (state.document_changed, state.mode_changed):
            sig.connect(self.refresh)
        self.refresh()

    def choose_audio_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path
        from .event_audio import audio_dir, set_audio_dir
        start = audio_dir(self.state.doc)
        folder = QFileDialog.getExistingDirectory(self, "Audio folder", str(start or ""))
        if folder:
            set_audio_dir(self.state.doc, Path(folder))
            self.state.doc.notify()

    def play_selected(self) -> None:
        from .event_audio import EventAudio, audio_file
        name = self.selected_event()
        event = self.state.doc.data.get("events", {}).get(name or "", {})
        if not event.get("audio"):
            QMessageBox.information(self, "Events", "Give the event an audio path first")
            return
        if audio_file(self.state.doc, event["audio"]) is None:
            QMessageBox.warning(self, "Events", f"{event['audio']} isn't in the audio folder")
            return
        if not hasattr(self, "_audio"):
            self._audio = EventAudio(self.state.doc)
        self._audio.doc = self.state.doc
        self._audio.play(event["audio"], event.get("volume", 1))

    # --- filling ---------------------------------------------------------------------------

    def refresh(self) -> None:
        doc, st = self.state.doc, self.state
        self._filling = True
        selected = self.selected_event()
        names = doc.event_names()
        self.events.setRowCount(len(names))
        for r, name in enumerate(names):
            ev = doc.data["events"][name]
            for c, field in enumerate(EVENT_COLUMNS):
                value = name if field == "name" else ev.get(field, doc.EVENT_DEFAULTS[field])
                item = QTableWidgetItem(str(value))
                if field == "name":
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setData(Qt.ItemDataRole.UserRole, name)
                self.events.setItem(r, c, item)
        if selected in names:
            self.events.selectRow(names.index(selected))
        keys = doc.event_keys(st.animation) if st.animation else []
        self.keys_title.setText(f"Keys in '{st.animation}'" if st.animation else "Keys (choose an animation)")
        self.keys.setRowCount(len(keys))
        for r, key in enumerate(keys):
            ev = doc.data["events"].get(key.get("name"), {})
            for c, field in enumerate(KEY_COLUMNS):
                if field == "time":
                    value = f"{key.get('time', 0):g}"
                elif field == "name":
                    value = key.get("name", "")
                else:
                    value = key.get(field, ev.get(field, doc.EVENT_DEFAULTS[field]))
                item = QTableWidgetItem(str(value))
                if field not in ("time", "name") and field not in key:
                    item.setForeground(Qt.GlobalColor.gray)  # the event's default
                self.keys.setItem(r, c, item)
        self.key_button.setEnabled(bool(st.animation) and bool(names))
        self.note.setText("Grey values come from the event; typing a value overrides it for that key. "
                          "Double-click a time to go there.")
        self._filling = False

    def selected_event(self) -> str | None:
        row = self.events.currentRow()
        item = self.events.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # --- editing ---------------------------------------------------------------------------

    def _run(self, func) -> bool:
        try:
            func()
            return True
        except (EditorError, ValueError) as exc:
            QMessageBox.warning(self, "Events", str(exc))
            self.refresh()
            return False

    def _event_edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        field = EVENT_COLUMNS[item.column()]
        name = self.events.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        self._run(lambda: self.state.doc.set_event(name, **{field: item.text()}))

    def _key_edited(self, item: QTableWidgetItem) -> None:
        if self._filling or not self.state.animation:
            return
        field = KEY_COLUMNS[item.column()]
        self._run(lambda: self.state.doc.set_event_key(self.state.animation, item.row(), **{field: item.text()}))

    def new_event(self, name: str | None = None) -> None:
        if name is None:
            name, ok = QInputDialog.getText(self, "New event", "Name")
            if not ok or not name.strip():
                return
        self._run(lambda: self.state.doc.add_event(name.strip()))

    def rename_event(self, new: str | None = None) -> None:
        old = self.selected_event()
        if old is None:
            return
        if new is None:
            new, ok = QInputDialog.getText(self, "Rename event", "Name", text=old)
            if not ok:
                return
        self._run(lambda: self.state.doc.rename_event(old, new.strip()))

    def delete_event(self) -> None:
        name = self.selected_event()
        if name is not None:
            self._run(lambda: self.state.doc.delete_event(name))

    def key_event(self) -> None:
        st = self.state
        name = self.selected_event() or (st.doc.event_names() or [None])[0]
        if name and st.animation:
            self._run(lambda: st.doc.key_event(st.animation, name, st.time))

    def delete_key(self) -> None:
        row = self.keys.currentRow()
        if row >= 0 and self.state.animation:
            self._run(lambda: self.state.doc.delete_event_key(self.state.animation, row))

    def go_to(self, row: int) -> None:
        st = self.state
        keys = st.doc.event_keys(st.animation) if st.animation else []
        if 0 <= row < len(keys):
            st.set_mode("animate")
            st.set_time(keys[row].get("time", 0))
