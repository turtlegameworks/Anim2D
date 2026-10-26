"""Anim2D main window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QToolBar, QVBoxLayout, QWidget)

from ..presets.library import PresetLibrary, default_user_library_dir
from ..ui.dev_badge import add_dev_badge, title_suffix
from ..ui.template_dialog import TemplateImportDialog
from . import APP_NAME
from ..resources import settings as app_settings
from .document import PROJECT_SUFFIX, Document, EditorError
from .export_dialog import ExportContext, ExportDialog
from .fx_dialog import InsertFxDialog
from .preview_panel import PreviewPanel
from .properties import PropertiesPanel
from .settings_dialog import EditorSettings, SettingsDialog
from .shortcuts_dialog import ShortcutRegistry, ShortcutsDialog, add_access_keys
from .state import EditorState
from .timeline import TimelinePanel
from .tool_panel import TOOLS, ModeSwitch, ToolPanel
from .weights_panel import WeightsPanel
from .events_panel import EventsPanel
from .history_panel import HistoryPanel
from .tree_panel import TreePanel
from .viewport import Viewport

PROJECT_FILTER = "Anim2D project (*.anim2d *.rig2d)"


class EditorWindow(QMainWindow):
    def __init__(self, doc: Document | None = None, library: PresetLibrary | None = None,
                 settings_store: QSettings | None = None):
        super().__init__()
        self.store = settings_store or app_settings("editor")
        from .update_ui import UpdateController
        self.updates = UpdateController(self, self.store)
        self.settings = EditorSettings.load(self.store)
        self.settings.apply()
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave)
        self.state = EditorState(doc or Document())
        self._library = library
        self.viewport = Viewport(self.state)
        self.tree = TreePanel(self.state)
        self.properties = PropertiesPanel(self.state)
        self.timeline = TimelinePanel(self.state)
        # The viewport with the tool bar along its bottom, like Spine's.
        self.tool_panel = ToolPanel(self.state)
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(0)
        centre_layout.addWidget(self.viewport, 1)
        tools = self._scrolled(self.tool_panel)  # scrolls sideways when the viewport is narrow
        tools.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bar = tools.horizontalScrollBar().sizeHint().height()
        tools.setFixedHeight(self.tool_panel.minimumSizeHint().height() + bar)
        centre_layout.addWidget(tools)
        self.setCentralWidget(centre)
        # Tree on the right with the selection's properties under it, as in Spine.
        self.tree_dock = self._dock("Tree", self._scrolled(self.tree), Qt.DockWidgetArea.RightDockWidgetArea)
        scroll = QScrollArea()  # long property lists scroll instead of squeezing the tree
        scroll.setWidget(self.properties)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.properties_dock = self._dock("Properties", scroll, Qt.DockWidgetArea.RightDockWidgetArea)
        self.splitDockWidget(self.tree_dock, self.properties_dock, Qt.Orientation.Vertical)
        # the Timeline bar and the Preview scroll when made smaller than their controls, so their
        # windows can be any size
        self.timeline_dock = self._dock("Timeline", self._scrolled(self.timeline), Qt.DockWidgetArea.BottomDockWidgetArea)
        self.dopesheet_dock = self._dock("Dopesheet", self.timeline.sheet, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.graph_dock = self._dock("Graph", self.timeline.graph, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.splitDockWidget(self.timeline_dock, self.dopesheet_dock, Qt.Orientation.Vertical)
        self.tabifyDockWidget(self.dopesheet_dock, self.graph_dock)
        self.dopesheet_dock.raise_()
        self.preview = PreviewPanel(self.state)
        self.preview_dock = self._dock("Preview", self._scrolled(self.preview), Qt.DockWidgetArea.LeftDockWidgetArea)
        self.preview_dock.hide()
        self.weights = WeightsPanel(self.state)
        self.weights_dock = self._dock("Weights", self.weights, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.weights_dock.hide()
        self.events = EventsPanel(self.state)
        self.events_dock = self._dock("Events", self.events, Qt.DockWidgetArea.RightDockWidgetArea)
        self.events_dock.hide()
        self.history = HistoryPanel(self.state)
        self.history_dock = self._dock("History", self.history, Qt.DockWidgetArea.RightDockWidgetArea)
        self.history_dock.hide()
        self.state.tool_changed.connect(self._tool_changed)
        self._layout_mode: str | None = None
        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.validation_label = QLabel()
        self.statusBar().addPermanentWidget(self.validation_label)
        self.update_button = QPushButton()
        self.update_button.setFlat(True)
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.setToolTip("A new version of Anim2D is out: click to see what's new and install it")
        self.update_button.clicked.connect(self._show_update)
        self.update_button.hide()
        self.statusBar().addPermanentWidget(self.update_button)
        self.updated_button = self._updated_note()
        self.dev_badge = add_dev_badge(self)  # local test builds only: right-most in the status bar
        self.updates.available.connect(self._update_available)
        self.viewport.status.connect(self.status_label.setText)
        self.tree.insert_fx_requested.connect(self.insert_fx)
        self.tree.events_requested.connect(lambda: (self.events_dock.show(), self.events_dock.raise_()))
        self.state.document_changed.connect(self._doc_changed)
        self._build_menus()
        self._doc_changed()
        if self.store.value("geometry"):
            self.restoreGeometry(self.store.value("geometry"))
        else:
            self.resize(1500, 920)
        self.state.mode_changed.connect(self._apply_mode_layout)
        self.state.message.connect(self.status_label.setText)
        self._apply_mode_layout()
        self._apply_settings()
        self._watch_layer_files()
        self.load_reference()
        self.updates.schedule_startup_check()

    # --- Setup / Animate interfaces ---------------------------------------------------------

    def _apply_mode_layout(self) -> None:
        """Each mode has its own layout, like Spine: Setup without the timeline; Animate with the
        timeline and the Preview. Changes the user makes to a mode's layout are remembered."""
        mode = self.state.mode
        if mode == self._layout_mode:
            return
        if self._layout_mode is not None:
            self.store.setValue(f"layout3/{self._layout_mode}", self.saveState())
        saved = self.store.value(f"layout3/{mode}")
        if not (saved and self.restoreState(saved)):
            animate = mode == "animate"
            for dock in (self.timeline_dock, self.dopesheet_dock, self.graph_dock, self.preview_dock):
                dock.setVisible(animate)
            if animate:
                self.dopesheet_dock.raise_()
            self.resizeDocks([self.tree_dock, self.properties_dock], [55, 45], Qt.Orientation.Vertical)
            self.resizeDocks([self.tree_dock], [340], Qt.Orientation.Horizontal)
            if animate:
                self.resizeDocks([self.preview_dock], [300], Qt.Orientation.Horizontal)
                self.resizeDocks([self.timeline_dock, self.dopesheet_dock], [40, 200], Qt.Orientation.Vertical)
        self._layout_mode = mode
        accent = "#7a2a24" if mode == "animate" else "#2e4d7a"
        self.statusBar().setStyleSheet(f"QStatusBar {{ background: {accent}; color: white; }}")
        self.mode_bar.setStyleSheet(f"QToolBar {{ background: {accent}; border: none; }}")

    def reset_layout(self) -> None:
        """Forget the saved arrangement of both modes and lay the windows out as at first start."""
        for mode in ("setup", "animate"):
            self.store.remove(f"layout3/{mode}")
        for dock in self.findChildren(QDockWidget):
            dock.setFloating(False)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.tree_dock)
        self.splitDockWidget(self.tree_dock, self.properties_dock, Qt.Orientation.Vertical)
        for dock in (self.preview_dock, self.weights_dock):
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timeline_dock)
        self.splitDockWidget(self.timeline_dock, self.dopesheet_dock, Qt.Orientation.Vertical)
        self.tabifyDockWidget(self.dopesheet_dock, self.graph_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.events_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.history_dock)
        self.weights_dock.hide()
        self.events_dock.hide()
        self.history_dock.hide()
        mode, self._layout_mode = self._layout_mode, None
        self.store.remove(f"layout3/{mode}")
        self._apply_mode_layout()

    def _tool_changed(self) -> None:
        """The Weights tool opens the Weights view, like Spine."""
        if self.state.tool == "weights" and not self.weights_dock.isVisible():
            self.weights_dock.show()
            self.weights_dock.raise_()

    def toggle_mode(self) -> None:
        self.state.set_mode("setup" if self.state.mode == "animate" else "animate")

    @staticmethod
    def _scrolled(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumSize(80, 30)
        return scroll

    def _dock(self, title: str, widget, area) -> QDockWidget:
        """Every window can be moved to any side, stacked as tabs, floated or closed (View menu)."""
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable |
                         QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    @property
    def doc(self) -> Document:
        return self.state.doc

    @property
    def library(self) -> PresetLibrary:
        if self._library is None:
            self._library = PresetLibrary.load(user_root=default_user_library_dir())
        return self._library

    # --- menus ----------------------------------------------------------------------------

    def _action(self, menu, text: str, func, shortcut=None, tip: str = "") -> QAction:
        action = QAction(text, self)
        if isinstance(shortcut, QKeySequence.StandardKey):
            shortcut = QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.PortableText)
        command = " ".join(w for w in text.replace("…", "").replace("&", "").split() if w.isascii())
        self.shortcuts.add(command.strip(), action, shortcut or "")
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(lambda: self._guard(func))
        menu.addAction(action)
        return action

    def _guard(self, func) -> None:
        try:
            func()
        except EditorError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))

    def _build_menus(self) -> None:
        self.shortcuts = ShortcutRegistry(self.store)
        self.timeline.graph_requested.connect(lambda: (self.graph_dock.show(), self.graph_dock.raise_()))
        bar = self.menuBar()
        f = bar.addMenu("&File")  # laid out like Spine's File menu
        self._action(f, "New Project", self.new_project, "Ctrl+Shift+N")
        self._action(f, "Open Project…", self.open_project, QKeySequence.StandardKey.Open)
        self.recent_menu = f.addMenu("Recent Projects")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._action(f, "Save Project", self.save, QKeySequence.StandardKey.Save)
        self._action(f, "Save Project &As…", self.save_as, "Ctrl+Shift+S")
        f.addSeparator()
        self._action(f, "New Skeleton", self.new_skeleton, "Ctrl+N",
                     "Start an empty skeleton that uses the same images folder")
        self.skeletons_menu = f.addMenu("Skeletons in project")
        self.skeletons_menu.aboutToShow.connect(self._fill_skeletons_menu)
        f.addSeparator()
        self._action(f, "Import Project…", self.import_spine,
                     tip="Open a Spine project (.spine, 3.8 or 4.2) or a Spine JSON export as a new project")
        self._action(f, "Import Data…", self.import_data,
                     tip="Add another Spine JSON's bones, slots, skins and animations to this skeleton")
        self._action(f, "Import PSD / OpenRaster…", self.import_psd_file,
                     tip="Add a Photoshop (.psd) or Krita/GIMP OpenRaster (.ora) file's layers as slots, placed "
                         "where they sit; Anim2D follows the file and updates the images when it's saved")
        self._action(f, "Sync layered images now", self.sync_layers,
                     tip="Read every imported .psd/.ora file again and update changed layers")
        self._action(f, "&Export…", self.export_spine, "Ctrl+E",
                     "JSON, binary, GIF, PNG, APNG, PSD, JPEG, WebP, AVI, MOV or MP4")
        self._action(f, "Unity Bridge…", self.unity_bridge,
                     tip="Export straight into a Unity project for spine-unity, optionally on every save")
        self._action(f, "Export to Unity", self.export_to_unity, "Ctrl+Alt+E",
                     "Export into the Unity project set in Unity Bridge")
        f.addSeparator()
        self._action(f, "Texture Packer…", self.texture_packer, "Ctrl+P")
        self._action(f, "Texture Unpacker…", self.texture_unpacker)
        f.addSeparator()
        self._action(f, "Settings…", self.open_settings, "F12")
        self._action(f, "Set Images Folder…", self.set_images_folder)
        f.addSeparator()
        self._action(f, "E&xit", self.close, QKeySequence.StandardKey.Quit)

        e = bar.addMenu("&Edit")
        self.undo_action = self._action(e, "Undo", self.doc_undo, QKeySequence.StandardKey.Undo)
        self.redo_action = self._action(e, "Redo", self.doc_redo, "Ctrl+Y")
        e.addSeparator()
        self._action(e, "Delete selected", self.delete_selected, QKeySequence.StandardKey.Delete)
        self._action(e, "Key selected bone / IK", self.timeline._key, "K")
        self._action(e, "Add IK constraint to selected bone…", self.add_ik, "Ctrl+K")
        self._action(e, "New animation…", self.new_animation)
        e.addSeparator()
        self._action(e, "Copy pose", self.copy_pose, "Ctrl+Shift+C", "Copy the selected bone's pose and the bones below it")
        self._action(e, "Paste pose", lambda: self.paste_pose(False), "Ctrl+Shift+V",
                     "Animate mode: key the copied pose here. Setup mode: set the setup pose")
        self._action(e, "Paste pose mirrored", lambda: self.paste_pose(True), "Ctrl+Alt+V",
                     "Paste onto the opposite side's bones (L_ <-> R_, _l <-> _r, Left <-> Right)")
        self._action(e, "Deselect", self.deselect, "Space", "Clear the selection (bones, slots, attachments, "
                     "vertices and any other item)")
        self._action(e, "Bake constraints to keys", self.bake_animation,
                     tip="Key every frame of the current animation with IK/transform/path applied, then turn "
                         "those constraints off in it")
        self._action(e, "Retime…", self.retime_animation, "Ctrl+Alt+T",
                     "Scale time, reverse, or loop a section of the current animation or the selected keys")
        self._action(e, "Bake physics to keys", self.bake_physics_keys,
                     tip="Key the physics (springy bones) into the current animation's rotations, so it "
                         "plays on Spine 3.8/4.0 runtimes")
        self._action(e, "Simplify keys", self.simplify_animation,
                     tip="Remove keys the current animation can do without (within 0.1° / 0.1 px)")
        self._action(e, "Mirror bones…", self.mirror_selected, tip="Copy the selected bone chain, its slots and "
                     "images, mirrored left to right")
        e.addSeparator()
        self._action(e, "Keyboard Shortcuts…", self.edit_shortcuts, "Ctrl+Alt+K", "Change the key of any command")

        v = bar.addMenu("&View")
        self._action(v, "Frame all", self.viewport.frame_all, "F")
        bones = self._action(v, "Show bones", self._toggle_bones)
        bones.setCheckable(True)
        bones.setChecked(True)
        images = self._action(v, "Show images", self._toggle_images)
        images.setCheckable(True)
        images.setChecked(True)
        self._action(v, "Reference footage…", self.reference_footage, "Ctrl+Alt+R",
                     "A video or image sequence behind the viewport to animate over (rotoscoping)")
        motion = self._action(v, "Motion paths", self._toggle_motion_paths, "Ctrl+Alt+M",
                              "Animate mode: draw the selected bone's path, a dot per frame; drag a dot to move "
                              "the bone at that frame")
        motion.setCheckable(True)
        self.motion_paths_action = motion
        self.grid_menu = v.addMenu("Grid")
        self._build_grid_menu()
        self._action(v, "Appearance…", self.appearance, tip="Interface theme, viewport background and grid colours")
        v.addSeparator()
        for dock, text in ((self.timeline_dock, "Timeline"), (self.dopesheet_dock, "Dopesheet"),
                           (self.graph_dock, "Graph"), (self.events_dock, "Events"),
                           (self.history_dock, "History")):
            action = dock.toggleViewAction()
            action.setText(text)
            v.addAction(action)
        self._action(v, "Reset window &layout", self.reset_layout, tip="Put every window back in its place")
        weights = self.weights_dock.toggleViewAction()
        weights.setText("Weights")
        v.addAction(weights)
        preview = self.preview_dock.toggleViewAction()
        preview.setText("Preview")
        preview.setStatusTip("A second viewport that plays animations and crossfades on its own")
        self.shortcuts.add("Preview window", preview, "Ctrl+Shift+P")
        v.addAction(preview)

        fx = bar.addMenu("F&X")
        self._action(fx, "Insert FX into skeleton…", self.insert_fx, "Ctrl+I",
                     "Add a preset or Spine template effect to the selected bone and current animation")
        self._action(fx, "Import FX template from Spine export…", self.import_template)
        self._action(fx, "Open FX Builder", self.open_fx_builder, tip="Generate FX packages from presets in "
                     "the FX Builder window")

        h = bar.addMenu("&Help")
        self._action(h, "Search commands…", self.search_commands, "Ctrl+Space",
                     "Find any command by typing part of its name, then press Enter")
        self._action(h, "Shortcuts", self.show_shortcuts)
        h.addSeparator()
        self._action(h, "Check for Updates…", lambda: self.updates.check(manual=True),
                     tip="See whether a newer version of Anim2D is out")
        self._action(h, "Report a Bug…", self.report_bug, tip="Open a bug report on GitHub")
        self._action(h, f"About {APP_NAME}", lambda: self.open_settings("About"))

        self.mode_bar = QToolBar("Mode")
        self.mode_bar.setObjectName("Mode")
        self.mode_bar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.mode_bar)
        self.mode_switch = ModeSwitch(self.state)
        self.mode_bar.addWidget(self.mode_switch)
        self._action(v, "Switch Setup / Animate", self.toggle_mode, "Ctrl+Tab")

        for tool, label, key in TOOLS:  # keyboard shortcuts for the tools
            action = QAction(label, self)
            self.shortcuts.add(f"Tool: {label}", action, key)
            action.triggered.connect(lambda _=False, t=tool: self.state.set_tool(t))
            self.addAction(action)
        fx_bar = QToolBar("FX")
        fx_bar.setObjectName("FX")
        self.addToolBar(fx_bar)
        self._action(fx_bar, "✨ Insert FX", self.insert_fx)
        for top in bar.actions():  # last, once every command is in its menu
            add_access_keys(top.menu())

    def _selected_bone(self) -> str:
        sel = self.state.selection
        if sel.kind == "bone":
            return sel.name
        if sel.kind in ("slot", "attachment"):
            return self.doc.slot(sel.name if sel.kind == "slot" else sel.owner)["bone"]
        raise EditorError("Select a bone first")

    def copy_pose(self) -> None:
        from .mirror import _subtree, copy_pose
        bone = self._selected_bone()
        self.state.pose_clipboard = copy_pose(self.state, _subtree(self.doc, bone))
        self.status_label.setText(f"Copied the pose of {len(self.state.pose_clipboard)} bone(s) from {bone}")

    def paste_pose(self, mirrored: bool) -> None:
        from .mirror import paste_pose
        if not getattr(self.state, "pose_clipboard", None):
            raise EditorError("Copy a pose first (Edit > Copy pose)")
        changed = paste_pose(self.state, self.state.pose_clipboard, mirrored)
        where = f"keyed at {self.state.time:.3f}s" if self.state.animating else "set as the setup pose"
        self.status_label.setText(f"Pasted{' mirrored' if mirrored else ''} onto {len(changed)} bone(s), {where}")

    def deselect(self) -> None:
        if self.state.vertex_selection:
            self.state.set_vertex_selection(set())
        self.state.select(None)
        sheet = self.timeline.sheet  # selected keys in the Dopesheet / Graph
        if sheet.selected:
            sheet.selected = set()
            sheet.update()
            self.timeline.graph.update()
        self.status_label.setText("Nothing selected")

    def bake_animation(self) -> None:
        from .bake import bake
        if not self.state.animation:
            raise EditorError("Choose an animation first")
        sel = self.state.selection
        bones = None
        if sel.kind == "bone":
            from .mirror import _subtree
            bones = _subtree(self.doc, sel.name)
        result = bake(self.doc, self.state.animation, bones)
        self.status_label.setText(f"Baked {len(result['bones'])} bone(s) over {result['frames']} frames; "
                                  f"constraints off in this animation: {', '.join(result['constraints']) or 'none'}")

    def retime_animation(self) -> None:
        from .retime_dialog import RetimeDialog
        if not self.state.animation:
            raise EditorError("Choose an animation first")
        selected = sorted(self.timeline.sheet.selected, key=lambda r: r.time)
        dialog = RetimeDialog(self.doc, self.state.animation, selected, self.state.time, self)
        if dialog.exec():
            self.timeline.sheet.selected = set()
            self.status_label.setText(dialog.apply())

    def bake_physics_keys(self) -> None:
        from .physics import bake_physics
        if not self.state.animation:
            raise EditorError("Choose an animation first")
        count = bake_physics(self.doc, self.state.animation)
        self.status_label.setText(f"Baked physics of {count} bone(s) into {self.state.animation}; set their "
                                  f"mix to 0 (bone menu > Physics…) so the Preview doesn't apply it twice")

    def simplify_animation(self) -> None:
        from .bake import simplify
        if not self.state.animation:
            raise EditorError("Choose an animation first")
        removed = simplify(self.doc, self.state.animation)
        self.status_label.setText(f"Simplified '{self.state.animation}': {removed} key(s) removed")

    def mirror_selected(self) -> None:
        from .mirror import mirror_bones
        bone = self._selected_bone()
        axis, ok = QInputDialog.getDouble(self, "Mirror bones", "Mirror across the vertical line x =", 0.0,
                                          -1e6, 1e6, 2)
        if ok:
            names = mirror_bones(self.doc, bone, axis)
            self.state.select("bone", names[bone])
            self.status_label.setText(f"Mirrored {len(names)} bone(s): {bone} -> {names[bone]}")

    def edit_shortcuts(self) -> None:
        if ShortcutsDialog(self.shortcuts, self).exec() == ShortcutsDialog.DialogCode.Accepted:
            self.status_label.setText("Keyboard shortcuts saved")

    def show_shortcuts(self) -> None:
        lines = [f"{self.shortcuts.key(c)}  —  {c}" for c in sorted(self.shortcuts.entries, key=str.lower)
                 if self.shortcuts.key(c)]
        QMessageBox.information(self, "Shortcuts", "\n".join(lines) + "\n\nRight/middle drag: pan · wheel: zoom · "
                                "drag a gizmo handle to change only that axis.\nEdit > Keyboard Shortcuts changes any key.")

    def _toggle_bones(self) -> None:
        self.viewport.show_bones = not self.viewport.show_bones
        self.viewport.update()

    def _toggle_images(self) -> None:
        self.viewport.show_images = not self.viewport.show_images
        self.viewport.update()

    # --- state ----------------------------------------------------------------------------

    def _doc_changed(self) -> None:
        doc = self.doc
        name = doc.path.name if doc.path else "untitled"
        if len(doc.skeleton_names()) > 1:
            name += f" [{doc.skeleton_name}]"
        self.setWindowTitle(f"{APP_NAME}{title_suffix()} - {name}{' *' if doc.dirty else ''}")
        self.undo_action.setText(f"&Undo {doc.undo_label()}".strip())
        self.undo_action.setEnabled(doc.can_undo)
        self.redo_action.setText(f"&Redo {doc.redo_label()}".strip())
        self.redo_action.setEnabled(doc.can_redo)
        errors, warnings = doc.validate()
        if errors:
            self.validation_label.setText(f"✖ {len(errors)} problem(s): {errors[0]}")
            self.validation_label.setStyleSheet("color: #ff6b6b")
        else:
            self.validation_label.setText(f"✔ valid · {len(doc.bones)} bones · {len(doc.slots)} slots")
            self.validation_label.setStyleSheet("color: #8ee08e")

    def doc_undo(self) -> None:
        self.doc.undo()

    def doc_redo(self) -> None:
        self.doc.redo()

    def delete_selected(self) -> None:
        if self.timeline.sheet.selected and self.timeline.sheet.hasFocus():
            self.timeline.sheet.delete_selected()
            return
        sel = self.state.selection
        mesh = self.state.active_mesh()
        if mesh is not None and self.state.vertex_selection and not self.state.animating:
            from . import mesh_ops
            mesh_ops.remove_vertices(self.doc, *mesh, set(self.state.vertex_selection))
            self.state.set_vertex_selection(set())
            return
        shape = self.state.active_shape()
        if shape is not None and self.state.vertex_selection and not self.state.animating:
            slot, name, kind = shape
            if kind == "path":
                self.doc.remove_path_points(slot, name, {i // 3 for i in self.state.vertex_selection})
            elif kind != "point":
                self.doc.remove_polygon_vertices(slot, name, set(self.state.vertex_selection))
            self.state.set_vertex_selection(set())
            return
        if sel.kind == "bone":
            self.doc.delete_bone(sel.name)
        elif sel.kind == "slot":
            self.doc.delete_slot(sel.name)
        elif sel.kind == "attachment":
            self.doc.delete_attachment(sel.owner, sel.name)
        elif sel.kind == "animation":
            self.doc.delete_animation(sel.name)
        elif sel.kind == "ik":
            self.doc.delete_ik(sel.name)
        elif sel.kind in ("transform", "path"):
            self.doc.delete_constraint(sel.name)

    def add_ik(self) -> None:
        sel = self.state.selection
        if sel.kind != "bone" or sel.name == self.doc.root_name:
            raise EditorError("Select the bone at the end of the chain (for example the shin or forearm) first")
        self.tree.add_ik(sel.name)

    def new_animation(self) -> None:
        name, ok = QInputDialog.getText(self, "New animation", "Name")
        if ok and name.strip():
            self.state.set_animation(self.doc.add_animation(name.strip()))
            self.state.set_mode("animate")

    # --- files ----------------------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        answer = QMessageBox.question(self, APP_NAME, "Save changes to the current project?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _start_dir(self) -> str:
        """The folder file dialogs open in: the last one used, at first Documents/Anim2D/Projects."""
        saved = self.store.value("last_dir")
        if saved and Path(str(saved)).is_dir():
            return str(saved)
        from .. import paths
        try:
            paths.projects_dir().mkdir(parents=True, exist_ok=True)
        except OSError:
            return str(Path.home())
        return str(paths.projects_dir())

    def _remember_dir(self, path: Path) -> None:
        self.store.setValue("last_dir", str(Path(path).parent))

    def set_document(self, doc: Document) -> None:
        self.state.set_document(doc)
        self.preview.set_document(doc)
        self._doc_changed()
        self.viewport.frame_all()
        self._watch_layer_files()
        self.load_reference()

    def _new_document(self, images_dir: Path | None = None) -> Document:
        doc = Document(images_dir=images_dir)
        doc.data["skeleton"]["fps"] = self.settings.default_fps
        return doc

    def new_project(self) -> None:
        if self._confirm_discard():
            self.set_document(self._new_document())

    def _fill_skeletons_menu(self) -> None:
        m = self.skeletons_menu
        m.clear()
        for name in self.doc.skeleton_names():
            action = m.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == self.doc.skeleton_name)
            action.triggered.connect(lambda _=False, n=name: self._guard(lambda: self.switch_skeleton(n)))
        m.addSeparator()
        for text, func in (("Add skeleton…", self.add_skeleton), ("Duplicate this skeleton…", self.duplicate_skeleton),
                           ("Rename this skeleton…", self.rename_skeleton), ("Delete this skeleton", self.delete_skeleton)):
            m.addAction(text).triggered.connect(lambda _=False, f=func: self._guard(f))

    def switch_skeleton(self, name: str) -> None:
        self.doc.switch_skeleton(name)
        self.set_document(self.doc)  # fresh selection, animation and views for the other skeleton
        self.status_label.setText(f"Editing skeleton {name}")

    def _ask_skeleton_name(self, title: str, start: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, "Skeleton name", text=start)
        return name.strip() if ok and name.strip() else None

    def add_skeleton(self) -> None:
        name = self._ask_skeleton_name("Add skeleton", f"skeleton{len(self.doc.skeleton_names()) + 1}")
        if name:
            self.doc.add_skeleton(name, switch=False)
            self.switch_skeleton(name)

    def duplicate_skeleton(self) -> None:
        name = self._ask_skeleton_name("Duplicate skeleton", f"{self.doc.skeleton_name} copy")
        if name:
            self.doc.add_skeleton(name, self.doc.data, switch=False)
            self.switch_skeleton(name)

    def rename_skeleton(self) -> None:
        name = self._ask_skeleton_name("Rename skeleton", self.doc.skeleton_name)
        if name:
            self.doc.rename_skeleton(self.doc.skeleton_name, name)

    def delete_skeleton(self) -> None:
        name = self.doc.skeleton_name
        if QMessageBox.question(self, APP_NAME, f"Delete skeleton '{name}' from the project? This can't be undone.")                 != QMessageBox.StandardButton.Yes:
            return
        self.doc.delete_skeleton(name)
        self.set_document(self.doc)

    def new_skeleton(self) -> None:
        """An empty skeleton that keeps the images folder (not yet saved anywhere)."""
        if self._confirm_discard():
            self.set_document(self._new_document(self.doc.images_dir))
            self.status_label.setText("New skeleton" + (f" using {self.doc.images_dir}" if self.doc.images_dir
                                                         else ""))

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self._start_dir(), PROJECT_FILTER)
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        self._remember_dir(path)
        self.set_document(Document.load(path))
        self.add_recent(path)

    # Recent projects (File > Recent Projects), newest first.
    def recent_projects(self) -> list[str]:
        value = self.store.value("recent", [])
        items = [value] if isinstance(value, str) else list(value or [])
        return [i for i in items if i][: self.settings.recent_count]

    def add_recent(self, path: Path) -> None:
        text = str(Path(path).resolve())
        items = [text] + [i for i in self.recent_projects() if i != text]
        self.store.setValue("recent", items[: self.settings.recent_count])

    def clear_recent(self) -> None:
        self.store.setValue("recent", [])

    def _fill_recent(self) -> None:
        menu = self.recent_menu
        menu.clear()
        items = self.recent_projects()
        for item in items:
            action = menu.addAction(item)
            action.triggered.connect(lambda _=False, i=item: self._guard(lambda: self._open_recent(i)))
        if not items:
            menu.addAction("No recent projects").setEnabled(False)
        else:
            menu.addSeparator()
            menu.addAction("Clear recent projects", self.clear_recent)

    def _open_recent(self, item: str) -> None:
        if not Path(item).is_file():
            self.store.setValue("recent", [i for i in self.recent_projects() if i != item])
            raise EditorError(f"{item} no longer exists")
        if self._confirm_discard():
            self.open_path(Path(item))

    def save(self) -> bool:
        if self.doc.path is None:
            return self.save_as()
        self.doc.save()
        self.add_recent(self.doc.path)
        self._doc_changed()
        self.status_label.setText(f"Saved {self.doc.path}")
        self._export_on_save()
        return True

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", self._start_dir(), PROJECT_FILTER)
        if not path:
            return False
        path = Path(path).with_suffix(PROJECT_SUFFIX)
        self._remember_dir(path)
        self.doc.save(path)
        self.add_recent(path)
        self._doc_changed()
        self.status_label.setText(f"Saved {path}")
        self._export_on_save()
        return True

    def unity_bridge(self) -> None:
        from .unity_dialog import UnityDialog
        dialog = UnityDialog(self.doc, self)
        if dialog.exec() and dialog.exported:
            self.status_label.setText(f"Exported to Unity: {dialog.exported}")
        self._doc_changed()

    def export_to_unity(self) -> None:
        from .unity_bridge import UnitySettings, export_to_unity
        if not UnitySettings.of(self.doc).project:
            self.unity_bridge()
            return
        out, warnings = export_to_unity(self.doc)
        self.status_label.setText(f"Exported to Unity: {out}" + (f" · {len(warnings)} note(s)" if warnings else ""))

    def _export_on_save(self) -> None:
        from .unity_bridge import UnitySettings, export_to_unity
        s = UnitySettings.of(self.doc)
        if not (s.on_save and s.project):
            return
        try:
            out, _ = export_to_unity(self.doc, s)
            self.status_label.setText(f"Saved and exported to Unity: {out.name}")
        except EditorError as e:
            self.status_label.setText(f"Saved, but the Unity export failed: {e}")

    def set_images_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Images folder", str(self.doc.images_dir or self._start_dir()))
        if folder:
            self.doc.checkpoint("Set images folder")
            self.doc.images_dir = Path(folder)
            self.doc.notify()

    def import_spine(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Project", self._start_dir(),
            "Spine projects and exports (*.spine *.json);;Spine project, 3.8.x or 4.2.x (*.spine);;Spine JSON (*.json)")
        if not path:
            return
        self._remember_dir(Path(path))
        if Path(path).suffix.lower() == ".spine":
            doc, warnings = Document.import_spine_project(Path(path))
        else:
            doc, warnings = Document.import_spine_json(Path(path))
        self.set_document(doc)
        self.status_label.setText(f"Imported {Path(path).name}" + (f" · {' '.join(warnings)}" if warnings else ""))

    def import_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Data", self._start_dir(),
                                              "Spine projects and exports (*.spine *.json)")
        if not path:
            return
        self._remember_dir(Path(path))
        notes = self.doc.merge_spine_json(Path(path))
        self.status_label.setText(f"Imported data from {Path(path).name}")
        if notes:
            QMessageBox.information(self, APP_NAME, "Imported with notes:\n\n" + "\n".join(notes[:20]))

    def import_psd_file(self) -> None:
        from .psd_import import import_psd
        from .psd_import import LAYER_FILES
        path, _ = QFileDialog.getOpenFileName(self, "Import PSD / OpenRaster", self._start_dir(), LAYER_FILES)
        if not path:
            return
        self._remember_dir(Path(path))
        try:
            bone = self._selected_bone()
        except EditorError:
            bone = None  # the root
        try:
            result = import_psd(self.doc, Path(path), bone)
        except EditorError as e:
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        self.status_label.setText(f"Imported {len(result['slots'])} layers from {Path(path).name}; "
                                  f"saving it again updates them here")
        self._watch_layer_files()

    def _viewport_world_rect(self) -> QRectF:
        vp = self.viewport
        x0, y0 = vp.to_world(QPointF(0, 0))
        x1, y1 = vp.to_world(QPointF(vp.width(), vp.height()))
        return QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def export_context(self) -> ExportContext:
        name = self.doc.path.stem if self.doc.path else "skeleton"
        skin = self.doc.edit_skin if self.doc.edit_skin != "default" else None
        return ExportContext(self.doc, self.state.animation, skin, self.state.time, self._viewport_world_rect,
                             name, self._start_dir())

    def export_spine(self) -> None:
        """File > Export: data, images or video, in one dialog like Spine's."""
        dialog = ExportDialog(self.export_context(), self, self.store)
        if dialog.exec() == ExportDialog.DialogCode.Accepted and dialog.written:
            first = dialog.written[0]
            self._remember_dir(first)
            more = f" (+{len(dialog.written) - 1} more)" if len(dialog.written) > 1 else ""
            self.status_label.setText(f"Exported {first}{more}")

    def texture_packer(self) -> None:
        from .packer_dialogs import TexturePackDialog
        dialog = TexturePackDialog(self.store, self, str(self.doc.images_dir or ""))
        if dialog.exec() == TexturePackDialog.DialogCode.Accepted:
            self.status_label.setText(f"Packed {dialog.written[0]}" if dialog.written else "Nothing packed")

    def texture_unpacker(self) -> None:
        from .packer_dialogs import TextureUnpackDialog
        dialog = TextureUnpackDialog(self.store, self)
        if dialog.exec() == TextureUnpackDialog.DialogCode.Accepted:
            self.status_label.setText(f"Unpacked {len(dialog.written)} image(s)")

    def open_settings(self, page: str = "General") -> None:
        dialog = SettingsDialog(self.settings, self, self.clear_recent, window=self, page=page)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.settings = dialog.settings()
            self.settings.save(self.store)
            self._apply_settings()
        dialog.deleteLater()  # it's the window's child: don't keep it (and its update signals) around

    def _build_grid_menu(self) -> None:
        """View > Grid: five styles, a custom colour, or off. Remembered between sessions."""
        from PySide6.QtGui import QActionGroup
        from ..preview.grid import CHOICES, CUSTOM
        group = QActionGroup(self)
        self.grid_actions = {}
        for name in CHOICES:
            action = self.grid_menu.addAction(f"{name} colour…" if name == CUSTOM else name)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, n=name: self._guard(lambda: self.set_grid(n, ask=True)))
            self.grid_actions[name] = action
        style = str(self.store.value("grid_style", "Subtle"))
        custom = self.store.value("grid_color") or None
        self.set_grid(style if style in self.grid_actions else "Subtle", custom=custom)
        from ..preview.grid import BACKGROUND_CHOICES
        name = str(self.store.value("viewport_background", "Default"))
        self.set_background(name if name in BACKGROUND_CHOICES else "Default",
                            self.store.value("viewport_background_color") or None)
        from .theme import THEMES, DEFAULT_THEME
        theme = str(self.store.value("theme", DEFAULT_THEME))
        self.theme_name = theme if theme in THEMES else DEFAULT_THEME

    # --- reference footage -------------------------------------------------------------------
    def reference_footage(self) -> None:
        from .reference_dialog import ReferenceDialog
        if getattr(self, "reference_dialog", None) is None:
            self.reference_dialog = ReferenceDialog(self)
        self.reference_dialog.load_values()
        self.reference_dialog.show_info()
        self.reference_dialog.show()
        self.reference_dialog.raise_()

    def load_reference(self, fit: bool = False) -> None:
        """(Re)load the project's reference footage; fit: centre and scale it once it has loaded."""
        from .reference import ReferenceMedia, ReferenceSettings
        if getattr(self, "reference_media", None) is None:
            self.reference_media = ReferenceMedia(self)
            self.reference_media.loaded.connect(self._reference_loaded)
            self.reference_media.failed.connect(self.status_label.setText)
            self.viewport.reference = self.reference_media
        s = ReferenceSettings.of(self.doc)
        self._fit_reference_on_load = fit
        if not s.path:
            self.reference_media.clear()
            return
        if not Path(s.path).exists():
            self.reference_media.clear()
            self.status_label.setText(f"Reference footage not found: {s.path}")
            return
        self.reference_media.load(Path(s.path), self.doc.fps)

    def _reference_loaded(self) -> None:
        if getattr(self, "_fit_reference_on_load", False):
            self._fit_reference_on_load = False
            self.fit_reference()
        self.viewport.update()

    def fit_reference(self) -> None:
        """Centre the footage in the viewport and scale it to fit."""
        from .reference import ReferenceSettings
        media = self.reference_media
        if media.size.isEmpty():
            return
        s = ReferenceSettings.of(self.doc)
        vp = self.viewport
        (x0, y0), (x1, y1) = vp.to_world(QPointF(0, 0)), vp.to_world(QPointF(vp.width(), vp.height()))
        s.x, s.y = round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)
        s.scale = round(min(abs(x1 - x0) / media.size.width(), abs(y1 - y0) / media.size.height()) * 0.95, 4)
        s.store(self.doc)
        vp.update()

    def _toggle_motion_paths(self) -> None:
        self.state.motion_paths = self.motion_paths_action.isChecked()
        self.state.view_changed.emit()

    # --- live layer sync ---------------------------------------------------------------------
    def _watch_layer_files(self) -> None:
        """Follow the imported .psd/.ora files; saving one in Photoshop/Krita/GIMP updates the images."""
        from PySide6.QtCore import QFileSystemWatcher
        from .psd_import import layer_sources
        if not hasattr(self, "layer_watcher"):
            self.layer_watcher = QFileSystemWatcher(self)
            self.layer_watcher.fileChanged.connect(self._layer_file_changed)
            self._layer_timer = QTimer(self)
            self._layer_timer.setSingleShot(True)
            self._layer_timer.setInterval(600)  # editors write in several steps: wait until it settles
            self._layer_timer.timeout.connect(self._sync_changed_layers)
            self._changed_layer_files: set[str] = set()
        watched = set(self.layer_watcher.files())
        wanted = {str(p) for p in layer_sources(self.doc) if p.is_file()}
        if watched - wanted:
            self.layer_watcher.removePaths(list(watched - wanted))
        if wanted - watched:
            self.layer_watcher.addPaths(list(wanted - watched))

    def _layer_file_changed(self, path: str) -> None:
        self._changed_layer_files.add(path)
        self._layer_timer.start()

    def _sync_changed_layers(self) -> None:
        paths, self._changed_layer_files = self._changed_layer_files, set()
        self.sync_layers([Path(p) for p in paths], quiet=True)
        self._watch_layer_files()  # editors that save by replacing the file drop it from the watcher

    def sync_layers(self, paths: list[Path] | None = None, quiet: bool = False) -> None:
        from .psd_import import layer_sources, resync_layers
        paths = paths if paths is not None else layer_sources(self.doc)
        if not paths:
            if not quiet:
                raise EditorError("No .psd or .ora file has been imported into this project")
            return
        notes = []
        for path in paths:
            if not path.is_file():
                notes.append(f"{path.name}: not found")
                continue
            try:
                r = resync_layers(self.doc, path)
            except EditorError as exc:
                notes.append(str(exc))
                continue
            text = f"{path.name}: {len(r['updated'])} updated"
            if r["added"]:
                text += f", {len(r['added'])} new"
            if r["missing"]:
                text += f", {len(r['missing'])} no longer in the file ({', '.join(r['missing'][:4])})"
            notes.append(text)
        self.viewport.update()
        self.status_label.setText("Layers synced · " + " · ".join(notes))

    def search_commands(self) -> None:
        from .command_search import CommandSearch
        dialog = CommandSearch(self.menuBar(), self)
        dialog.exec()
        if dialog.ran:
            self.status_label.setText(f"Ran: {dialog.ran}")

    def appearance(self) -> None:
        self.open_settings("Appearance")

    def report_bug(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..resources import HOMEPAGE
        QDesktopServices.openUrl(QUrl(f"{HOMEPAGE}/issues/new/choose"))

    def _update_available(self, release) -> None:
        self.update_button.setText(f"⬆ Update to {release.version}")
        self.update_button.show()

    def _updated_note(self) -> QPushButton | None:
        """'Updated to x.y.z · What's new' in the status bar, the first time a newer version starts."""
        from .. import updates
        from ..resources import HOMEPAGE, VERSION
        last = str(self.store.value("last_version", "") or "")
        self.store.setValue("last_version", VERSION)
        if not last or not updates.is_newer(VERSION, last):
            return None
        button = QPushButton(f"✓ Updated to {VERSION} · What's new")
        button.setFlat(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"Anim2D was updated from {last}. Click for the release notes.")

        def open_notes() -> None:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(f"{HOMEPAGE}/releases/tag/v{VERSION}"))
            button.hide()
        button.clicked.connect(open_notes)
        self.statusBar().addPermanentWidget(button)
        QTimer.singleShot(120_000, button, button.hide)  # a note, not a fixture
        return button

    def _show_update(self) -> None:
        from .update_ui import UpdateDialog
        if self.updates.release is not None:
            dialog = UpdateDialog(self.updates, self.updates.release, self)
            dialog.exec()
            dialog.deleteLater()

    def set_theme(self, name: str) -> None:
        """Interface theme for the whole app; remembered (the FX Preset Builder uses it too)."""
        from PySide6.QtWidgets import QApplication
        from .theme import apply_theme
        app = QApplication.instance()
        self.theme_name = apply_theme(app, name) if app else name
        self.store.setValue("theme", self.theme_name)

    def set_background(self, name: str, custom: str | None = None) -> None:
        for state in (self.state, self.preview.state):
            state.set_background(name, custom)
        self.store.setValue("viewport_background", name)
        if self.state.background.get("custom"):
            self.store.setValue("viewport_background_color", self.state.background["custom"])

    def set_grid(self, style: str, custom: str | None = None, ask: bool = False) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        from ..preview.grid import CUSTOM
        if style == CUSTOM and ask:
            start = QColor(self.state.grid.get("custom") or "#4f8fe0")
            color = QColorDialog.getColor(start, self, "Grid colour")
            if not color.isValid():
                self.grid_actions[self.state.grid["style"]].setChecked(True)
                return
            custom = color.name()
        for state in (self.state, self.preview.state):
            state.set_grid(style, custom)
        self.grid_actions[style].setChecked(True)
        self.store.setValue("grid_style", style)
        if self.state.grid.get("custom"):
            self.store.setValue("grid_color", self.state.grid["custom"])

    def _apply_settings(self) -> None:
        self.settings.apply()
        if self.settings.autosave_minutes > 0:
            self.autosave_timer.start(self.settings.autosave_minutes * 60_000)
        else:
            self.autosave_timer.stop()

    def autosave(self) -> Path | None:
        """Write <project>.anim2d.autosave when a saved project has unsaved changes."""
        if self.doc.path is None or not self.doc.dirty:
            return None
        target = self.doc.path.with_name(self.doc.path.name + ".autosave")
        try:
            self.doc.save_copy(target)
        except OSError as exc:
            self.status_label.setText(f"Autosave failed: {exc}")
            return None
        self.status_label.setText(f"Autosaved {target.name}")
        return target

    # --- FX -------------------------------------------------------------------------------

    def insert_fx(self) -> None:
        sel = self.state.selection
        bone = sel.name if sel.kind == "bone" else self.doc.root_name
        dialog = InsertFxDialog(self.doc, self.library, bone, self.state.animation, self.state.time, self)
        if dialog.exec() == InsertFxDialog.DialogCode.Accepted and dialog.result:
            self._after_insert(dialog.result)

    def _after_insert(self, result) -> None:
        self.state.set_animation(result.animation)
        self.state.set_mode("animate")
        self.state.select("bone", f"{result.prefix}_root")
        msg = (f"Inserted {result.prefix}: {result.bones} bones, {result.slots} slots, keys "
               f"{result.start:.3f}s–{result.end:.3f}s in '{result.animation}'")
        self.status_label.setText(msg + (f" · {result.warnings[0]}" if result.warnings else ""))

    def import_template(self) -> None:
        dialog = TemplateImportDialog(self.library, self.library.user_root or default_user_library_dir(),
                                      parent=self)
        if dialog.exec() == TemplateImportDialog.DialogCode.Accepted and dialog.registered_id:
            self._library = PresetLibrary.load(self.library.root, self.library.user_root or default_user_library_dir())
            self.status_label.setText(f"Template '{dialog.registered_id}' is ready in FX > Insert FX")

    def open_fx_builder(self) -> None:
        from ..ui.main_window import MainWindow
        self._builder = MainWindow(self.library)
        self._builder.show()

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.store.setValue("geometry", self.saveGeometry())
        self.store.setValue(f"layout3/{self.state.mode}", self.saveState())
        super().closeEvent(event)
