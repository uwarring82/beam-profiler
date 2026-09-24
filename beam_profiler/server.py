import argparse
import json
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import Profiler


STATIC = Path(__file__).with_name("static")


def handler_for(profiler):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Only request logs carry a status code; always keep error logs.
            if len(args) < 2 or str(args[1]) not in {"200", "204"}:
                super().log_message(fmt, *args)

        def respond(self, code, data, mime="application/json", download=None):
            if mime == "application/json":
                data = json.dumps(data, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="' + download + '"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def local_request(self):
            # Prevent cross-origin control of local USB hardware / DNS rebinding.
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return host in allowed and (not origin or origin in {"http://" + h for h in allowed})

        def do_GET(self):
            if not self.local_request():
                return self.respond(403, {"error": "Local access only."})
            url = urlparse(self.path)
            path = url.path
            palette = parse_qs(url.query).get("palette", ["thermal"])[0]
            if path == "/api/status":
                return self.respond(200, profiler.status())
            if path == "/api/log":
                try:
                    after = int(parse_qs(url.query).get("after", ["0"])[0])
                except ValueError:
                    return self.respond(400, {"error": "after must be an integer."})
                return self.respond(200, {"entries": profiler.log.since(after), "seq": profiler.log.seq,
                                          "folder": str(profiler.log.directory) if profiler.log.directory else None})
            if path == "/api/frame":
                return self.respond(200, profiler.packet())
            if path == "/api/export":
                try:
                    return self.respond(200, profiler.export(palette), "application/zip", "beam-snapshot.zip")
                except ValueError as error:
                    return self.respond(409, {"error": str(error)})
            if path == "/api/png":
                try:
                    return self.respond(200, profiler.inspection(palette), "image/png", "beam-inspection.png")
                except ValueError as error:
                    return self.respond(409, {"error": str(error)})
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in files:
                name, mime = files[path]
                return self.respond(200, (STATIC / name).read_bytes(), mime)
            self.respond(404, {"error": "Not found."})

        def do_POST(self):
            if not self.local_request():
                return self.respond(403, {"error": "Local access only."})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(415, {"error": "Expected application/json."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("Invalid request size.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                path = urlparse(self.path).path
                if not path.startswith("/api/"):
                    return self.respond(404, {"error": "Not found."})
                action = path.removeprefix("/api/")
                if action not in {"scan","connect","disconnect","pause","configure","analysis",
                                  "dark","uncertainty","reset_statistics","record","auto_exposure"}:
                    return self.respond(404, {"error": "Unknown action."})
                result = profiler.request(action, data)
                self.respond(200, result)
            except (ValueError, KeyError, TypeError) as error:
                self.respond(400, {"error": str(error)})
            except Exception as error:
                self.respond(503, {"error": str(error) or "Camera operation timed out."})
    return Handler


def main():
    parser = argparse.ArgumentParser(description="Local USB beam profiler")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--sessions-dir", type=Path, default=Path("sessions"),
                        help="Folder for session logs and recordings (default: ./sessions)")
    parser.add_argument("--restore-snapshot", type=Path, help="Restore a local export and start frozen; statistics reset")
    args = parser.parse_args()
    profiler = Profiler(args.sessions_dir)
    if profiler.log.error:
        print(profiler.log.error, flush=True)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(profiler))
    except OSError as error:
        profiler.close()
        parser.exit(1, f"Could not listen on port {args.port}: {error}. Try --port {args.port + 1}.\n")
    try:
        profiler.request("scan")
        if args.restore_snapshot:
            try:
                profiler.request("restore_snapshot", {"archive":args.restore_snapshot.read_bytes(),
                                                      "source":str(args.restore_snapshot)})
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
                parser.exit(1, f"Could not restore {args.restore_snapshot}: {error}\n")
        profiler.log.add("server_start", f"Serving http://127.0.0.1:{args.port}.", port=args.port)
        print(f"Beam profiler: http://127.0.0.1:{args.port}", flush=True)
        if profiler.log.directory:
            print(f"Session log and recordings: {profiler.log.directory}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        profiler.close()


if __name__ == "__main__":
    main()
