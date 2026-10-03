"""Cliente HTTP compartido con reintentos para las APIs científicas."""

import time

import httpx

USER_AGENT = "scientific-discovery-lab/0.1 (Hack-Nation x Databricks hackathon)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class Http:
    def __init__(self, timeout: float = 60.0, retries: int = 4, min_interval: float = 0.0):
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self.retries = retries
        self.min_interval = min_interval
        self._last = 0.0

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
            else:
                if resp.status_code not in RETRY_STATUS or attempt == self.retries:
                    resp.raise_for_status()
                    return resp
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")

    def get_json(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs).json()

    def post_json(self, url: str, payload: dict, **kwargs):
        return self.request("POST", url, json=payload, **kwargs).json()
