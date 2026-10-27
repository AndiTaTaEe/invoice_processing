import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from invoice_processing.http_client import APIError, RunRequestLimit, HTTPClient

def error(code, message="x"):
    """
    create an error response body with the given code and message.
    error body format: {"error": {"code": "...", "message": "...", "details": {}}}
    """
    return {"error": {"code": code, "message": message, "details": {}}}

class TestServer:
    def __init__(self, script):
        self.script = list(script)
        self.requests = [] # store the requests received by the server - method, path, idem_key, body
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b"" # read the request body if present, if not present, set body to empty bytes
                outer.requests.append((self.command, self.path, self.headers.get("Idempotency-Key"), body)) # store the request details
                status, headers, payload = outer.script.pop(0) # get the next response from the script
                raw = json.dumps(payload).encode()
                self.send_response(status) # send the response status code
                for key, value in headers.items():
                    self.send_header(key, value) # send the response headers
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            
            do_GET = do_POST = _handle # handle both GET and POST requests with the same method

            def log_message(self, *args):
                pass
        
        self.httpd = HTTPServer(("localhost", 0), Handler)
        self.url = f"http://localhost:{self.httpd.server_port}" # get the URL of the server with the assigned port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True) # run the server in a separate thread
    
    def __enter__(self):
        self.thread.start() # start the server thread
        return self
    def __exit__(self, *exc):
        self.httpd.shutdown() # shutdown the server when exiting the context
        self.httpd.server_close() # close the server socket

class HTTPClientTests(unittest.TestCase):
    def setUp(self):
        self.slept = [] # store the sleep durations for testing

    def client(self, url):
        return HTTPClient(url, sleep=self.slept.append) # create an HTTPClient with the given URL and a sleep function that appends to self.slept
    
    def test_503_429_503_201_status(self):
        script = [
            (503, {}, error("X")),
            (429, {"Retry-After": "2"}, error("RATE_LIMITED")),
            (503, {}, error("X")),
            (201, {}, {"ok": True})
        ]
        with TestServer(script) as server:
            result = self.client(server.url).post("/decisions", {"a": 1}, "k1") # make a POST request to /decisions with body {"a": 1} and idem_key "k1"
        self.assertEqual(result, {"ok": True}) # assert that the result is {"ok": True}
        self.assertEqual(len(server.requests), 4) # assert that 4 requests were made
        self.assertEqual(self.slept, [1.0, 2.0, 1.0]) # test retry-after sleep durations: first 503 -> sleep 1.0, then 429 with Retry-After 2 -> sleep 2.0, then 503 -> sleep 1.0

    def test_three_500_reach_max_retries(self):
        script = [
            (500, {}, error("X")),
            (500, {}, error("X")),
            (500, {}, error("X"))
        ]
        with TestServer(script) as server:
            with self.assertRaises(APIError) as context:
                self.client(server.url).post("/decisions", {"a": 1}, "k1") 
        self.assertEqual(len(server.requests), 3) # assert that 3 requests were made
        self.assertTrue(context.exception.exhausted) # assert that the exception indicates that the max retries were exhausted
        self.assertEqual(context.exception.statuses, [500, 500, 500]) # assert that the statuses of the requests were all 500

    def test_429_does_not_consume_retries(self):
        script = [
            (429, {"Retry-After": "1"}, error("RATE_LIMITED")),
            (429, {"Retry-After": "1"}, error("RATE_LIMITED")),
            (429, {"Retry-After": "1"}, error("RATE_LIMITED")),
            (500, {}, error("B")),
            (500, {}, error("B")),
            (201, {}, {"ok": 1})
        ]
        with TestServer(script) as server:
            result = self.client(server.url).post("/decisions", {"a": 1}, "k1") 
        self.assertEqual(result, {"ok": 1}) # assert that the result is {"ok": 1}
        self.assertEqual(len(server.requests), 6) # assert that 6 requests were made

    def test_429_rate_limited_gives_up_after_10_waits(self):
        script = [(429, {}, error("RATE_LIMITED"))] * 11 # 11 consecutive 429 responses
        with TestServer(script) as server:
            with self.assertRaises(APIError) as context:
                self.client(server.url).get("/inbox")
        self.assertEqual(len(server.requests), 11) # assert that 11 requests were made
        self.assertEqual(len(self.slept), 10) # assert that 10 sleeps were made (the 11th request does not sleep)
        self.assertEqual(context.exception.status, 429) 

    def test_run_request_limit_not_retrying(self):
        script = [(429, {}, error("RUN_REQUEST_LIMIT"))]
        with TestServer(script) as server:
            with self.assertRaises(RunRequestLimit):
                self.client(server.url).get("/inbox")
        self.assertEqual(len(server.requests), 1) # assert that only 1 request was made since RUN_REQUEST_LIMIT should not be retried

    def test_404_returns_none(self):
        script = [(404, {}, error("NOT_FOUND"))]
        with TestServer(script) as server:
            self.assertIsNone(self.client(server.url).get_optional("/suppliers/123")) # assert that get_optional returns None for a 404 response
        self.assertEqual(len(server.requests), 1) # assert that only 1 request was made

    def test_503_get_method(self):
        # we will test 2 503 responses followed by a 200 response - the supplier should be found, not None
        supplier = {"cui": "123", "status": "ACTIVE"}
        script = [
            (503, {}, error("X")),
            (503, {}, error("X")),
            (200, {}, supplier)
        ]
        with TestServer(script) as server:
            result = self.client(server.url).get_optional("/suppliers/123") # make a GET request to /suppliers/123
        self.assertEqual(result, supplier) # assert that the result is the supplier object

    def test_503_raise_error_not_none(self):
        script = [
            (503, {}, error("X")),
            (503, {}, error("X")),
            (503, {}, error("X"))
        ]
        with TestServer(script) as server:
            with self.assertRaises(APIError):
                self.client(server.url).get_optional("/suppliers/123") # make a GET request to /suppliers/123 which should raise APIError after 3 retries

    def test_other_4xx_not_retryable(self):
        script = [
            (422, {}, error("IDEMPOTENCY_KEY_REUSED"))
        ]
        with TestServer(script) as server:
            with self.assertRaises(APIError) as context:
                self.client(server.url).post("/decisions", {"a": 1}, "k1") # make a POST request to /decisions which should
        self.assertEqual(len(server.requests), 1) # assert that only 1 request was made since 422 is not retryable
        self.assertEqual(context.exception.code, "IDEMPOTENCY_KEY_REUSED") # assert that the error code is IDEMPOTENCY_KEY_REUSED
        self.assertFalse(context.exception.exhausted) # assert that the exception does not indicate that the max retries were exhausted

    def test_same_key_same_body_every_time(self):
        script = [
            (503, {}, error("X")),
            (503, {}, error("X")),
            (201, {}, {"ok": 1})
        ]
        body = {"document_id": "DOC-1", "reason_codes": ["A", "B"]}
        with TestServer(script) as server:
            self.client(server.url).post("/decisions", body, "DOC-1:decision")
        keys = {r[2] for r in server.requests} # get the set of idempotency keys used in the requests
        bodies = {r[3] for r in server.requests} # get the set of request bodies used in the requests
        self.assertEqual(keys, {"DOC-1:decision"})
        self.assertEqual(len(bodies), 1) # assert that the body is the same for every retry attempt

    def test_network_error_retrying_3_times(self):
        with socket.socket() as s: # free port socket to simulate network error
            s.bind(("localhost", 0))
            port = s.getsockname()[1]
        with self.assertRaises(APIError) as context:
            self.client(f"http://localhost:{port}").get("/inbox") # make a GET request to a port that is not listening to simulate network error
        self.assertIsNone(context.exception.status) # assert that the status is None since it was a network error
        self.assertEqual(context.exception.statuses, [None, None, None]) # assert that the statuses of the requests were all None since they were network errors
        self.assertEqual(len(self.slept), 2) # assert that 2 sleeps were made (the first request does not sleep, the second and third do)

    def test_post_without_idem_key_raises_value_error(self):
        with self.assertRaises(ValueError):
            HTTPClient("http://localhost:8080").request("POST", "/decisions", body={}) # assert that a ValueError is raised when making a POST request without an idempotency key

if __name__ == "__main__":
    unittest.main()