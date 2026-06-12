"""Lightweight HTTP Adapter for Supabase to avoid pyiceberg dependency issues.
Implements the subset of Supabase functionality needed for DepthAPI.
"""

from urllib.parse import quote, urlencode
from typing import Any, Dict, List, Optional, Union

import httpx
import structlog

logger = structlog.get_logger(__name__)

class SupabaseHTTPResponse:
    def __init__(self, data: Any, error: Optional[Any] = None):
        self.data = data
        self.error = error

class SupabaseHTTPClient:
    def __init__(self, url: str, key: str, is_admin: bool = False):
        self.url = url.rstrip("/")
        self.rest_url = f"{self.url}/rest/v1"
        self.key = key
        self.is_admin = is_admin
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def rpc(self, function_name: str, params: Dict[str, Any]) -> "SupabaseHTTPRPC":
        return SupabaseHTTPRPC(self, function_name, params)

    def table(self, table_name: str) -> "SupabaseHTTPTable":
        return SupabaseHTTPTable(self, table_name)

class SupabaseHTTPRPC:
    def __init__(self, client: SupabaseHTTPClient, name: str, params: Dict[str, Any]):
        self.client = client
        self.name = name
        self.params = params

    async def execute(self) -> SupabaseHTTPResponse:
        url = f"{self.client.rest_url}/rpc/{self.name}"
        async with httpx.AsyncClient(timeout=self.client.timeout) as http:
            response = await http.post(url, headers=self.client.headers, json=self.params)
            if response.status_code >= 400:
                logger.error("supabase_rpc_failed", status=response.status_code, body=response.text)
                return SupabaseHTTPResponse(None, _safe_json(response))
            return SupabaseHTTPResponse(_safe_json(response))

class SupabaseHTTPTable:
    def __init__(self, client: SupabaseHTTPClient, name: str):
        self.client = client
        self.name = name
        self.params: Dict[str, Any] = {}
        self.filters: List[tuple[str, str]] = []
        self._single = False

    def select(self, columns: str = "*") -> "SupabaseHTTPTable":
        self.params["select"] = columns
        return self

    def eq(self, column: str, value: Any) -> "SupabaseHTTPTable":
        self.filters.append((column, f"eq.{_encode_filter_value(value)}"))
        return self

    def is_(self, column: str, value: Any) -> "SupabaseHTTPTable":
        self.filters.append((column, f"is.{_encode_filter_value(value)}"))
        return self

    def in_(self, column: str, values: list[Any]) -> "SupabaseHTTPTable":
        encoded_vals = ",".join(_encode_filter_value(v) for v in values)
        self.filters.append((column, f"in.({encoded_vals})"))
        return self

    def order(self, column: str, desc: bool = False, nullsfirst: bool = False) -> "SupabaseHTTPTable":
        suffix = ".desc" if desc else ".asc"
        if nullsfirst:
            suffix += ".nullsfirst"
        self.params["order"] = f"{column}{suffix}"
        return self

    def limit(self, count: int) -> "SupabaseHTTPTable":
        self.params["limit"] = max(int(count), 0)
        return self

    def single(self) -> "SupabaseHTTPTable":
        # Request an object response in PostgREST-compatible format.
        self._single = True
        return self

    async def execute(self) -> SupabaseHTTPResponse:
        url = f"{self.client.rest_url}/{self.name}"
        merged_params_list = []
        for k, v in self.params.items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    merged_params_list.append((k, item))
            else:
                merged_params_list.append((k, v))
        merged_params_list.extend(self.filters)
        
        query_params = urlencode(merged_params_list, doseq=True)
        full_url = f"{url}?{query_params}" if query_params else url
        
        async with httpx.AsyncClient(timeout=self.client.timeout) as http:
            headers = self.client.headers.copy()
            if self._single:
                headers["Accept"] = "application/vnd.pgrst.object+json"
            response = await http.get(full_url, headers=headers)
            if response.status_code >= 400:
                return SupabaseHTTPResponse(None, _safe_json(response))
            
            data = _safe_json(response)
            # Handle .single() - if data is a list and we only want one
            if self._single and isinstance(data, list) and len(data) == 1:
                return SupabaseHTTPResponse(data[0])
            return SupabaseHTTPResponse(data)

    def update(self, values: Dict[str, Any]) -> "SupabaseHTTPTableUpdate":
        return SupabaseHTTPTableUpdate(self, values)

    def upsert(self, values: Union[Dict[str, Any], List[Dict[str, Any]]], on_conflict: Optional[str] = None) -> "SupabaseHTTPTableUpsert":
        return SupabaseHTTPTableUpsert(self, values, on_conflict)

    def insert(self, values: Union[Dict[str, Any], List[Dict[str, Any]]]) -> "SupabaseHTTPTableInsert":
        return SupabaseHTTPTableInsert(self, values)

    def delete(self) -> "SupabaseHTTPTableDelete":
        return SupabaseHTTPTableDelete(self)

class SupabaseHTTPTableUpdate:
    def __init__(self, table: SupabaseHTTPTable, values: Dict[str, Any]):
        self.table = table
        self.values = values

    def eq(self, column: str, value: Any) -> "SupabaseHTTPTableUpdate":
        self.table.eq(column, value)
        return self

    async def execute(self) -> SupabaseHTTPResponse:
        if not self.table.filters and not self.table.client.is_admin:
            raise ValueError("Update requires at least one filter to prevent accidental bulk updates")
        url = f"{self.table.client.rest_url}/{self.table.name}"
        query_params = urlencode(self.table.filters, doseq=True)
        full_url = f"{url}?{query_params}"
        
        async with httpx.AsyncClient(timeout=self.table.client.timeout) as http:
            response = await http.patch(full_url, headers=self.table.client.headers, json=self.values)
            if response.status_code >= 400:
                return SupabaseHTTPResponse(None, _safe_json(response))
            return SupabaseHTTPResponse(_safe_json(response))

class SupabaseHTTPTableUpsert:
    def __init__(self, table: SupabaseHTTPTable, values: Union[Dict[str, Any], List[Dict[str, Any]]], on_conflict: Optional[str] = None):
        self.table = table
        self.values = values
        self.on_conflict = on_conflict

    async def execute(self) -> SupabaseHTTPResponse:
        url = f"{self.table.client.rest_url}/{self.table.name}"
        headers = self.table.client.headers.copy()
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        if self.on_conflict:
            url = f"{url}?{urlencode({'on_conflict': self.on_conflict})}"

        async with httpx.AsyncClient(timeout=self.table.client.timeout) as http:
            response = await http.post(url, headers=headers, json=self.values)
            if response.status_code >= 400:
                return SupabaseHTTPResponse(None, _safe_json(response))
            return SupabaseHTTPResponse(_safe_json(response))


class SupabaseHTTPTableInsert:
    def __init__(self, table: SupabaseHTTPTable, values: Union[Dict[str, Any], List[Dict[str, Any]]]):
        self.table = table
        self.values = values

    async def execute(self) -> SupabaseHTTPResponse:
        url = f"{self.table.client.rest_url}/{self.table.name}"
        async with httpx.AsyncClient(timeout=self.table.client.timeout) as http:
            response = await http.post(url, headers=self.table.client.headers, json=self.values)
            if response.status_code >= 400:
                return SupabaseHTTPResponse(None, _safe_json(response))
            return SupabaseHTTPResponse(_safe_json(response))


class SupabaseHTTPTableDelete:
    def __init__(self, table: SupabaseHTTPTable):
        self.table = table

    def eq(self, column: str, value: Any) -> "SupabaseHTTPTableDelete":
        self.table.eq(column, value)
        return self

    async def execute(self) -> SupabaseHTTPResponse:
        if not self.table.filters and not self.table.client.is_admin:
            raise ValueError("Delete requires at least one filter to prevent accidental bulk deletes")
        url = f"{self.table.client.rest_url}/{self.table.name}"
        query_params = urlencode(self.table.filters)
        full_url = f"{url}?{query_params}" if query_params else url
        async with httpx.AsyncClient(timeout=self.table.client.timeout) as http:
            response = await http.delete(full_url, headers=self.table.client.headers)
            if response.status_code >= 400:
                return SupabaseHTTPResponse(None, _safe_json(response))
            return SupabaseHTTPResponse(_safe_json(response))


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def _encode_filter_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return quote(str(value), safe="")
