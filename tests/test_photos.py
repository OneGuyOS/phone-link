"""Camera-photo import: MediaStore parsing, deduplication and state."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phone_link.photos import (
    MAX_SEEN,
    PhotoImportError,
    PhotoImportState,
    PhotoImporter,
    choose_destination,
    camera_query_command,
    load_state,
    parse_photos,
    safe_filename,
    save_state,
)

ROW = (
    "Row: {index} _id={id}, _display_name={name}, "
    "relative_path=DCIM/Camera/, date_added={date}, _size={size}"
)
OUTPUT = "\n".join(
    (
        ROW.format(index=0, id=11, name="IMG_0002.jpg", date=200, size=4),
        ROW.format(index=1, id=10, name="IMG_0001.jpg", date=100, size=3),
    )
)


def completed(argv, returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class FakeRunner:
    def __init__(self, output=OUTPUT, files=None, failures=()):
        self.output = output.encode("utf-8") if isinstance(output, str) else output
        self.files = files or {}
        self.failures = set(failures)
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if "shell" in argv:
            return completed(argv, stdout=self.output)
        if "exec-out" in argv:
            path = argv[-1]
            if path in self.failures:
                return completed(argv, 1, b"", b"cat: no such file")
            data = self.files.get(path)
            if data is None:
                return completed(argv, 1, b"", b"cat: no such file")
            return completed(argv, stdout=data)
        return completed(argv, 1, b"", b"unexpected command")


class ParseTests(unittest.TestCase):
    def test_rows_are_parsed_and_sorted_by_date(self) -> None:
        photos = parse_photos(OUTPUT)
        self.assertEqual([photo.name for photo in photos], ["IMG_0001.jpg", "IMG_0002.jpg"])
        self.assertEqual(photos[0].remote_path, "/sdcard/DCIM/Camera/IMG_0001.jpg")
        self.assertEqual(photos[0].key, "10:100:3")

    def test_bytes_output_is_accepted(self) -> None:
        self.assertEqual(len(parse_photos(OUTPUT.encode("utf-8"))), 2)

    def test_other_folders_and_unsafe_names_are_skipped(self) -> None:
        output = "\n".join(
            (
                ROW.format(index=0, id=1, name="ok.jpg", date=1, size=1),
                ROW.format(index=1, id=2, name="../escape.jpg", date=2, size=1),
                ROW.format(index=2, id=3, name="shot.jpg", date=3, size=1).replace(
                    "DCIM/Camera/", "DCIM/Screenshots/"
                ),
                ROW.format(index=3, id=4, name="bad.jpg", date=4, size=-1),
                "garbage",
            )
        )
        photos = parse_photos(output)
        self.assertEqual([photo.name for photo in photos], ["ok.jpg"])

    def test_no_rows_is_empty(self) -> None:
        self.assertEqual(parse_photos("No result found.\n"), ())


class CommandTests(unittest.TestCase):
    def test_query_targets_the_camera_folder(self) -> None:
        command = camera_query_command("adb", "serial", 0)
        self.assertEqual(command[:4], ["adb", "-s", "serial", "shell"])
        self.assertIn("content query", command[4])
        self.assertIn("relative_path='DCIM/Camera/'", command[4])
        self.assertNotIn("date_added >=", command[4])

    def test_query_uses_the_watermark(self) -> None:
        command = camera_query_command("adb", "serial", 1234)
        self.assertIn("date_added >= 1234", command[4])

    def test_query_ignores_a_negative_watermark(self) -> None:
        self.assertNotIn("date_added >=", camera_query_command("adb", "s", -5)[4])


class FilenameTests(unittest.TestCase):
    def test_paths_are_reduced_to_one_component(self) -> None:
        self.assertEqual(safe_filename("../../etc/passwd", "fallback.jpg"), "passwd")
        self.assertEqual(safe_filename("a/b/photo.jpg", "fallback.jpg"), "photo.jpg")

    def test_empty_and_hidden_names_get_the_fallback(self) -> None:
        self.assertEqual(safe_filename("", "photo-1.jpg"), "photo-1.jpg")
        self.assertEqual(safe_filename("..", "photo-1.jpg"), "photo-1.jpg")
        self.assertEqual(safe_filename(".secret.jpg", "photo-1.jpg"), "photo-1.jpg")

    def test_control_characters_are_removed(self) -> None:
        self.assertEqual(safe_filename("a\x00b\x1fc.jpg", "x.jpg"), "abc.jpg")


class DestinationTests(unittest.TestCase):
    def test_a_free_name_is_used_as_is(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path, present = choose_destination(folder, "photo.jpg", 4)
            self.assertEqual(path, folder / "photo.jpg")
            self.assertFalse(present)

    def test_same_size_is_already_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "photo.jpg").write_bytes(b"data")
            path, present = choose_destination(folder, "photo.jpg", 4)
            self.assertTrue(present)
            self.assertEqual(path.name, "photo.jpg")

    def test_a_different_file_gets_a_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "photo.jpg").write_bytes(b"old")
            path, present = choose_destination(folder, "photo.jpg", 4)
            self.assertFalse(present)
            self.assertEqual(path.name, "photo-1.jpg")


class StateTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "photos.json"
            state = PhotoImportState(since=123, seen=["a", "b"])
            save_state(state, path)
            loaded = load_state(path)
            self.assertEqual(loaded.since, 123)
            self.assertEqual(loaded.seen, ["a", "b"])

    def test_missing_or_broken_files_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "photos.json"
            self.assertEqual(load_state(path), PhotoImportState())
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_state(path), PhotoImportState())

    def test_seen_is_capped(self) -> None:
        state = PhotoImportState(seen=[str(index) for index in range(MAX_SEEN + 10)])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "photos.json"
            save_state(state, path)
            loaded = load_state(path)
            self.assertEqual(len(loaded.seen), MAX_SEEN)
            self.assertEqual(loaded.seen[-1], str(MAX_SEEN + 9))


class ImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name) / "Pictures"
        self.files = {
            "/sdcard/DCIM/Camera/IMG_0001.jpg": b"one",
            "/sdcard/DCIM/Camera/IMG_0002.jpg": b"two!",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def importer(self, runner, state=None):
        importer = PhotoImporter("adb", runner=runner)
        importer.serial = "phone"
        importer.folder = self.folder
        importer.state = state or PhotoImportState()
        return importer

    def test_imports_files_and_advances_the_watermark(self) -> None:
        runner = FakeRunner(files=self.files)
        importer = self.importer(runner)
        result = importer.run()

        self.assertEqual(result.imported, 2)
        self.assertEqual(result.failed, 0)
        self.assertEqual((self.folder / "IMG_0001.jpg").read_bytes(), b"one")
        self.assertEqual((self.folder / "IMG_0002.jpg").read_bytes(), b"two!")
        self.assertEqual(importer.state.since, 200)
        self.assertEqual(len(importer.state.seen), 2)
        pulls = [call for call in runner.calls if "exec-out" in call[0]]
        self.assertEqual(len(pulls), 2)
        self.assertEqual(pulls[0][0][-1], "/sdcard/DCIM/Camera/IMG_0001.jpg")

    def test_second_run_does_not_pull_seen_photos(self) -> None:
        runner = FakeRunner(files=self.files)
        importer = self.importer(runner)
        importer.run()
        runner.calls.clear()
        result = importer.run()

        self.assertEqual(result.imported, 0)
        self.assertEqual(result.failed, 0)
        self.assertFalse(any("exec-out" in call[0] for call in runner.calls))

    def test_existing_same_size_file_counts_as_already_imported(self) -> None:
        self.folder.mkdir(parents=True)
        (self.folder / "IMG_0001.jpg").write_bytes(b"one")
        runner = FakeRunner(files=self.files)
        importer = self.importer(runner)
        result = importer.run()

        self.assertEqual(result.imported, 1)
        self.assertEqual(result.duplicates, 1)
        self.assertFalse((self.folder / "IMG_0001-1.jpg").exists())
        self.assertEqual(len(importer.state.seen), 2)

    def test_a_name_clash_with_different_content_gets_a_suffix(self) -> None:
        self.folder.mkdir(parents=True)
        (self.folder / "IMG_0001.jpg").write_bytes(b"older")
        runner = FakeRunner(files=self.files)
        result = self.importer(runner).run()

        self.assertEqual(result.imported, 2)
        self.assertEqual((self.folder / "IMG_0001-1.jpg").read_bytes(), b"one")

    def test_a_failed_pull_is_reported_and_retried(self) -> None:
        runner = FakeRunner(files=self.files, failures={"/sdcard/DCIM/Camera/IMG_0001.jpg"})
        importer = self.importer(runner)
        result = importer.run()

        self.assertEqual(result.imported, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(len(result.errors), 1)
        # The watermark must not move past the failed photo.
        self.assertEqual(importer.state.since, 0)

        runner.failures.clear()
        result = importer.run()
        self.assertEqual(result.imported, 1)
        self.assertEqual((self.folder / "IMG_0001.jpg").read_bytes(), b"one")

    def test_a_query_error_is_fatal(self) -> None:
        runner = FakeRunner(
            output="Error while accessing provider:media\n"
            "java.lang.IllegalArgumentException: Invalid column size\n"
        )
        importer = self.importer(runner)
        with self.assertRaises(PhotoImportError):
            importer.run()

    def test_a_nonzero_query_is_fatal(self) -> None:
        def runner(argv, *, timeout):
            return completed(argv, 1, b"", b"device offline")

        with self.assertRaises(PhotoImportError):
            self.importer(runner).run()

    def test_missing_adb_or_phone_is_fatal(self) -> None:
        with self.assertRaises(PhotoImportError):
            PhotoImporter(None).run()
        importer = PhotoImporter("adb", runner=FakeRunner())
        importer.serial = ""
        importer.folder = self.folder
        with self.assertRaises(PhotoImportError):
            importer.run()

    def test_oversized_photos_are_skipped(self) -> None:
        runner = FakeRunner(files=self.files)
        importer = self.importer(runner)
        with mock.patch("phone_link.photos.MAX_PHOTO_BYTES", 2):
            result = importer.run()
        self.assertEqual(result.failed, 2)
        self.assertEqual(result.imported, 0)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_import_is_stopped_cleanly(self) -> None:
        importer = self.importer(FakeRunner())
        importer.stop()
        self.assertFalse(importer.start())


if __name__ == "__main__":
    unittest.main()
