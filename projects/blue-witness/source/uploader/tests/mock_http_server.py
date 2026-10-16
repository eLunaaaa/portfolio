from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import sys


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code, data):
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def do_GET(self):
        if self.path == "/api/v1/health":
            self.reply(200, {"status": "ok", "database": "ok"})
        elif self.path == "/api/v1/events/EVT_TEST/evidence/status":
            self.reply(200, {"stored": True})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        while size:
            chunk = self.rfile.read(min(size, 65536))
            if not chunk:
                break
            size -= len(chunk)
        if self.path == "/api/v1/events/EVT_TEST/evidence" and size == 0:
            self.reply(201, {"stored": True})
        else:
            self.reply(400, {"error": "invalid"})

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
