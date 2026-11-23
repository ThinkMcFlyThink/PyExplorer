"""
StatusBar - item/selection counts on the left, background jobs (progress +
cancel) in the middle, free space and a zoom slider on the right.
It only listens to events; nothing calls it directly.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSlider, QStatusBar, QToolButton, QWidget

from ..core.events import Topics
from ..core.formatting import human_size
from .views.registry import view_class


class StatusBar(QStatusBar):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setSizeGripEnabled(True)
        self.info = QLabel("", self)
        self.addWidget(self.info)
        self.message = QLabel("", self, objectName="Dim")
        self.addWidget(self.message, 1)

        self.job_box = QWidget(self)
        jrow = QHBoxLayout(self.job_box)
        jrow.setContentsMargins(0, 0, 0, 0)
        self.job_label = QLabel("", self.job_box)
        self.job_bar = QProgressBar(self.job_box, minimum=0, maximum=1000, textVisible=False)
        self.job_bar.setFixedWidth(160)
        self.job_cancel = QToolButton(self.job_box, text="Cancel", autoRaise=True)
        self.job_cancel.clicked.connect(self._cancel_job)
        for w in (self.job_label, self.job_bar, self.job_cancel):
            jrow.addWidget(w)
        self.addPermanentWidget(self.job_box)
        self.job_box.hide()

        self.space = QLabel("", self, objectName="Dim")
        self.addPermanentWidget(self.space)
        self.zoom = QSlider(Qt.Orientation.Horizontal, self)
        self.zoom.setFixedWidth(110)
        self.zoom.setToolTip("Zoom (Ctrl + mouse wheel)")
        self.zoom.valueChanged.connect(self._on_zoom_slider)
        self.addPermanentWidget(self.zoom)

        self._job = None
        self._space_for = None
        self._message_timer = QTimer(self, singleShot=True, interval=6000, timeout=lambda: self.message.setText(""))
        bus = ctx.bus
        self._unsubs = [
            bus.subscribe(Topics.SELECTION_CHANGED, self._on_selection),
            bus.subscribe(Topics.ACTIVE_LOCATION_CHANGED, self._on_location),
            bus.subscribe(Topics.JOBS_CHANGED, self._on_job),
            bus.subscribe(Topics.STATUS_MESSAGE, self.show_message),
            bus.subscribe(Topics.ZOOM_CHANGED, lambda mode, level: self.sync_zoom()),
            bus.subscribe(Topics.VIEW_MODE_CHANGED, lambda mode: self.sync_zoom()),
        ]
        self.destroyed.connect(lambda *_: [u() for u in self._unsubs])
        self.sync_zoom()

    # -- selection / location ------------------------------------------------
    def _on_selection(self, tab=None) -> None:
        if tab is not None and tab is self.ctx.commands.active_tab():
            self.info.setText(tab.summary())

    def _on_location(self, path: str = "", **_) -> None:
        self._space_for = path
        self.space.setText("")

        def done(usage) -> None:
            if usage and self._space_for == path:
                total, used, free = usage
                self.space.setText(f"{human_size(free)} free of {human_size(total)}")
        self.ctx.runner.submit(lambda: self.ctx.vfs.resolve(path).disk_usage(path), pool="misc",
                               on_done=done, on_error=lambda e: None)

    # -- messages --------------------------------------------------------------
    def show_message(self, text: str, level: str = "info", **_) -> None:
        color = self.ctx.theme.palette.get("error") if level == "error" else self.ctx.theme.palette.get("fg_dim")
        self.message.setStyleSheet(f"color: {color};")
        self.message.setText(text)
        self._message_timer.start()

    # -- jobs ------------------------------------------------------------------
    def _on_job(self, job) -> None:
        running = [j for j in self.ctx.jobs.jobs.values() if not j.finished]
        if not running:
            self._job = None
            self.job_box.hide()
            if job.finished:
                failed = job.error and job.error != "Cancelled by user"
                self.show_message(f"{job.title}: {job.error}" if job.error else f"{job.title} — done",
                                  "error" if failed else "info")
            return
        self._job = j = running[-1]
        extra = f" (+{len(running) - 1} more)" if len(running) > 1 else ""
        self.job_label.setText(f"{j.title}{extra}: {j.detail}"[:90])
        if j.progress is None:
            self.job_bar.setRange(0, 0)             # "busy" animation
        else:
            self.job_bar.setRange(0, 1000)
            self.job_bar.setValue(int(j.progress * 1000))
        self.job_box.show()

    def _cancel_job(self) -> None:
        if self._job is not None:
            self._job.token.cancel()

    # -- zoom slider -----------------------------------------------------------
    def sync_zoom(self) -> None:
        mode = self.ctx.settings.get("view.mode", "details")
        self.zoom.blockSignals(True)
        self.zoom.setRange(0, len(view_class(mode).ZOOM_SIZES) - 1)
        self.zoom.setValue(self.ctx.settings.get(f"view.zoom.{mode}", 1))
        self.zoom.blockSignals(False)

    def _on_zoom_slider(self, value: int) -> None:
        self.ctx.commands.set_zoom(value)
