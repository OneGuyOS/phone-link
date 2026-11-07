"""Unit tests for the app-managed local llama.cpp server."""

from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from phone_link import local_server
from phone_link.local_server import (
    DEFAULT_PORT,
    MODEL_ENV,
    SERVER_ENV,
    LocalServer,
    default_threads,
    find_model,
    find_server,
    server_argv,
)


class DetectTests(unittest.TestCase):
    def test_configured_server_wins(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "llama-server"
            binary.write_bytes(b"")
            with mock.patch.dict(os.environ, {SERVER_ENV: str(binary)}):
                self.assertEqual(find_server(), str(binary))

    def test_missing_configured_server_is_not_silently_replaced(self) -> None:
        with mock.patch.dict(
            os.environ, {SERVER_ENV: "/nonexistent/llama-server"}
        ):
            with mock.patch.object(local_server.shutil, "which", return_value=None):
                self.assertIsNone(find_server())

    def test_configured_model_wins(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "tiny.gguf"
            model.write_bytes(b"GGUF")
            with mock.patch.dict(os.environ, {MODEL_ENV: str(model)}):
                self.assertEqual(find_model(), str(model))

    def test_phone_model_is_preferred_over_other_models(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            models = Path(folder)
            (models / "OLMoE-1B-7B-Q4.gguf").write_bytes(b"GGUF")
            (models / "Qwen3.5-4B-Q4_K_M.gguf").write_bytes(b"GGUF")
            with mock.patch.dict(os.environ, {MODEL_ENV: ""}):
                with mock.patch.object(
                    local_server, "default_model_dirs", return_value=[models]
                ):
                    self.assertTrue(find_model().endswith("Qwen3.5-4B-Q4_K_M.gguf"))

    def test_missing_configured_model_is_reported_as_missing(self) -> None:
        with mock.patch.dict(
            os.environ, {MODEL_ENV: "/nonexistent/model.gguf"}
        ):
            self.assertIsNone(find_model())


class ArgumentTests(unittest.TestCase):
    def test_server_is_bound_to_loopback(self) -> None:
        args = server_argv(
            "/opt/llama-server",
            "/models/tiny.gguf",
            port=DEFAULT_PORT,
            context=2048,
            threads=3,
            alias="zara-fast",
        )
        self.assertEqual(args[0], "/opt/llama-server")
        self.assertEqual(args[args.index("-m") + 1], "/models/tiny.gguf")
        self.assertEqual(args[args.index("--host") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("--port") + 1], str(DEFAULT_PORT))
        self.assertEqual(args[args.index("--ctx-size") + 1], "2048")
        self.assertEqual(args[args.index("--threads") + 1], "3")
        self.assertEqual(args[args.index("--alias") + 1], "zara-fast")
        self.assertIn("--jinja", args)
        self.assertEqual(
            args[args.index("--chat-template-kwargs") + 1],
            '{"enable_thinking":false}',
        )
        self.assertNotIn("0.0.0.0", args)

    def test_default_threads_is_sane(self) -> None:
        self.assertGreaterEqual(default_threads(), 1)


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _server(
        self, *, server: str, model: str
    ) -> tuple[LocalServer, list[str]]:
        failures: list[str] = []
        instance = LocalServer(port=48123, server_path=server, model_path=model)
        instance.failed.connect(failures.append)
        return instance, failures

    def test_missing_binary_explains_how_to_set_it(self) -> None:
        server, failures = self._server(
            server="/nonexistent/llama-server", model="/nonexistent/model.gguf"
        )
        with mock.patch.object(local_server, "models_answer", return_value=False):
            with mock.patch.object(local_server, "port_open", return_value=False):
                server.ensure_started()
        self.assertEqual(len(failures), 1)
        self.assertIn("llama-server", failures[0])
        self.assertIn(SERVER_ENV, failures[0])

    def test_missing_model_names_the_environment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "llama-server"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            server, failures = self._server(server=str(binary), model="")
            with mock.patch.object(
                local_server, "models_answer", return_value=False
            ):
                with mock.patch.object(
                    local_server, "port_open", return_value=False
                ):
                    with mock.patch.object(
                        local_server, "find_server", return_value=str(binary)
                    ):
                        with mock.patch.object(local_server, "find_model", return_value=None):
                            server.ensure_started()
        self.assertEqual(len(failures), 1)
        self.assertIn("gguf", failures[0])
        self.assertIn(MODEL_ENV, failures[0])

    def test_cold_start_reaches_ready_and_stops(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "llama-server"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
                "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
                "class Handler(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        body = json.dumps({'data': [{'id': 'zara-fast'}]}).encode()\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Type', 'application/json')\n"
                "        self.send_header('Content-Length', str(len(body)))\n"
                "        self.end_headers()\n"
                "        self.wfile.write(body)\n"
                "    def log_message(self, *args):\n"
                "        pass\n"
                "HTTPServer(('127.0.0.1', port), Handler).serve_forever()\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            model = Path(folder) / "tiny.gguf"
            model.write_bytes(b"GGUF")

            server = LocalServer(
                port=port,
                server_path=str(binary),
                model_path=str(model),
                start_timeout_ms=15000,
            )
            events: list[str] = []
            loop = QEventLoop()
            server.ready.connect(lambda: events.append("ready"))
            server.failed.connect(lambda message: events.append(message))
            server.ready.connect(loop.quit)
            server.failed.connect(loop.quit)
            QTimer.singleShot(0, server.ensure_started)
            QTimer.singleShot(15000, loop.quit)
            loop.exec()

            self.assertEqual(events, ["ready"])
            self.assertTrue(server.is_ready())
            server.stop()
            self.assertFalse(server.is_ready())


if __name__ == "__main__":
    unittest.main()
