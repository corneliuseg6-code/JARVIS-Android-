#!/usr/bin/env python3
"""A stand-in for Ollama, for testing JARVIS without downloading a model.

    python3 tools/mock_ollama.py            # listens on 127.0.0.1:11434

It answers /api/tags and streams a canned reply from /api/chat in the same
NDJSON shape Ollama uses, so the whole chat path (SSE, streaming render,
conversation persistence, speech trigger) can be exercised offline.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLIES = {
    "ro": "Sunt JARVIS. Totul funcționează: interfața, serverul și conexiunea.",
    "en": "JARVIS here. Interface, server and connection are all working.",
    "fr": "Ici JARVIS. L'interface, le serveur et la connexion fonctionnent.",
    "de": "Hier JARVIS. Oberfläche, Server und Verbindung funktionieren.",
    "es": "Aquí JARVIS. La interfaz, el servidor y la conexión funcionan.",
    "it": "Qui JARVIS. Interfaccia, server e connessione funzionano.",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            body = json.dumps({
                "models": [
                    {"name": "llama3.2:3b"},
                    {"name": "qwen3-vl:2b-instruct"},
                ]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        system = ""
        for message in payload.get("messages", []):
            if message.get("role") == "system":
                system = message.get("content", "")
        lang = "en"
        for code, name in (("ro", "Romanian"), ("fr", "French"), ("de", "German"),
                           ("es", "Spanish"), ("it", "Italian"), ("en", "English")):
            if name in system:
                lang = code
                break
        text = REPLIES.get(lang, REPLIES["en"])

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for word in text.split(" "):
            self._chunk(json.dumps({"message": {"role": "assistant", "content": word + " "},
                                    "done": False}) + "\n")
            time.sleep(0.05)
        self._chunk(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}) + "\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _chunk(self, text):
        data = text.encode()
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    print(f"mock ollama on 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
