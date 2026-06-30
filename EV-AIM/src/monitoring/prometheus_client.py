from __future__ import annotations

import os
from typing import Any, Dict, List
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth


class PublicPrometheusClient:
    """Client for colleague's public authenticated Prometheus API.

    Expected env vars:
      BASE_URL=http(s)://host
      BASIC_AUTH_USER=...
      BASIC_AUTH_PASSWORD=...
    """

    def __init__(
        self,
        base_url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = (base_url or os.environ["BASE_URL"]).rstrip("/")
        self.auth = HTTPBasicAuth(
            user or os.environ["BASIC_AUTH_USER"],
            password or os.environ["BASIC_AUTH_PASSWORD"],
        )
        self.timeout_seconds = timeout_seconds

    def query(self, query: str) -> List[Dict[str, Any]]:
        encoded_query = quote(query)
        url = f"{self.base_url}/prometheus/api/v1/query?query={encoded_query}"
        response = requests.get(url, auth=self.auth, timeout=self.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus instant query failed: {data}")
        return data.get("data", {}).get("result", [])

    def query_range(
        self,
        query: str,
        start: float,
        end: float,
        step: str,
    ) -> List[Dict[str, Any]]:
        encoded_query = quote(query)
        url = (
            f"{self.base_url}/prometheus/api/v1/query_range"
            f"?query={encoded_query}"
            f"&start={start}"
            f"&end={end}"
            f"&step={step}"
        )
        response = requests.get(url, auth=self.auth, timeout=self.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus range query failed: {data}")
        return data.get("data", {}).get("result", [])
