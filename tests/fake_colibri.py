"""Simulação da Colibri Cloud API para testes (httpx.MockTransport)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx


class FakeColibri:
    def __init__(self, client_id: str = "chave-de-teste", page_size: int = 2, total_items: int = 5):
        self.client_id = client_id
        self.page_size = page_size
        self.total_items = total_items
        self.tokens_issued = 0
        self.valid_tokens: set[str] = set()
        self.calls: list[tuple[str, str, dict[str, list[str]]]] = []
        self.revoke_next = False  # simula token invalidado pelo servidor
        self.last_body = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = parse_qs(request.url.query.decode())
        self.calls.append((request.method, path, query))

        if path == "/oauth/authenticate":
            if query.get("client_id", [None])[0] != self.client_id:
                return httpx.Response(401, json={"message": "client_id não encontrado"})
            self.tokens_issued += 1
            token = f"tok-{self.tokens_issued}"
            self.valid_tokens.add(token)
            return httpx.Response(
                200, json={"expires_in": "5", "access_token": token, "token_type": "Bearer"}
            )

        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if self.revoke_next:
            self.revoke_next = False
            self.valid_tokens.discard(token)
        if token not in self.valid_tokens:
            return httpx.Response(401, json={"message": "Não autorizado"})

        if path == "/api/v1/rede-loja":
            return httpx.Response(
                200,
                json=[{"idRede": 1, "nome": "Camelo", "idRedePai": None, "lojas": [{"idLoja": 11, "nome": "Moema"}]}],
            )
        if path in {"/api/v1/comprovante", "/api/v1/itemvenda", "/api/v1/status-periodo"}:
            return self._paginated(query)
        if path == "/api/v1/catalogo/material/":
            return httpx.Response(200, json=[{"id": 1, "descricao": "Pizza Margherita", "query": query}])
        if path == "/api/v1/catalogo/material/7/":
            if request.method == "GET":
                return httpx.Response(200, json={"id": 7, "descricao": "Calabresa"})
            if request.method == "DELETE":
                return httpx.Response(204)
        if path == "/api/v1/catalogo/grupo-material/" and request.method == "POST":
            self.last_body = json.loads(request.content.decode())
            return httpx.Response(201, json={"id": 3, **self.last_body})
        if path == "/api/v1/erro":
            return httpx.Response(500, text="boom")
        return httpx.Response(404, json={"message": "Não encontrado"})

    def _paginated(self, query: dict[str, list[str]]) -> httpx.Response:
        pagina = int(query.get("pagina", ["1"])[0])
        total_paginas = -(-self.total_items // self.page_size)
        start = (pagina - 1) * self.page_size
        items = [
            {"id": i + 1, "lojas": query.get("lojas", [None])[0], "dtinicio": query.get("dtinicio", [None])[0]}
            for i in range(start, min(start + self.page_size, self.total_items))
        ]
        return httpx.Response(
            200,
            json={
                "totalItens": self.total_items,
                "totalItensPagina": len(items),
                "totalPaginas": total_paginas,
                "paginaAtual": pagina,
                "paginaAnterior": pagina - 1 if pagina > 1 else None,
                "proximaPagina": pagina + 1 if pagina < total_paginas else None,
                "data": items,
            },
        )
