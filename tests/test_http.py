from http.server import HTTPServer
from io import BytesIO
import json

from beam_profiler.server import handler_for


class FakeProfiler:
    def status(self): return {"connected":False}
    def request(self, action, data): return {"action":action}


class Socket:
    def __init__(self, request):
        self.input, self.output = BytesIO(request), BytesIO()
    def makefile(self, *_args): return self.input
    def sendall(self, data): self.output.write(data)


def response(request):
    server = HTTPServer(("127.0.0.1", 8877), handler_for(FakeProfiler()), bind_and_activate=False)
    server.server_port = 8877
    sock = Socket(request)
    handler_for(FakeProfiler())(sock, ("127.0.0.1", 10000), server)
    server.server_close()
    return sock.output.getvalue()


def test_local_status():
    result = response(b'GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:8877\r\n\r\n')
    assert b'200 OK' in result
    assert json.loads(result.split(b'\r\n\r\n')[1]) == {"connected":False}


def test_cross_origin_control_rejected():
    result = response(b'POST /api/connect HTTP/1.1\r\nHost: 127.0.0.1:8877\r\nOrigin: https://example.com\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}')
    assert b'403 Forbidden' in result


def test_rebinding_host_rejected():
    assert b'403 Forbidden' in response(b'GET /api/status HTTP/1.1\r\nHost: example.com:8877\r\n\r\n')


def test_non_json_post_rejected():
    assert b'415 Unsupported Media Type' in response(b'POST /api/connect HTTP/1.1\r\nHost: localhost:8877\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\n{}')


def test_internal_restore_not_exposed_over_http():
    assert b'404 Not Found' in response(b'POST /api/restore_snapshot HTTP/1.1\r\nHost: localhost:8877\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}')
