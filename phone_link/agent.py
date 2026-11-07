"""Agent chat backends for Phone Link.

Three kinds of backend share one conversation model:

* ``openai`` - any OpenAI-compatible ``/v1/chat/completions`` endpoint.  The
  built-in providers use it for the local llama.cpp server on this computer
  and the llama.cpp server running in Termux on the phone (reachable through
  ``adb forward``).  Custom endpoints can be added in the settings dialog.
* ``claude`` - the Claude Code CLI in ``--print`` mode, streaming JSON events.
* ``codex`` - the Codex CLI in ``exec --json`` mode.

Provider definitions and API keys are stored in
``$XDG_DATA_HOME/phone-link/agent.json`` with owner-only permissions.  Nothing
here sends data anywhere unless the active provider is a remote endpoint.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

DEFAULT_SYSTEM_PROMPT = (
    "You are the assistant built into Phone Link, a KDE desktop companion for "
    "an Android phone. Be concise and practical. You do not automatically see "
    "the phone's state; ask for or use details the user provides."
)


@dataclass
class Provider:
    id: str
    label: str
    kind: str = "openai"  # openai | claude | codex
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    local: bool = False
    free: bool = False
    builtin: bool = False
    permission_mode: str = "bypassPermissions"  # claude CLI
    sandbox: str = "workspace-write"  # codex CLI
    working_dir: str = ""


@dataclass
class ChatMessage:
    role: str  # user | assistant | system
    text: str


# Presets are the starting points; the settings dialog edits copies of these
# and stores the result.  Phone Link ships with two providers: a small local
# llama.cpp model that the app can start itself, and the llama.cpp server in
# Termux on the phone.  Custom OpenAI-compatible endpoints can be added from
# the settings dialog.
PRESETS: tuple[Provider, ...] = (
    Provider(
        id="zara-fast",
        label="Zara Fast (local)",
        base_url="http://127.0.0.1:18089/v1",
        model="zara-fast",
        local=True,
        builtin=True,
    ),
    Provider(
        id="phone",
        label="Phone server (ADB forward)",
        base_url="http://127.0.0.1:8080/v1",
        local=True,
        builtin=True,
    ),
)

PRESET_BY_ID = {provider.id: provider for provider in PRESETS}

_EDITABLE = (
    "label",
    "kind",
    "base_url",
    "model",
    "api_key",
    "system_prompt",
    "local",
    "free",
    "permission_mode",
    "sandbox",
    "working_dir",
)


# --------------------------------------------------------------------------
# Storage


def default_config_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "phone-link" / "agent.json"


@dataclass
class AgentStore:
    providers: list[Provider] = field(default_factory=list)
    active_id: str = ""

    def active(self) -> Provider:
        for provider in self.providers:
            if provider.id == self.active_id:
                return provider
        return self.providers[0]

    def by_id(self, provider_id: str) -> Provider | None:
        for provider in self.providers:
            if provider.id == provider_id:
                return provider
        return None

    def update(self, provider: Provider) -> None:
        for index, existing in enumerate(self.providers):
            if existing.id == provider.id:
                self.providers[index] = provider
                return
        self.providers.append(provider)

    def remove(self, provider_id: str) -> None:
        self.providers = [p for p in self.providers if p.id != provider_id]


def _provider_to_dict(provider: Provider) -> dict:
    return {
        "id": provider.id,
        "label": provider.label,
        "kind": provider.kind,
        "base_url": provider.base_url,
        "model": provider.model,
        "api_key": provider.api_key,
        "system_prompt": provider.system_prompt,
        "local": provider.local,
        "free": provider.free,
        "builtin": provider.builtin,
        "permission_mode": provider.permission_mode,
        "sandbox": provider.sandbox,
        "working_dir": provider.working_dir,
    }


def _provider_from_dict(data: dict) -> Provider | None:
    provider_id = str(data.get("id") or "").strip()
    if not provider_id:
        return None
    base = PRESET_BY_ID.get(provider_id)
    provider = replace(base) if base else Provider(id=provider_id, label=provider_id)
    for name in _EDITABLE:
        if name in data:
            setattr(provider, name, data[name])
    provider.builtin = provider_id in PRESET_BY_ID
    return provider


def load_store(path: Path | None = None) -> AgentStore:
    """Load providers, falling back to the built-in presets."""

    config = path or default_config_path()
    store = AgentStore(providers=[replace(provider) for provider in PRESETS])
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return store
    if not isinstance(raw, dict):
        return store

    saved = raw.get("providers")
    if isinstance(saved, list):
        merged: dict[str, Provider] = {p.id: p for p in store.providers}
        custom: list[Provider] = []
        for item in saved:
            if not isinstance(item, dict):
                continue
            provider = _provider_from_dict(item)
            if provider is None:
                continue
            if provider.id in PRESET_BY_ID:
                merged[provider.id] = provider
            elif bool(item.get("builtin")):
                # A preset that this version no longer ships (for example a
                # cloud provider or an old local model) must not come back as
                # a custom provider.
                continue
            else:
                custom.append(provider)
        store.providers = list(merged.values()) + custom

    active = str(raw.get("active_id") or "")
    if active and store.by_id(active):
        store.active_id = active
    return store


def save_store(store: AgentStore, path: Path | None = None) -> Path:
    """Write providers and keys atomically with owner-only permissions."""

    config = path or default_config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "active_id": store.active_id,
        "providers": [_provider_to_dict(provider) for provider in store.providers],
    }
    temporary = config.with_name(config.name + ".tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, config)
    try:
        os.chmod(config, 0o600)
    except OSError:
        pass
    return config


def provider_key(provider: Provider) -> str:
    """The API key stored for a custom provider, if any."""

    return provider.api_key.strip()


# --------------------------------------------------------------------------
# OpenAI-compatible HTTP


def chat_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def models_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/models"):
        return base
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def sse_payloads(lines: Iterable[str]) -> Iterable[dict]:
    """Yield the JSON objects in an OpenAI-style SSE stream."""

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            if payload == "[DONE]":
                return
            continue
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        if isinstance(chunk, dict):
            yield chunk


def chunk_text(chunk: dict) -> str:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
    else:
        content = None
    if content is None and isinstance(first.get("text"), str):
        content = first["text"]
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _request_headers(provider: Provider) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    key = provider_key(provider)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def describe_http_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            body = exc.read().decode("utf-8", "replace")
            parsed = json.loads(body)
            error = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message") or "")
            elif isinstance(error, str):
                detail = error
            if not detail:
                detail = body.strip()[:300]
        except Exception:
            detail = str(exc)
        return f"The endpoint returned HTTP {exc.code}: {detail}"
    if isinstance(exc, urllib.error.URLError):
        return f"Could not reach the endpoint: {exc.reason}"
    return str(exc)


def list_models(provider: Provider, timeout: float = 10.0) -> list[str]:
    """Fetch model ids from an OpenAI-compatible endpoint."""

    headers = _request_headers(provider)
    headers["Accept"] = "application/json"
    request = urllib.request.Request(models_url(provider.base_url), headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    models: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if isinstance(model_id, str) and model_id:
                models.append(model_id)
    return models


def probe(provider: Provider, timeout: float = 1.2) -> bool:
    """Cheap reachability check used by the local-provider scan."""

    try:
        list_models(provider, timeout=timeout)
        return True
    except Exception:
        return False


class HttpChat(QObject):
    """Stream one completion on a worker thread."""

    delta = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    stopped = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._response = None

    def start(self, provider: Provider, messages: list[ChatMessage]) -> None:
        self._stop.clear()
        conversation = [{"role": "system", "content": provider.system_prompt}]
        conversation.extend(
            {"role": message.role, "content": message.text} for message in messages
        )
        body = json.dumps(
            {"model": provider.model, "messages": conversation, "stream": True}
        ).encode("utf-8")
        self._thread = threading.Thread(
            target=self._run,
            args=(provider, body),
            name="phone-link-agent-http",
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def _run(self, provider: Provider, body: bytes) -> None:
        text: list[str] = []
        request = urllib.request.Request(
            chat_url(provider.base_url),
            data=body,
            headers=_request_headers(provider),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                self._response = response
                for raw in response:
                    if self._stop.is_set():
                        break
                    try:
                        line = raw.decode("utf-8", "replace")
                    except Exception:
                        continue
                    for chunk in sse_payloads([line]):
                        piece = chunk_text(chunk)
                        if piece:
                            text.append(piece)
                            self.delta.emit(piece)
            if self._stop.is_set():
                self.stopped.emit("".join(text))
            else:
                self.done.emit("".join(text))
        except Exception as exc:
            if self._stop.is_set():
                self.stopped.emit("".join(text))
            else:
                self.failed.emit(describe_http_error(exc))
        finally:
            self._response = None


class ModelFetcher(QObject):
    """Fetch a provider's model list without blocking the UI."""

    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None

    def start(self, provider: Provider) -> None:
        self._thread = threading.Thread(
            target=self._run, args=(provider,), daemon=True
        )
        self._thread.start()

    def _run(self, provider: Provider) -> None:
        try:
            models = list_models(provider)
        except Exception as exc:
            self.failed.emit(describe_http_error(exc))
            return
        if not models:
            self.failed.emit("The endpoint returned no models.")
        else:
            self.finished.emit(models)


# --------------------------------------------------------------------------
# CLI agents


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def conversation_prompt(
    messages: list[ChatMessage],
    system_prompt: str = "",
    *,
    include_system: bool = True,
) -> str:
    """Render a conversation for a CLI agent.

    CLI sessions are fresh each turn, so the whole history travels in the
    prompt.  Claude takes its system prompt through a flag and should pass
    ``include_system=False``.
    """

    blocks: list[str] = []
    if include_system and system_prompt.strip():
        blocks.append("System: " + system_prompt.strip())
    for message in messages:
        role = {"user": "User", "assistant": "Assistant", "system": "System"}.get(
            message.role, message.role.title()
        )
        blocks.append(f"{role}: {message.text.strip()}")
    blocks.append("Assistant:")
    return "\n\n".join(blocks)


def claude_argv(
    executable: str, provider: Provider, prompt: str
) -> list[str]:
    args = [
        executable,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--permission-mode",
        provider.permission_mode or "bypassPermissions",
    ]
    if provider.model.strip():
        args.extend(["--model", provider.model.strip()])
    if provider.system_prompt.strip():
        args.extend(["--append-system-prompt", provider.system_prompt.strip()])
    return args


def codex_argv(executable: str, provider: Provider, prompt: str) -> list[str]:
    working_dir = provider.working_dir.strip() or str(Path.home())
    args = [
        executable,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        working_dir,
        "--sandbox",
        provider.sandbox or "workspace-write",
    ]
    if provider.model.strip():
        args.extend(["--model", provider.model.strip()])
    args.append(prompt)
    return args


def claude_text_delta(event: dict) -> str:
    if event.get("type") != "stream_event":
        return ""
    inner = event.get("event")
    if not isinstance(inner, dict):
        return ""
    if inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta")
    if not isinstance(delta, dict):
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""


def claude_tool(event: dict) -> str:
    if event.get("type") == "stream_event":
        inner = event.get("event")
        if isinstance(inner, dict) and inner.get("type") == "content_block_start":
            block = inner.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name") or "tool"
                return str(name)
        return ""
    if event.get("type") == "assistant":
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            names = [
                str(block.get("name"))
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name")
            ]
            if names:
                return ", ".join(names)
    return ""


def claude_result_text(event: dict) -> str:
    if event.get("type") != "result":
        return ""
    result = event.get("result")
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return str(result.get("text") or "")
    return ""


def codex_text(event: dict) -> str:
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    if item.get("type") != "agent_message":
        return ""
    text = item.get("text")
    return text if isinstance(text, str) else ""


def codex_tool(event: dict) -> str:
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    kind = item.get("type")
    if kind == "command_execution":
        command = item.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if isinstance(command, str) and command.strip():
            return "$ " + command.strip()
    elif kind == "reasoning":
        return ""
    return ""


class CliChat(QObject):
    """Run one CLI agent turn and stream its text events."""

    delta = Signal(str)
    tool = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    stopped = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._buffer = ""
        self._stderr = ""
        self._text: list[str] = []
        self._stopping = False
        self._streamed = False
        self._seen: set[str] = set()
        self._provider: Provider | None = None

    def start(
        self,
        provider: Provider,
        messages: list[ChatMessage],
        *,
        executable: str,
    ) -> None:
        self._buffer = ""
        self._stderr = ""
        self._text = []
        self._stopping = False
        self._streamed = False
        self._seen = set()
        self._provider = provider

        if provider.kind == "claude":
            prompt = conversation_prompt(
                messages, provider.system_prompt, include_system=False
            )
            args = claude_argv(executable, provider, prompt)
        else:
            prompt = conversation_prompt(messages, provider.system_prompt)
            args = codex_argv(executable, provider, prompt)

        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.setWorkingDirectory(
            provider.working_dir.strip() or str(Path.home())
        )
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        process.start(executable, args[1:])

    def cancel(self) -> None:
        self._stopping = True
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            QTimer.singleShot(1500, self._kill_if_running)

    def _kill_if_running(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None:
            return
        data = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handle_line(line.strip())

    def _read_stderr(self) -> None:
        process = self._process
        if process is None:
            return
        self._stderr += bytes(process.readAllStandardError()).decode(
            "utf-8", "replace"
        )
        if len(self._stderr) > 8000:
            self._stderr = self._stderr[-8000:]

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        try:
            event = json.loads(strip_ansi(line))
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        provider = self._provider
        if provider is None:
            return

        if provider.kind == "claude":
            tool = claude_tool(event)
            if tool:
                self.tool.emit("Using " + tool)
            piece = claude_text_delta(event)
            if piece:
                self._streamed = True
                self._text.append(piece)
                self.delta.emit(piece)
            if event.get("type") == "result" and not self._streamed:
                final = claude_result_text(event)
                if final:
                    self._text.append(final)
                    self.delta.emit(final)
        else:
            tool = codex_tool(event)
            if tool:
                self.tool.emit(tool)
            piece = codex_text(event)
            if piece:
                item = event.get("item")
                item_id = str(item.get("id") or "") if isinstance(item, dict) else ""
                if item_id and item_id in self._seen:
                    return
                if item_id:
                    self._seen.add(item_id)
                self._text.append(piece)
                self.delta.emit(piece)

    def _on_finished(self, code: int, _status) -> None:
        self._read_stdout()
        text = "".join(self._text)
        if self._stopping:
            self.stopped.emit(text)
        elif text.strip():
            self.done.emit(text)
        else:
            detail = strip_ansi(self._stderr).strip()
            detail = detail[-600:] if detail else ""
            if detail:
                self.failed.emit(
                    f"The agent exited with code {code}.\n{detail}"
                )
            else:
                self.failed.emit(f"The agent exited with code {code} without a reply.")
        self._process = None

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            kind = self._provider.kind if self._provider else "agent"
            self.failed.emit(
                f"Could not start the {kind} command. Make sure it is installed "
                "and on PATH."
            )
            self._process = None


def cli_executable(kind: str) -> str | None:
    command = "claude" if kind == "claude" else "codex"
    return shutil.which(command)


def adb_forward(adb_path: str, port: int = 8080) -> tuple[bool, str]:
    """Expose the phone's local port on this computer over the ADB link."""

    import subprocess

    try:
        result = subprocess.run(
            [adb_path, "forward", f"tcp:{port}", f"tcp:{port}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip() or "adb forward failed"
    return True, ""


def preferred_provider(store: AgentStore) -> Provider:
    """The saved provider, else the first reachable local one, else the first."""

    saved = store.by_id(store.active_id) if store.active_id else None
    if saved is not None:
        return saved
    local = [p for p in store.providers if p.local and p.kind == "openai"]
    for provider in local[:3]:
        if probe(provider, timeout=0.35):
            return provider
    for provider in store.providers:
        if provider.local:
            return provider
    return store.providers[0]
