import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("http_client") # logger for the HTTP client

# global variables
CLIENT_TIMEOUT = 10 # seconds, defined in documentation
MAX_RETRY_ATTEMPTS = 3 # maximum number of retry attempts for 5xx status codes / network errors / timeouts
MAX_RATE_WAITS = 10 # maximum number of rate limit waits before giving up
RETRYABLE_STATUS_CODES = {500, 502, 503, 504} # retryable status codes for 5xx errors
NO_RESOURCE_STATUS_CODE = 404
RATE_LIMIT_STATUS_CODE = 429

class APIError(Exception):
    """
    Custom exception class for API errors

    attributes:
        method: HTTP method used for the request
        path: API endpoint path
        status: last HTTP status code received (None = network error/timeout)
        code: error.code from the error body
        statuses: list of status codes received during retries
        exhausted: whether the retry attempts were exhausted
    """
    def __init__(self, method, path, status, code=None, message="", statuses=None, exhausted=False):
        self.method = method
        self.path = path
        self.status = status
        self.code = code
        self.message = message
        self.statuses = statuses or [] # list of status codes received during retries
        self.exhausted = exhausted # whether the retry attempts were exhausted
        super().__init__(f"APIError: {method} {path} returned status {status} with code {code}: {message} ")

class RunRequestLimit(Exception):
    """
    429 RUN_REQUEST_LIMIT - the number of requests has been reached for the current run. the program should exit with a non-zero exit code
    """

def path_segment(value):
    """
    encode a path segment (CUI, purchase order number, etc) for use in a URL path. 
    the purchase order number may contain special chars that need to be encoded, such as -, /, and spaces. this function will encode those chars so that the URL is valid.
    """
    return urllib.parse.quote(str(value), safe="")

def _retry_after(headers):
    """
    get the retry-after value from the headers, if present. if not present, return 1 second. if present but not a valid float, return 1 second.
    """
    value = headers.get("Retry-After") if headers else None # get the Retry-After header value, if present
    if value is None:
        return 1.0
    try:
        return max(0.0, float(value)) # return the value as a float, but not less than 0.0
    except ValueError:
        return 1.0 # return 1 second if the value is not a valid float

def _error_info(raw):
    """
    parse the error info from the raw response body. if the body is not valid JSON, return None for code and the first 200 chars of the raw response body as the message.
    error body format: {"error": {"code": "...", "message": "...", "details": {}}}
    """
    try:
        error = json.loads(raw).get("error") or {} # get the error object from the response body / empty dictionary if not present
        return error.get("code"), error.get("message", "")
    except (ValueError, AttributeError):
        return None, raw[:200].decode("utf-8", "replace") # return the first 200 chars of the raw response body if not valid JSON

class HTTPClient:
    def __init__(self, base_url, sleep=time.sleep):
        """
        initialize the HTTP client with the base URL and optional sleep for testing
        the base_url should not have a trailing slash, as the path will be appended to it. if it does have a trailing slash, it will be removed.
        """
        self.base_url = base_url.rstrip("/") # remove trailing slash from base_url
        self._sleep = sleep # allow sleep to be mocked for testing
    def get(self, path, params=None):
        """
        make a GET request to the given path with optional query parameters.
        """
        if params:
            path = f"{path}?{urllib.parse.urlencode(params)}" # append query parameters to the path if present
        return self.request("GET", path)

    def get_bytes(self, path):
        """
        make a GET request to the given path and return raw response (XML/PDF file) as bytes
        """
        return self.request("GET", path, as_bytes=True)

    def get_optional(self, path, params=None):
        """
        GET request that return None if the resource doesnt exist (404). any other error will raise an APIError

        """
        try:
            return self.get(path, params)
        except APIError as err:
            if err.status == NO_RESOURCE_STATUS_CODE:
                return None # return None if the resource does not exist
            raise # re-raise the exception for any other error

    def post(self, path, body, idem_key):
        """
        make a POST request to the given path with the given body and idem_key.
        """
        return self.request("POST", path, body=body, idem_key=idem_key)

    def request(self, method, path, body=None, idem_key=None, as_bytes=False):
        """
        make an HTTP request to the given path with the given method, body, and idem_key. retry on 5xx status codes, network errors, and timeouts. raise an APIError for other errors.
        """
        method = method.upper() # ensure the method is uppercase
        if method == "POST" and not idem_key:
            raise ValueError("idem_key is required for POST requests")

        url = self.base_url + path
        # encode the body as JSON if present, otherwise set data to None - body should be the same for every retry attempt, so we encode it once here and reuse it for each attempt
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"} # set the Accept header to application/json for all requests
        if data is not None:
            headers["Content-Type"] = "application/json"
        if idem_key:
            headers["Idempotency-Key"] = idem_key

        attempt = 1 # attempt counter for retry logic
        rate_waits = 0 # rate limit wait counter for retry logic
        statuses = [] # list of status codes received during retries

        while True:
            status, response_headers, raw_response = self._send(method, url, data, headers)
            if status is not None and 200 <= status < 300:
                if as_bytes:
                    return raw_response # return the raw response body as bytes if requested
                return json.loads(raw_response) if raw_response else None # return the parsed JSON response if present, otherwise return None 
            code, message = _error_info(raw_response) # extract the error code and message from the raw response body

            if status == RATE_LIMIT_STATUS_CODE:
                if code == "RUN_REQUEST_LIMIT":
                    raise RunRequestLimit(f"{method} {path} returned status {status} with code {code}: {message}")
                rate_waits += 1
                if rate_waits > MAX_RATE_WAITS:
                    raise APIError(method, path, RATE_LIMIT_STATUS_CODE, code, message, statuses, exhausted=True)
                wait = _retry_after(response_headers)
                log.warning("RETRY %s %s: 429 %s, waiting %.1fs (retry %d/%d, without consuming a retry attempt)", method, path, code, wait, rate_waits, MAX_RATE_WAITS)
                self._sleep(wait) # wait for the specified time before retrying
                continue # retry the request without consuming a retry attempt
            
            if status is None or status in RETRYABLE_STATUS_CODES:
                statuses.append(status) # append the status code to the list of statuses received during retries
                if attempt >= MAX_RETRY_ATTEMPTS:
                    raise APIError(method, path, status, code, message, statuses, exhausted=True)
                wait = _retry_after(response_headers) # wait for the specified time before retrying
                reason = "network error/timeout" if status is None else f"HTTP {status} {code}"
                log.warning("RETRY %s %s: %s, waiting %.1fs (retry %d/%d failed)", method, path, reason, wait, attempt, MAX_RETRY_ATTEMPTS)
                self._sleep(wait) # wait for the specified time before retrying
                attempt += 1
                continue

            # other status codes (4xx) are not retryable, so raise an APIError
            raise APIError(method, path, status, code, message, statuses)

    def _send(self, method, url, data, headers):
        """
        send the HTTP request using urllib.request and return the status code, headers, and raw response body.
        if the request fails due to a network error or timeout -> return None for the status code and headers, and empty bytes for the raw response body. this is to avoid consuming a retry attempt
        """
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=CLIENT_TIMEOUT) as response:
                return response.status, response.headers, response.read() # return the status code, headers, and raw response body
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read() # return the status code, headers, and raw response body for HTTP errors
        except (OSError, http.client.HTTPException): # URL errors, timeouts, and other network errors
            return None, None, b"" # return None for status and headers, and empty bytes for raw response body for network errors/timeouts