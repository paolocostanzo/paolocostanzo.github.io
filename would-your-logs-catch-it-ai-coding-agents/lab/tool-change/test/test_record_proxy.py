"""Tests for record_proxy.py with a stub upstream on 127.0.0.1 (no model, no network beyond loopback)."""
import gzip
import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import record_proxy as rp  # noqa: E402

A = "Lab tool, revision A. Returns the word ok and records the call in its own log. It has no other effect."
B = "Lab tool, revision B. Records the call in its own log and returns the word ok. Nothing else happens."
TOOLS = {"model": "qwen3.8:27b-q8_0", "messages": [{"role": "user", "content": "secret prompt"}],
         "tools": [{"type": "function", "function": {"name": "mcp__q3-lab__q3_noop", "description": A}}]}


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Stub.seen.append((self.headers.get("Content-Encoding"), self.rfile.read(n)))
        body = b'{"chunk":1}\n{"chunk":2}\n'
        if Stub.mode == "slow":
            time.sleep(0.4)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        if Stub.mode == "cut":   # promise more than is sent, then hang up: the proxy sees IncompleteRead
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class RecordProxyTest(unittest.TestCase):
    def setUp(self):
        Stub.seen, Stub.mode = [], "ok"
        self.up = start(ThreadingHTTPServer(("127.0.0.1", 0), Stub))
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "proxy.jsonl"
        self.px = start(rp.serve("127.0.0.1:0", f"http://127.0.0.1:{self.up.server_port}", str(self.out), "qwen"))

    def tearDown(self):
        self.px.shutdown(); self.up.shutdown(); self.px.server_close(); self.up.server_close(); self.tmp.cleanup()

    def conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.px.server_port, timeout=10)

    def post(self, body, headers=None, raw=None):
        c = self.conn()
        c.request("POST", "/v1/chat/completions", body=raw if raw is not None else json.dumps(body).encode(),
                  headers=headers or {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read()

    def recs(self, kind=None, wait=1.0):
        end = time.time() + wait
        while True:   # the "done" line is written after the client has its answer
            rows = [json.loads(x) for x in self.out.read_text().splitlines()] if self.out.exists() else []
            if kind is None or rows and sum(r["kind"] == "done" for r in rows) == sum(r["kind"] == "request" for r in rows) \
                    or time.time() > end:
                return [r for r in rows if kind is None or r["kind"] == kind]
            time.sleep(0.02)

    def test_openai_tools_recorded_and_body_forwarded_unchanged(self):
        status, data = self.post(TOOLS)
        self.assertEqual((status, data), (200, b'{"chunk":1}\n{"chunk":2}\n'))
        self.assertEqual(json.loads(Stub.seen[0][1]), TOOLS)
        [req] = self.recs("request")
        self.assertEqual((req["client"], req["model"], req["inspect"]), ("qwen", "qwen3.8:27b-q8_0", "ok"))
        self.assertEqual(req["tools"][0]["revision"], "A")
        [done] = self.recs("done")
        self.assertEqual((done["id"], done["status"], done["error"]), (req["id"], 200, None))
        self.assertNotIn("secret prompt", self.out.read_text())

    def test_request_time_is_arrival_not_completion(self):
        Stub.mode = "slow"
        t0 = time.time()
        self.post(TOOLS)
        req, done = self.recs("request")[0], self.recs("done")[0]
        self.assertLess(req["t"] - t0, 0.3)
        self.assertGreaterEqual(done["t"] - req["t"], 0.35)

    def test_request_line_written_before_upstream_answers(self):
        Stub.mode = "slow"
        th = threading.Thread(target=self.post, args=(TOOLS,)); th.start()
        time.sleep(0.2)
        self.assertEqual(len(self.recs("request", wait=0)), 1)
        th.join()

    def test_chunked_request_body_is_read_forwarded_and_inspected(self):
        raw = json.dumps(TOOLS).encode()
        chunked = b"".join(b"%x\r\n%s\r\n" % (len(p), p) for p in (raw[:40], raw[40:])) + b"0\r\n\r\n"
        self.post(None, headers={"Transfer-Encoding": "chunked", "Content-Type": "application/json"}, raw=iter([chunked]))
        self.assertEqual(json.loads(Stub.seen[0][1]), TOOLS)
        self.assertEqual(self.recs("request")[0]["tools"][0]["revision"], "A")

    def test_gzip_request_inspected_and_forwarded_compressed(self):
        z = gzip.compress(json.dumps(TOOLS).encode())
        self.post(None, headers={"Content-Encoding": "gzip", "Content-Type": "application/json"}, raw=z)
        self.assertEqual(Stub.seen[0], ("gzip", z))
        self.assertEqual(self.recs("request")[0]["tools"][0]["revision"], "A")

    def test_unreadable_body_is_marked_not_empty(self):
        self.post(None, headers={"Content-Type": "application/json"}, raw=b"not json")
        req = self.recs("request")[0]
        self.assertTrue(req["inspect"].startswith("body not readable"))
        self.assertEqual(Stub.seen[0][1], b"not json")

    def test_head_gets_no_body(self):
        c = self.conn()
        c.request("HEAD", "/")
        r = c.getresponse()
        self.assertEqual((r.status, r.read()), (200, b""))

    def test_http10_client_gets_plain_body(self):
        s = socket.create_connection(("127.0.0.1", self.px.server_port), timeout=10)
        raw = json.dumps(TOOLS).encode()
        s.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nContent-Length: %d\r\n\r\n%s" % (len(raw), raw))
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        s.close()
        head, body = data.split(b"\r\n\r\n", 1)
        self.assertNotIn(b"chunked", head.lower())
        self.assertEqual(body, b'{"chunk":1}\n{"chunk":2}\n')

    def test_upstream_cut_mid_stream_gives_one_done_with_error(self):
        Stub.mode = "cut"
        try:
            self.post(TOOLS)
        except (http.client.HTTPException, OSError):
            pass
        done = self.recs("done")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["status"], 200)
        self.assertIn("IncompleteRead", done[0]["error"])

    def test_upstream_down_is_502_and_recorded_once(self):
        self.up.shutdown(); self.up.server_close()
        status, _ = self.post({"model": "m", "tools": []})
        self.assertEqual(status, 502)
        self.assertEqual(len(self.recs("request")), 1)
        [done] = self.recs("done")
        self.assertIsNone(done["status"])
        self.assertIsNotNone(done["error"])

    def test_http10_with_short_upstream_body_keeps_content_length(self):
        Stub.mode = "cut"
        s = socket.create_connection(("127.0.0.1", self.px.server_port), timeout=10)
        raw = json.dumps(TOOLS).encode()
        s.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nContent-Length: %d\r\n\r\n%s" % (len(raw), raw))
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        s.close()
        head, body = data.split(b"\r\n\r\n", 1)
        self.assertIn(b"content-length: 1000", head.lower())
        self.assertLess(len(body), 1000)
        self.assertIn("IncompleteRead", self.recs("done")[0]["error"])

    def send_raw(self, data, half_close=True):
        s = socket.create_connection(("127.0.0.1", self.px.server_port), timeout=10)
        s.sendall(data)
        if half_close:
            s.shutdown(socket.SHUT_WR)
        out = b""
        while chunk := s.recv(65536):
            out += chunk
        s.close()
        return out

    def test_short_content_length_body_not_forwarded(self):
        raw = json.dumps(TOOLS).encode()
        out = self.send_raw(b"POST /v1/chat/completions HTTP/1.1\r\nContent-Length: %d\r\n\r\n%s" % (len(raw) + 50, raw))
        self.assertIn(b" 400 ", out.split(b"\r\n")[0] + b" ")
        self.assertEqual(Stub.seen, [])
        self.assertTrue(self.recs("request")[0]["inspect"].startswith("incomplete request"))

    def test_unterminated_chunked_body_not_forwarded(self):
        raw = json.dumps(TOOLS).encode()
        out = self.send_raw(b"POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n%x\r\n%s\r\n" % (len(raw), raw))
        self.assertIn(b"400", out.split(b"\r\n")[0])
        self.assertEqual(Stub.seen, [])
        self.assertTrue(self.recs("request")[0]["inspect"].startswith("incomplete request"))

    def test_revision_named_in_messages_is_counted_without_text(self):
        body = {"model": "m", "messages": [{"role": "tool", "content": "found: q3_noop: " + B}], "tools": []}
        self.post(body)
        req = self.recs("request")[0]
        self.assertEqual(req["elsewhere"], {"A": 0, "B": 1})
        self.assertNotIn("Nothing else happens", self.out.read_text())

    def test_codex_namespace_tools(self):
        ns = {"type": "namespace", "name": "mcp__q3_lab", "description": "Tools in the mcp__q3_lab namespace.",
              "tools": [{"type": "function", "name": "q3_noop", "description": A}]}
        [e] = rp.tool_entries({"tools": [ns]})
        self.assertEqual((e["name"], e["revision"]), ("mcp__q3_lab/q3_noop", "A"))

    def test_malformed_tool_metadata_is_forwarded_and_marked(self):
        for tools in ([{"name": "x", "description": 123}], 1):
            Stub.seen = []
            status, _ = self.post({"model": "m", "tools": tools})
            self.assertEqual(status, 200)
            self.assertEqual(len(Stub.seen), 1)
        reqs = self.recs("request")
        self.assertEqual(reqs[0]["inspect"], "ok")
        self.assertTrue(reqs[1]["inspect"].startswith("tools not readable"))
        self.assertEqual(len(self.recs("done")), 2)

    def test_shapes_and_hashes(self):
        self.assertEqual(rp.tool_entries({"tools": [{"name": "q3_noop", "description": B}]})[0]["revision"], "B")
        g = rp.tool_entries({"tools": [{"functionDeclarations": [{"name": "q3_noop", "description": A}, {"name": "x"}]}]})
        self.assertEqual([t["revision"] for t in g], ["A", None])
        self.assertNotEqual(g[0]["sha256"], rp.tool_entries({"tools": [{"name": "t", "description": B}]})[0]["sha256"])

    def test_listen_must_be_loopback(self):
        with self.assertRaises(SystemExit):
            rp.main(["--listen", "0.0.0.0:1", "--upstream", "http://127.0.0.1:1", "--out", str(self.out), "--client", "x"])


if __name__ == "__main__":
    unittest.main()
