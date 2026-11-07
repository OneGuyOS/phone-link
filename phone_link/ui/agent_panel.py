"""The agent chat panel and its provider settings dialog."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..agent import (
    DEFAULT_SYSTEM_PROMPT,
    AgentStore,
    ChatMessage,
    CliChat,
    HttpChat,
    ModelFetcher,
    Provider,
    adb_forward,
    agent_files_dir,
    cli_executable,
    load_store,
    probe,
    save_file_blocks,
    save_store,
)
from ..local_server import LocalServer
from ..phone_server import PhoneStarter
from .theme import ACCENT, SUCCESS


class ChatInput(QPlainTextEdit):
    """Enter sends; Shift+Enter inserts a newline."""

    sendRequested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MessageBubble(QFrame):
    """One chat bubble with optional muted tool lines."""

    def __init__(
        self,
        role: str,
        text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self._text = text
        self.setObjectName("AgentBubble")
        self.setProperty("role", role)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(5)

        caption = {
            "user": "You",
            "assistant": "Agent",
            "error": "Error",
            "system": "Note",
        }.get(role, role.title())
        self.role_label = QLabel(caption, self)
        self.role_label.setObjectName("AgentRole")
        layout.addWidget(self.role_label)

        self.text_label = QLabel(text, self)
        self.text_label.setObjectName("AgentText")
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        # Long unbroken tokens must not force the scroll area wider.
        self.text_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.text_label)

        self._tools_layout: QVBoxLayout | None = None

    def append_text(self, piece: str) -> None:
        self._text += piece
        self.text_label.setText(self._text)

    def set_text(self, text: str) -> None:
        self._text = text
        self.text_label.setText(text)

    def add_tool(self, line: str) -> None:
        if self._tools_layout is None:
            self._tools_layout = QVBoxLayout()
            self._tools_layout.setContentsMargins(0, 2, 0, 0)
            self._tools_layout.setSpacing(2)
            self.layout().addLayout(self._tools_layout)
        tool = QLabel(line, self)
        tool.setObjectName("AgentTool")
        tool.setTextFormat(Qt.TextFormat.PlainText)
        tool.setWordWrap(True)
        tool.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._tools_layout.addWidget(tool)


class AgentPanel(QWidget):
    """Right-panel chat area with provider switching and streaming."""

    def __init__(
        self,
        adb_path: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.adb_path = adb_path
        self._store: AgentStore = load_store()
        self._provider: Provider = self._initial_provider()
        self._messages: list[ChatMessage] = []
        self._chat: HttpChat | CliChat | None = None
        self._bubble: MessageBubble | None = None
        self._pending_local_send = False
        self._pending_phone_send = False
        self.local_server = LocalServer(self)
        self.local_server.ready.connect(self._local_ready)
        self.local_server.failed.connect(self._local_failed)
        self.phone_starter = PhoneStarter(adb_path, self)
        self.phone_starter.ready.connect(self._phone_ready)
        self.phone_starter.failed.connect(self._phone_failed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)
        title = QLabel("AGENT CHAT", self)
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)
        self.provider_combo = QComboBox(self)
        self.provider_combo.setObjectName("AgentProvider")
        self.provider_combo.setMinimumWidth(180)
        self.provider_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        header.addWidget(self.provider_combo)
        self.settings_button = QPushButton("⚙", self)
        self.settings_button.setObjectName("AgentIconButton")
        self.settings_button.setToolTip("Agent providers and API keys")
        self.settings_button.setFixedWidth(30)
        self.settings_button.clicked.connect(self._open_settings)
        header.addWidget(self.settings_button)
        self.new_button = QPushButton("New", self)
        self.new_button.setObjectName("AgentIconButton")
        self.new_button.setToolTip("Start a new chat")
        self.new_button.clicked.connect(self.new_chat)
        header.addWidget(self.new_button)
        self.files_button = QPushButton("Files", self)
        self.files_button.setObjectName("AgentIconButton")
        self.files_button.setToolTip("Open the folder where the agent saves files")
        self.files_button.clicked.connect(self._open_files)
        header.addWidget(self.files_button)
        root.addLayout(header)

        self.status = QLabel("", self)
        self.status.setObjectName("AgentStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("AgentScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._surface = QWidget()
        self._surface.setObjectName("AgentSurface")
        self._messages_layout = QVBoxLayout(self._surface)
        self._messages_layout.setContentsMargins(2, 2, 6, 4)
        self._messages_layout.setSpacing(9)
        self._messages_layout.addStretch(1)
        self.scroll.setWidget(self._surface)
        root.addWidget(self.scroll, 1)

        self.input = ChatInput(self)
        self.input.setObjectName("AgentComposer")
        self.input.setPlaceholderText(
            "Ask the agent…  Enter to send, Shift+Enter for a new line"
        )
        self.input.setFixedHeight(72)
        self.input.sendRequested.connect(self.send)
        root.addWidget(self.input)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(7)
        self.hint = QLabel("", self)
        self.hint.setObjectName("AgentHint")
        footer.addWidget(self.hint, 1)
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setObjectName("AgentStop")
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop)
        footer.addWidget(self.stop_button)
        self.send_button = QPushButton("Send", self)
        self.send_button.setObjectName("AgentSend")
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self.send)
        footer.addWidget(self.send_button)
        root.addLayout(footer)

        self._reload_providers()
        self._update_status()

    # -- providers ---------------------------------------------------------

    def _initial_provider(self) -> Provider:
        provider = self._store.active() if self._store.active_id else None
        if provider is not None:
            return provider
        provider = self._store.providers[0]
        # Prefer a reachable local server without making startup slow.
        for candidate in self._store.providers:
            if candidate.kind == "openai" and candidate.local:
                if probe(candidate, timeout=0.3):
                    return candidate
        return provider

    def _reload_providers(self) -> None:
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for provider in self._store.providers:
            label = provider.label
            tags = []
            if provider.local:
                tags.append("local")
            if tags:
                label = f"{label} · {tags[0]}"
            self.provider_combo.addItem(label, provider.id)
        index = self.provider_combo.findData(self._provider.id)
        if index < 0:
            index = 0
        self.provider_combo.setCurrentIndex(index)
        self.provider_combo.blockSignals(False)
        self._provider = self._store.by_id(
            self.provider_combo.currentData()
        ) or self._provider

    def _provider_changed(self) -> None:
        provider_id = self.provider_combo.currentData()
        provider = self._store.by_id(provider_id)
        if provider is None:
            return
        self._provider = provider
        self._store.active_id = provider.id
        self._save_active()
        self._update_status()

    def _save_active(self) -> None:
        try:
            save_store(self._store)
        except OSError:
            pass

    def _update_status(self) -> None:
        provider = self._provider
        local = provider.local or provider.base_url.startswith(
            ("http://127.0.0.1", "http://localhost")
        )
        if provider.kind == "openai":
            where = "Local" if local else "Remote"
            model = provider.model or "choose a model in settings"
            detail = f"{where} · {provider.label} · {model}"
            color = SUCCESS if local else ACCENT
        else:
            detail = f"CLI agent · {provider.label}"
            color = ACCENT
        self.status.setText(detail)
        self.status.setStyleSheet(f"color: {color};")
        self.hint.setText(
            "The selected endpoint receives this conversation."
            if provider.kind == "openai" and not local
            else ""
        )

    def _open_settings(self) -> None:
        dialog = AgentSettingsDialog(self._store, self.adb_path, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._store = load_store()
            active = self._store.by_id(dialog.active_id()) or self._store.providers[0]
            self._provider = active
            self._reload_providers()
            self._update_status()

    # -- conversation ------------------------------------------------------

    def _add_bubble(self, role: str, text: str = "") -> MessageBubble:
        bubble = MessageBubble(role, text, self._surface)
        self._messages_layout.insertWidget(self._messages_layout.count() - 1, bubble)
        self._scroll_later()
        return bubble

    def _scroll_later(self) -> None:
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def new_chat(self) -> None:
        self.stop()
        self._messages = []
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.input.clear()
        self.input.setFocus()

    def _focus_input(self) -> None:
        self.input.setFocus()

    def _open_files(self) -> None:
        folder = agent_files_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._add_bubble("error", f"Could not open the files folder: {exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _local_provider(self, provider: Provider) -> bool:
        return provider.kind == "openai" and provider.id == "zara-fast" and (
            provider.base_url.startswith(
                ("http://127.0.0.1", "http://localhost")
            )
        )

    def _phone_provider(self, provider: Provider) -> bool:
        return provider.kind == "openai" and provider.id == "phone" and (
            provider.base_url.startswith(
                ("http://127.0.0.1", "http://localhost")
            )
        )

    def _wait_for_local(self) -> None:
        self._pending_local_send = True
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.stop_button.setVisible(True)
        self.status.setText("Starting the local model…")
        self.local_server.ensure_started()

    def _local_ready(self) -> None:
        if not self._pending_local_send:
            return
        self._pending_local_send = False
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.stop_button.setVisible(False)
        self.send(_server_ready=True)

    def _local_failed(self, message: str) -> None:
        if not self._pending_local_send:
            return
        self._pending_local_send = False
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.stop_button.setVisible(False)
        self._update_status()
        self._add_bubble("error", message)

    def _wait_for_phone(self) -> None:
        self._pending_phone_send = True
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.stop_button.setVisible(True)
        self.status.setText("Starting the phone model…")
        self.phone_starter.ensure_started()

    def _phone_ready(self) -> None:
        if not self._pending_phone_send:
            return
        self._pending_phone_send = False
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.stop_button.setVisible(False)
        self.send(_server_ready=True)

    def _phone_failed(self, message: str) -> None:
        if not self._pending_phone_send:
            return
        self._pending_phone_send = False
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.stop_button.setVisible(False)
        self._update_status()
        self._add_bubble("error", message)

    def shutdown(self) -> None:
        """Stop this app's chat work and the servers it may have started."""

        self.stop()
        self.local_server.stop()
        self.phone_starter.stop()

    def send(self, _server_ready: bool = False) -> None:
        if self._chat is not None or self._pending_local_send:
            return
        if self._pending_phone_send:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        provider = self._provider
        if provider.kind == "openai" and not provider.model.strip():
            self._add_bubble(
                "error",
                "This provider has no model selected. Open the ⚙ settings, "
                "pick a model, and try again.",
            )
            return
        if not _server_ready and provider.kind == "openai":
            if self._local_provider(provider) and not self.local_server.is_ready():
                self._wait_for_local()
                return
            if (
                self._phone_provider(provider)
                and not self.phone_starter.is_ready()
            ):
                self._wait_for_phone()
                return

        self.input.clear()
        self._messages.append(ChatMessage("user", text))
        self._add_bubble("user", text)
        bubble = self._add_bubble("assistant", "")
        self._bubble = bubble
        self._set_streaming(True)

        if provider.kind == "openai":
            chat = HttpChat(self)
            chat.delta.connect(self._on_delta)
            chat.done.connect(self._on_done)
            chat.failed.connect(self._on_failed)
            chat.stopped.connect(self._on_stopped)
            self._chat = chat
            chat.start(provider, list(self._messages))
        else:
            executable = cli_executable(provider.kind)
            if executable is None:
                command = "claude" if provider.kind == "claude" else "codex"
                self._on_failed(
                    f"The `{command}` command was not found on PATH. Install it "
                    "or choose another provider."
                )
                return
            chat = CliChat(self)
            chat.delta.connect(self._on_delta)
            chat.tool.connect(self._on_tool)
            chat.done.connect(self._on_done)
            chat.failed.connect(self._on_failed)
            chat.stopped.connect(self._on_stopped)
            self._chat = chat
            chat.start(provider, list(self._messages), executable=executable)

    def stop(self) -> None:
        if self._pending_local_send:
            self._pending_local_send = False
            self.local_server.stop()
            self.input.setEnabled(True)
            self.send_button.setEnabled(True)
            self.stop_button.setVisible(False)
            self._update_status()
        if self._pending_phone_send:
            self._pending_phone_send = False
            self.phone_starter.stop()
            self.input.setEnabled(True)
            self.send_button.setEnabled(True)
            self.stop_button.setVisible(False)
            self._update_status()
        if self._chat is not None:
            self._chat.cancel()

    def _set_streaming(self, active: bool) -> None:
        self.send_button.setEnabled(not active)
        self.stop_button.setVisible(active)
        self.input.setEnabled(not active)
        if active:
            self.status.setText(f"Thinking… · {self._provider.label}")

    def _on_delta(self, piece: str) -> None:
        if self._bubble is not None:
            self._bubble.append_text(piece)
            self._scroll_later()

    def _on_tool(self, line: str) -> None:
        if self._bubble is not None:
            self._bubble.add_tool(line)
            self._scroll_later()

    def _finish(self, text: str, error: str | None, stopped: bool) -> None:
        chat = self._chat
        self._chat = None
        if chat is not None:
            chat.deleteLater()
        self._set_streaming(False)
        self._update_status()
        if self._bubble is None:
            return
        if error:
            self._bubble.setProperty("role", "error")
            self._bubble.style().unpolish(self._bubble)
            self._bubble.style().polish(self._bubble)
            self._bubble.set_text(error)
            self._bubble = None
            return
        if not text.strip():
            self._bubble.set_text("(no reply)")
        elif stopped:
            self._bubble.append_text("\n\n[stopped]")
        if text.strip():
            self._messages.append(ChatMessage("assistant", text))
            if not stopped and self._provider.kind == "openai":
                for saved in save_file_blocks(text):
                    if saved.error:
                        self._bubble.add_tool(
                            f"Could not save {saved.name}: {saved.error}"
                        )
                    elif saved.path is not None:
                        self._bubble.add_tool(
                            f"Saved {saved.name} → {saved.path}"
                        )
                self._scroll_later()
        self._bubble = None
        self.input.setFocus()

    def _on_done(self, text: str) -> None:
        self._finish(text, None, False)

    def _on_stopped(self, text: str) -> None:
        self._finish(text, None, True)

    def _on_failed(self, message: str) -> None:
        self._finish("", message, False)


class AgentSettingsDialog(QDialog):
    """Edit providers, endpoints, models and keys."""

    def __init__(
        self,
        store: AgentStore,
        adb_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Agent providers")
        self.resize(760, 640)
        self._store = copy.deepcopy(store)
        self._adb_path = adb_path
        self._current: Provider | None = None
        self._fetcher: ModelFetcher | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        intro = QLabel(
            "Keys are stored only in <code>~/.local/share/phone-link/agent.json</code> "
            "with owner-only permissions. The endpoint you select receives the "
            "conversation; local providers keep it on this machine. The built-in "
            "providers are the local llama.cpp server and the phone server; a "
            "custom OpenAI-compatible endpoint can be added if needed.",
            self,
        )
        intro.setWordWrap(True)
        intro.setObjectName("AgentHint")
        intro.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.list = QListWidget(self)
        self.list.setObjectName("AgentProviderList")
        self.list.currentItemChanged.connect(self._selection_changed)
        left.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        add = QPushButton("Add custom", self)
        add.clicked.connect(self._add_custom)
        buttons.addWidget(add)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self._remove_current)
        buttons.addWidget(self.remove_button)
        left.addLayout(buttons)
        body.addLayout(left, 2)

        self.form_host = QWidget(self)
        form = QVBoxLayout(self.form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        body.addWidget(self.form_host, 3)

        self.name_edit = QLineEdit(self)
        name_row = QFormLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.addRow("Name", self.name_edit)
        form.addLayout(name_row)

        self.kind_combo = QComboBox(self)
        self.kind_combo.addItem("OpenAI-compatible endpoint", "openai")
        self.kind_combo.addItem("Claude Code CLI", "claude")
        self.kind_combo.addItem("Codex CLI", "codex")
        self.kind_combo.currentIndexChanged.connect(self._sync_kind)
        self.kind_row = QFormLayout()
        self.kind_row.setContentsMargins(0, 0, 0, 0)
        self.kind_row.addRow("Backend", self.kind_combo)
        form.addLayout(self.kind_row)

        self.http_group = QGroupBox("Endpoint", self)
        http_form = QFormLayout(self.http_group)
        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText("https://host/v1")
        http_form.addRow("Base URL", self.url_edit)
        model_row = QWidget(self)
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(6)
        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        model_layout.addWidget(self.model_combo, 1)
        self.fetch_button = QPushButton("Fetch", self)
        self.fetch_button.setToolTip("Ask the endpoint for its model list")
        self.fetch_button.clicked.connect(self._fetch_models)
        model_layout.addWidget(self.fetch_button)
        http_form.addRow("Model", model_row)
        self.key_edit = QLineEdit(self)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("optional; stored locally")
        http_form.addRow("API key", self.key_edit)
        self.endpoint_hint = QLabel("", self)
        self.endpoint_hint.setOpenExternalLinks(True)
        self.endpoint_hint.setWordWrap(True)
        self.endpoint_hint.setObjectName("AgentHint")
        http_form.addRow("", self.endpoint_hint)
        form.addWidget(self.http_group)

        self.cli_group = QGroupBox("CLI options", self)
        cli_form = QFormLayout(self.cli_group)
        self.permission_combo = QComboBox(self)
        for label, value in (
            ("Full agent (bypass permissions)", "bypassPermissions"),
            ("Ask/deny prompts (accept edits)", "acceptEdits"),
            ("Plan only", "plan"),
        ):
            self.permission_combo.addItem(label, value)
        cli_form.addRow("Claude mode", self.permission_combo)
        self.sandbox_combo = QComboBox(self)
        for label, value in (
            ("Workspace write", "workspace-write"),
            ("Read only", "read-only"),
            ("Danger: full access", "danger-full-access"),
        ):
            self.sandbox_combo.addItem(label, value)
        cli_form.addRow("Codex sandbox", self.sandbox_combo)
        self.cwd_edit = QLineEdit(self)
        self.cwd_edit.setPlaceholderText("working directory (default: home)")
        cwd_row = QWidget(self)
        cwd_layout = QHBoxLayout(cwd_row)
        cwd_layout.setContentsMargins(0, 0, 0, 0)
        cwd_layout.setSpacing(6)
        cwd_layout.addWidget(self.cwd_edit, 1)
        browse = QPushButton("…", self)
        browse.setFixedWidth(30)
        browse.clicked.connect(self._browse_cwd)
        cwd_layout.addWidget(browse)
        cli_form.addRow("Working dir", cwd_row)
        form.addWidget(self.cli_group)

        self.phone_group = QGroupBox("Phone server", self)
        phone_layout = QVBoxLayout(self.phone_group)
        phone_layout.setSpacing(6)
        phone_help = QLabel(
            "Run a llama.cpp server inside Termux on the phone, then forward the "
            "port over ADB so this computer can reach it at 127.0.0.1:8080.\n\n"
            "In Termux:\n"
            "  pkg install llama-cpp\n"
            "  llama-server -m model.gguf --host 127.0.0.1 --port 8080\n\n"
            "The ADB forward works over USB and over wireless debugging.",
            self,
        )
        phone_help.setTextFormat(Qt.TextFormat.PlainText)
        phone_help.setWordWrap(True)
        phone_help.setObjectName("AgentHint")
        phone_layout.addWidget(phone_help)
        self.phone_button = QPushButton("Set up ADB forward and test", self)
        self.phone_button.clicked.connect(self._setup_phone)
        phone_layout.addWidget(self.phone_button)
        self.phone_status = QLabel("", self)
        self.phone_status.setWordWrap(True)
        self.phone_status.setObjectName("AgentHint")
        phone_layout.addWidget(self.phone_status)
        form.addWidget(self.phone_group)

        prompt_label = QLabel("System prompt", self)
        form.addWidget(prompt_label)
        self.prompt_edit = QPlainTextEdit(self)
        self.prompt_edit.setObjectName("CodeBlock")
        self.prompt_edit.setFixedHeight(86)
        form.addWidget(self.prompt_edit)
        form.addStretch(1)

        footer = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        footer.accepted.connect(self._save)
        footer.rejected.connect(self.reject)
        root.addWidget(footer)

        self._fill_list(self._store.active_id)

    # -- list and form -----------------------------------------------------

    def active_id(self) -> str:
        return self._store.active_id

    def _fill_list(self, select_id: str = "") -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for provider in self._store.providers:
            tags = []
            if provider.local:
                tags.append("local")
            if provider.kind in ("claude", "codex"):
                tags.append(provider.kind)
            suffix = f"  [{', '.join(tags)}]" if tags else ""
            item = QListWidgetItem(provider.label + suffix)
            item.setData(Qt.ItemDataRole.UserRole, provider.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if select_id:
            for row in range(self.list.count()):
                if self.list.item(row).data(Qt.ItemDataRole.UserRole) == select_id:
                    self.list.setCurrentRow(row)
                    return
        if self.list.count():
            self.list.setCurrentRow(0)

    def _selection_changed(self, current, _previous) -> None:
        if current is None:
            return
        self._store_form()
        provider_id = current.data(Qt.ItemDataRole.UserRole)
        provider = self._store.by_id(provider_id)
        if provider is None:
            return
        self._current = provider
        self.remove_button.setEnabled(not provider.builtin)
        self._load_provider(provider)

    def _load_provider(self, provider: Provider) -> None:
        self.name_edit.setText(provider.label)
        index = self.kind_combo.findData(provider.kind)
        self.kind_combo.setCurrentIndex(max(0, index))
        self.url_edit.setText(provider.base_url)
        self.model_combo.clear()
        if provider.model:
            self.model_combo.addItem(provider.model)
        self.model_combo.setCurrentText(provider.model)
        self.key_edit.setText(provider.api_key)
        self.permission_combo.setCurrentIndex(
            max(0, self.permission_combo.findData(provider.permission_mode))
        )
        self.sandbox_combo.setCurrentIndex(
            max(0, self.sandbox_combo.findData(provider.sandbox))
        )
        self.cwd_edit.setText(provider.working_dir)
        self.prompt_edit.setPlainText(provider.system_prompt or DEFAULT_SYSTEM_PROMPT)
        self._update_endpoint_hint(provider)
        self._sync_kind()

    def _store_form(self) -> None:
        provider = self._current
        if provider is None:
            return
        provider.label = self.name_edit.text().strip() or provider.id
        provider.kind = self.kind_combo.currentData()
        provider.base_url = self.url_edit.text().strip()
        provider.model = self.model_combo.currentText().strip()
        provider.api_key = self.key_edit.text().strip()
        provider.permission_mode = self.permission_combo.currentData()
        provider.sandbox = self.sandbox_combo.currentData()
        provider.working_dir = self.cwd_edit.text().strip()
        provider.system_prompt = self.prompt_edit.toPlainText().strip()
        if not provider.system_prompt:
            provider.system_prompt = DEFAULT_SYSTEM_PROMPT

    def _sync_kind(self) -> None:
        kind = self.kind_combo.currentData()
        self.http_group.setVisible(kind == "openai")
        self.cli_group.setVisible(kind in ("claude", "codex"))
        phone = self._current is not None and self._current.id == "phone"
        self.phone_group.setVisible(bool(phone and kind == "openai"))

    def _update_endpoint_hint(self, provider: Provider) -> None:
        if provider.id == "zara-fast":
            self.endpoint_hint.setText(
                "Phone Link starts this llama.cpp server automatically when the "
                "provider is used, and stops it when the window closes. Set "
                "PHONE_LINK_LLAMA_SERVER and PHONE_LINK_LOCAL_MODEL to choose "
                "the binary and model."
            )
        else:
            self.endpoint_hint.setText("")

    def _browse_cwd(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Working directory", self.cwd_edit.text() or str(Path.home())
        )
        if directory:
            self.cwd_edit.setText(directory)

    def _fetch_models(self) -> None:
        self._store_form()
        provider = self._current
        if provider is None:
            return
        self.fetch_button.setEnabled(False)
        self.fetch_button.setText("…")
        fetcher = ModelFetcher(self)
        fetcher.finished.connect(self._models_ready)
        fetcher.failed.connect(self._models_failed)
        fetcher.start(provider)
        self._fetcher = fetcher

    def _models_ready(self, models: list) -> None:
        self.fetch_button.setEnabled(True)
        self.fetch_button.setText("Fetch")
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems([str(model) for model in models])
        if current:
            self.model_combo.setCurrentText(current)
        self.endpoint_hint.setText(f"{len(models)} models available.")

    def _models_failed(self, message: str) -> None:
        self.fetch_button.setEnabled(True)
        self.fetch_button.setText("Fetch")
        QMessageBox.warning(self, "Could not fetch models", message)

    def _setup_phone(self) -> None:
        if not self._adb_path:
            self.phone_status.setText(
                "ADB was not found. Install android-tools-adb, then reopen this dialog."
            )
            return
        ok, error = adb_forward(self._adb_path, 8080)
        if not ok:
            self.phone_status.setText(f"ADB forward failed: {error}")
            return
        self._store_form()
        provider = self._current
        if provider is not None and probe(provider, timeout=2.0):
            self.phone_status.setText(
                "ADB forward is set up and the phone server answered."
            )
        else:
            self.phone_status.setText(
                "ADB forward is set up, but nothing answered on port 8080. "
                "Start the llama.cpp server in Termux first."
            )

    def _add_custom(self) -> None:
        self._store_form()
        existing = {provider.id for provider in self._store.providers}
        number = 1
        while f"custom-{number}" in existing:
            number += 1
        provider = Provider(
            id=f"custom-{number}",
            label=f"Custom provider {number}",
            kind="openai",
        )
        self._store.providers.append(provider)
        self._store.active_id = provider.id
        self._fill_list(provider.id)

    def _remove_current(self) -> None:
        provider = self._current
        if provider is None or provider.builtin:
            return
        answer = QMessageBox.question(
            self,
            "Remove provider",
            f"Remove “{provider.label}”? Its stored API key is deleted too.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.remove(provider.id)
        if self._store.active_id == provider.id:
            self._store.active_id = self._store.providers[0].id
        self._current = None
        self._fill_list(self._store.active_id)

    def _save(self) -> None:
        self._store_form()
        self._store.active_id = self._current.id if self._current else self._store.active_id
        try:
            save_store(self._store)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
            return
        self.accept()
