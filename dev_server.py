from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import urllib.request
import urllib.error

ROOT = Path(__file__).parent / "web"
RELAY = "http://127.0.0.1:3011"
PORT = 4174

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.startswith("/relay/"):
            self.proxy()
            return
        self.static()

    def do_POST(self):
        if self.path.startswith("/relay/"):
            self.proxy()
            return
        self.send_error(404)

    def do_PATCH(self):
        self.do_POST()

    def static(self):
        path = "/" + self.path.split("?", 1)[0].lstrip("/")
        if path == "/":
            path = "/index.html"
        file_path = (ROOT / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(ROOT.resolve())) or not file_path.exists():
            self.send_error(404)
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def proxy(self):
        target = RELAY + self.path.replace("/relay", "", 1)
        body = None
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            body = self.rfile.read(length)
        headers = {}
        for key in ("Authorization", "Content-Type", "Accept"):
            if self.headers.get(key):
                headers[key] = self.headers[key]
        req = urllib.request.Request(target, data=body, method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, value)
                self.end_headers()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as error:
            data = error.read()
            self.send_response(error.code)
            self.send_header("Content-Type", error.headers.get("Content-Type", "text/plain"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
    print(f"Tidal Echo LAN preview: http://0.0.0.0:{PORT}/", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
