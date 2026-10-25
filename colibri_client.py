"""Cliente HTTP para a Colibri Cloud REST API (NCR Colibri).

Fonte da especificação: coleção Postman pública "Colibri Cloud API"
(https://documenter.getpostman.com/view/31362201/2s9YeHbBMM), resumida em
docs/colibri-api.md.

Autenticação (conforme a documentação):
  GET {base}/oauth/authenticate?client_id=<chave do aplicativo>
  -> {"expires_in": "5", "access_token": "...", "token_type": "Bearer"}
O token vale 5 minutos; este cliente o renova sozinho antes de expirar e
refaz a chamada uma vez se a API responder 401.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://cloud.ncrcolibri.com.br"

# Margem de segurança sobre a validade declarada (5 min) para nunca enviar
# um token prestes a expirar.
TOKEN_SAFETY_MARGIN_SECONDS = 30

HTTP_STATUS_REASONS = {
    400: "Requisição inválida",
    401: "Não autorizado, client_id não encontrado",
    403: "Proibido",
    404: "Não encontrado",
    500: "Erro interno do servidor",
}


class ColibriError(Exception):
    """Erro retornado pela API Colibri ou falha de transporte."""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _csv(value: Any) -> str | None:
    """Converte listas em 'a,b,c' (formato Array[...] da API)."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value if v is not None and str(v).strip() != ""]
        return ",".join(items) if items else None
    text = str(value).strip()
    return text or None


def build_query(params: dict[str, Any]) -> dict[str, str]:
    """Remove valores nulos/vazios e serializa listas e booleanos."""
    query: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            query[key] = "true" if value else "false"
            continue
        serialized = _csv(value)
        if serialized is not None:
            query[key] = serialized
    return query


class ColibriClient:
    """Cliente assíncrono com cache de token."""

    def __init__(
        self,
        client_id: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not client_id:
            raise ValueError("COLIBRI_CLIENT_ID vazio")
        self._client_id = client_id
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "camelo-colibri-mcp/1.0"},
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ token
    async def get_token(self, force: bool = False) -> str:
        async with self._token_lock:
            now = time.monotonic()
            if not force and self._token and now < self._token_expires_at:
                return self._token
            try:
                response = await self._http.get(
                    "/oauth/authenticate", params={"client_id": self._client_id}
                )
            except httpx.HTTPError as exc:
                raise ColibriError(f"Falha de rede ao autenticar no Colibri: {exc}") from exc
            if response.status_code != 200:
                raise ColibriError(
                    f"Autenticação Colibri falhou (HTTP {response.status_code}: "
                    f"{HTTP_STATUS_REASONS.get(response.status_code, 'erro')}).",
                    status_code=response.status_code,
                    body=response.text[:500],
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise ColibriError(
                    "Resposta de autenticação Colibri não é JSON", body=response.text[:500]
                ) from exc
            token = data.get("access_token")
            if not token:
                raise ColibriError(
                    "Resposta de autenticação Colibri sem access_token", body=response.text[:500]
                )
            # expires_in vem em MINUTOS ("5") na documentação.
            try:
                expires_minutes = float(data.get("expires_in", 5))
            except (TypeError, ValueError):
                expires_minutes = 5.0
            ttl = max(expires_minutes * 60 - TOKEN_SAFETY_MARGIN_SECONDS, 30)
            self._token = token
            self._token_expires_at = time.monotonic() + ttl
            return token

    # --------------------------------------------------------------- requests
    async def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
    ) -> Any:
        """Executa uma chamada autenticada e devolve o JSON (ou texto) de resposta."""
        method = method.upper()
        if not path.startswith("/"):
            path = "/" + path
        params = build_query(query or {})
        token = await self.get_token()
        response = await self._send(method, path, params, body, token)
        if response.status_code == 401:
            # Token pode ter sido invalidado antes do previsto: renova e tenta 1x.
            token = await self.get_token(force=True)
            response = await self._send(method, path, params, body, token)
        if response.status_code >= 400:
            reason = HTTP_STATUS_REASONS.get(response.status_code, "erro")
            raise ColibriError(
                f"Colibri respondeu HTTP {response.status_code} ({reason}) em {method} {path}",
                status_code=response.status_code,
                body=response.text[:1000],
            )
        if response.status_code == 204 or not response.content:
            return {"status": response.status_code, "data": None}
        try:
            return response.json()
        except ValueError:
            return {"status": response.status_code, "text": response.text}

    async def _send(
        self, method: str, path: str, params: dict[str, str], body: Any, token: str
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"}
        kwargs: dict[str, Any] = {"params": params, "headers": headers}
        if body is not None and method in {"POST", "PUT", "PATCH"}:
            headers["Content-Type"] = "application/json; charset=utf-8"
            kwargs["json"] = body
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ColibriError(f"Falha de rede ao chamar Colibri {method} {path}: {exc}") from exc

    # ------------------------------------------------------------- paginação
    async def get_paginated(
        self,
        path: str,
        query: dict[str, Any],
        todas_paginas: bool = False,
        max_paginas: int = 50,
    ) -> dict[str, Any]:
        """Consulta paginada (totalItens/totalPaginas/paginaAtual/proximaPagina/data).

        Com todas_paginas=True percorre as páginas a partir de `pagina` até
        `max_paginas`, concatenando `data`.
        """
        pagina = int(query.get("pagina") or 1)
        first = await self.request("GET", path, {**query, "pagina": pagina})
        if not todas_paginas or not isinstance(first, dict):
            return first
        data = list(first.get("data") or [])
        total_paginas = int(first.get("totalPaginas") or 1)
        paginas_lidas = 1
        proxima = first.get("proximaPagina")
        while proxima and paginas_lidas < max_paginas and pagina < total_paginas:
            pagina = int(proxima)
            page = await self.request("GET", path, {**query, "pagina": pagina})
            if not isinstance(page, dict):
                break
            data.extend(page.get("data") or [])
            paginas_lidas += 1
            proxima = page.get("proximaPagina")
        return {
            "totalItens": first.get("totalItens"),
            "totalPaginas": total_paginas,
            "paginasLidas": paginas_lidas,
            "paginacaoCompleta": not proxima or pagina >= total_paginas,
            "data": data,
        }
