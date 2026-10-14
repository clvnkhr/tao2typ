"""Loopback-only catalogue preview backed by atomic batch checkpoints."""

import hashlib
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote, urlsplit

from tao2typ.catalogue import generate_site
from tao2typ.diagnostics import failure_stems, read_failure_artifact
from tao2typ.storage import read_bytes, read_json


class Preview:
    def __init__(self, archive: Path, output: Path, base_url: str) -> None:
        self.archive = archive.resolve()
        self.output = output
        self.base_url = base_url
        self.lock = threading.Lock()
        self.revision = ""
        self.page = b""

    def snapshot(self) -> dict[str, Any]:
        signature = []
        for name in ("state.json", "queue.json"):
            path = self.archive / name
            stat = path.stat() if path.exists() else None
            signature.append((name, stat.st_mtime_ns if stat else 0, stat.st_size if stat else 0))
        reports = list((self.archive / "jobs").glob("*.json*"))
        # Job names begin with their UTC timestamp; compression changes mtime.
        latest = max(reports, key=lambda path: path.name.removesuffix(".gz"), default=None)
        report = read_json(latest) if latest else {}
        if latest:
            stat = latest.stat()
            signature.append((latest.name, stat.st_mtime_ns, stat.st_size))
        revision = hashlib.sha256(repr(signature).encode()).hexdigest()
        queue = (
            read_json(self.archive / "queue.json") if (self.archive / "queue.json").exists() else {}
        )
        results = report.get("results", [])
        return {
            "revision": revision,
            "running": bool(report.get("started_at") and not report.get("finished_at")),
            "attempted": len(results),
            "converted": sum(row.get("status") == "converted" for row in results),
            "failed": sum(row.get("status") == "failed" for row in results),
            "remaining": len(queue.get("posts", [])),
        }

    def index(self) -> bytes:
        with self.lock:
            revision = self.snapshot()["revision"]
            if revision != self.revision:
                generate_site(
                    self.archive,
                    self.output,
                    download_base_url=self.base_url,
                    copy_downloads=False,
                    diagnostics_base_url=self.base_url,
                )
                self.page = (
                    (self.output / "index.html")
                    .read_bytes()
                    .replace(b"<body>", f'<body data-preview-revision="{revision}">'.encode(), 1)
                )
                self.revision = revision
            return self.page

    def diagnostic(self, url_path: str) -> tuple[bytes, str] | None:
        parts = unquote(url_path).split("/")
        if (
            len(parts) not in {4, 5}
            or parts[:2] != ["", "__tao2typ_failure"]
            or not parts[2].isascii()
            or not parts[2].isdigit()
            or (len(parts) == 5 and (parts[3] != "html" or not parts[4].isdigit()))
        ):
            return None
        raw = read_json(self.archive / "state.json")
        posts = [{**post, "id": post.get("id", key)} for key, post in raw.get("posts", {}).items()]
        post = next((post for post in posts if str(post["id"]) == parts[2]), None)
        if post is None or not post.get("last_error"):
            return None
        stem = failure_stems(posts).get(parts[2], "")
        return read_failure_artifact(self.archive, post, stem, "/".join(parts[3:]))

    def artifact(self, url_path: str) -> tuple[bytes, str] | None:
        path = PurePosixPath(unquote(url_path))
        if ".." in path.parts or len(path.parts) != 3 or path.parts[0] != "/":
            return None
        folder, filename = path.parts[1:]
        suffixes = {
            "pdfs": (".pdf",),
            "typst": (".typ", ".typ.gz"),
            "ir": (".json", ".json.gz"),
            "assets": (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"),
        }
        if folder not in suffixes or not filename.lower().endswith(suffixes[folder]):
            return None
        source = self.archive / folder / filename
        if not source.is_file() and folder in {"typst", "ir"} and not filename.endswith(".gz"):
            source = source.with_name(filename + ".gz")
        if (
            not source.is_file()
            or source.is_symlink()
            or not source.resolve().is_relative_to(self.archive / folder)
        ):
            return None
        if filename.endswith(".gz"):
            return source.read_bytes(), "application/gzip"
        media_type = mimetypes.guess_type(filename.removesuffix(".gz"))[0] or "text/plain"
        if folder == "typst":
            media_type = "text/plain; charset=utf-8"
        return read_bytes(source), media_type


def serve_preview(archive: Path, port: int = 8765) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("Preview port must be between 1 and 65535")
    if not (archive / "state.json").is_file():
        raise ValueError("No archive state; run a conversion batch first")
    with TemporaryDirectory(prefix="tao2typ-preview-") as temporary:
        preview = Preview(archive, Path(temporary), f"http://127.0.0.1:{port}")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                try:
                    if path in {"/", "/index.html"}:
                        data, media_type = preview.index(), "text/html; charset=utf-8"
                    elif path == "/__tao2typ_progress":
                        data = json.dumps(preview.snapshot()).encode()
                        media_type = "application/json"
                    elif path in {"/app.js", "/style.css"}:
                        preview.index()
                        data = (preview.output / path[1:]).read_bytes()
                        media_type = "text/javascript" if path.endswith(".js") else "text/css"
                    elif (artifact := preview.artifact(path)) is not None:
                        data, media_type = artifact
                    elif (diagnostic := preview.diagnostic(path)) is not None:
                        data, media_type = diagnostic
                    else:
                        self.send_error(404)
                        return
                except (OSError, ValueError):
                    self.send_error(503, "Checkpoint temporarily unavailable")
                    return
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        print(f"Local preview: http://127.0.0.1:{port}/#queue", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
