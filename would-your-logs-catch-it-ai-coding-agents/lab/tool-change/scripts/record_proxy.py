#!/usr/bin/env python3
"""Recording proxy for the open-weight cells (D39): sits between a client and the model endpoint inside the VM and writes, for
every request, which tool definitions the client sent to the model. Standard library only.

    python3 record_proxy.py --listen 127.0.0.1:11435 --upstream http://192.168.122.1:11434 --out ~/q3-out/proxy.jsonl --client qwen

Every request is forwarded with its original body bytes and its response streamed back. Two kinds of JSON line, linked by "id":
  - "request", written when the request has arrived and before anything is sent upstream:
    {"kind": "request", "id": n, "t": <arrival, epoch s>, "client": ..., "method": ..., "path": ..., "model": ...,
     "tools": [{"name": ..., "sha256": <of the description>, "revision": "A"|"B"|null, "head": <first 80 chars>}],
     "elsewhere": {"A": n, "B": n},   # revision names found in the rest of the request (e.g. a tool-search result in
                                      # the messages, the way Qwen Code hands MCP tools to the model); counts only, no text
     "inspect": "ok" | "<why the body could not be read>"}
  - "done", written once when the exchange ends: {"kind": "done", "id": n, "t": ..., "status": <upstream status or null>,
    "error": null | "<what failed>"}
A request whose body could not be read has "inspect" set to the reason, so it is never mistaken for a request without tools.
Prompts and replies are not recorded: only the tool list, which is what D39 asks about.
"""
import argparse
import gzip
import hashlib
import http.client
import itertools
import json
import re
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

REVISION = re.compile(r"\brevision ([AB])\b")
HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length"}


def tool_entries(body):
    """The tools in a request body, whatever the API: OpenAI and Ollama (tools[].function), OpenAI Responses and Anthropic
    (tools[].name), Gemini-style (tools[].functionDeclarations[]), and Codex CLI's namespaces (tools[] of type "namespace",
    whose own tools[] hold the MCP server's tools; recorded as "<namespace>/<tool>")."""
    out = []

    def add(f, prefix=""):
        desc = f.get("description") or ""
        if not isinstance(desc, str):
            desc = json.dumps(desc, ensure_ascii=False)
        m = REVISION.search(desc)
        out.append({"name": prefix + f["name"], "sha256": hashlib.sha256(desc.encode("utf-8")).hexdigest(),
                    "revision": m.group(1) if m else None, "head": desc[:80]})

    tools = body.get("tools") or []
    if not isinstance(tools, list):
        raise ValueError("tools is not a list")
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "namespace" and isinstance(t.get("tools"), list):
            for f in t["tools"]:
                if isinstance(f, dict) and "name" in f:
                    add(f, str(t.get("name")) + "/")
            continue
        fns = t.get("functionDeclarations") or t.get("function_declarations")
        items = fns if isinstance(fns, list) else [t.get("function") if isinstance(t.get("function"), dict) else t]
        for f in items:
            if isinstance(f, dict) and "name" in f:
                add(f)
    return out


def elsewhere(body):
    """How many times each revision is named outside tools[] (messages, system prompt...): counts only."""
    rest = {k: v for k, v in body.items() if k != "tools"}
    text = json.dumps(rest, ensure_ascii=False)
    return {r: len(re.findall(r"\brevision " + r + r"\b", text)) for r in "AB"}


def inspect(raw, encoding):
    """(model, tools, elsewhere, status) from a request body; the original bytes are forwarded untouched, this reads a
    decoded copy."""
    if not raw:
        return None, [], {"A": 0, "B": 0}, "ok"
    enc = (encoding or "identity").strip().lower()
    try:
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw)
        elif enc != "identity":
            return None, [], None, "unsupported content-encoding: " + enc
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError, OSError, zlib.error) as e:
        return None, [], None, "body not readable as JSON: " + type(e).__name__
    if not isinstance(body, dict):
        return None, [], None, "body is not a JSON object"
    try:
        return body.get("model"), tool_entries(body), elsewhere(body), "ok"
    except Exception as e:   # unexpected shapes are an explicit inspection failure; the request is still forwarded
        return body.get("model") if isinstance(body.get("model"), str) else None, [], None, \
            "tools not readable: " + type(e).__name__


class BadRequestBody(Exception):
    pass


def read_exact(rfile, n):
    data = rfile.read(n)
    if len(data) != n:
        raise BadRequestBody(f"body shorter than declared ({len(data)} of {n} bytes)")
    return data


def read_chunked(rfile):
    """The entity body of a request sent with Transfer-Encoding: chunked; incomplete framing raises BadRequestBody."""
    out = bytearray()
    while True:
        line = rfile.readline(65537)
        if not line.endswith(b"\n"):
            raise BadRequestBody("chunk size line missing")
        try:
            size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError:
            raise BadRequestBody("bad chunk size") from None
        if size == 0:
            while True:   # trailers, up to the blank line
                t = rfile.readline(65537)
                if t in (b"\r\n", b"\n"):
                    return bytes(out)
                if not t.endswith(b"\n"):
                    raise BadRequestBody("chunked body not terminated")
        out += read_exact(rfile, size)
        if rfile.readline(3) not in (b"\r\n", b"\n"):
            raise BadRequestBody("chunk not followed by CRLF")


def make_handler(upstream, out, client, lock, ids):
    up = urlsplit(upstream)

    def write(rec):
        with lock, open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _forward(self):
            t = round(time.time(), 3)
            rid = next(ids)
            te = (self.headers.get("Transfer-Encoding") or "").lower()
            if te and te != "chunked":
                write({"kind": "request", "id": rid, "t": t, "client": client, "method": self.command, "path": self.path,
                       "model": None, "tools": [], "inspect": "unsupported transfer-encoding: " + te})
                write({"kind": "done", "id": rid, "t": round(time.time(), 3), "status": None, "error": "not forwarded"})
                self.send_error(501, "transfer-encoding not supported")
                return
            try:
                raw = (read_chunked(self.rfile) if te == "chunked"
                       else read_exact(self.rfile, int(self.headers.get("Content-Length") or 0)))
            except (BadRequestBody, ValueError, OSError) as e:   # never forward an incomplete request
                write({"kind": "request", "id": rid, "t": t, "client": client, "method": self.command, "path": self.path,
                       "model": None, "tools": [], "inspect": "incomplete request: " + str(e)[:200]})
                write({"kind": "done", "id": rid, "t": round(time.time(), 3), "status": None, "error": "not forwarded"})
                try:
                    self.send_error(400, "incomplete request body")
                except OSError:
                    pass
                return
            model, tools, other, how = inspect(raw, self.headers.get("Content-Encoding"))
            write({"kind": "request", "id": rid, "t": t, "client": client, "method": self.command, "path": self.path,
                   "model": model, "tools": tools, "elsewhere": other, "inspect": how})

            status, error, started = None, None, False
            http10 = self.request_version == "HTTP/1.0"
            conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=600)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            headers["Host"] = up.netloc
            if raw or self.command in ("POST", "PUT", "PATCH"):
                headers["Content-Length"] = str(len(raw))
            try:
                conn.request(self.command, self.path, body=raw or None, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                bodyless = self.command == "HEAD" or status in (204, 304) or 100 <= status < 200
                self.send_response(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                chunked = not bodyless and not http10
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                elif resp.getheader("Content-Length"):   # HTTP/1.0 or bodyless: keep the length so a short body shows
                    self.send_header("Content-Length", resp.getheader("Content-Length"))
                self.send_header("Connection", "close")
                self.end_headers()
                started = True
                if not bodyless:
                    while True:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk) if chunked else chunk)
                        self.wfile.flush()
                    if resp.length:   # read1 ends quietly when the upstream hangs up early: a short body is an error,
                        raise http.client.IncompleteRead(b"", resp.length)   # and the client gets no closing chunk
                    if chunked:
                        self.wfile.write(b"0\r\n\r\n")
            except (OSError, http.client.HTTPException) as e:
                error = type(e).__name__ + ": " + str(e)[:200]
                if not started:
                    try:
                        msg = ("upstream error: " + error).encode()
                        self.send_response(502)
                        self.send_header("Content-Length", str(len(msg)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(msg)
                    except OSError:
                        pass
            finally:
                conn.close()
                write({"kind": "done", "id": rid, "t": round(time.time(), 3), "status": status, "error": error})
            self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _forward

    return Handler


def serve(listen, upstream, out, client):
    host, port = listen.rsplit(":", 1)
    srv = ThreadingHTTPServer((host, int(port)), make_handler(upstream, out, client, threading.Lock(), itertools.count(1)))
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen", default="127.0.0.1:11435")
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--client", required=True)
    a = ap.parse_args(argv)
    if not a.listen.startswith("127.0.0.1:"):
        sys.exit("record_proxy: listen on 127.0.0.1 only")
    srv = serve(a.listen, a.upstream, a.out, a.client)
    print(f"record_proxy: {a.listen} -> {a.upstream}, writing {a.out}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
