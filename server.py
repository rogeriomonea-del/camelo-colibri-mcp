"""camelo-colibri-mcp — servidor MCP remoto para a API Colibri Cloud (NCR).

Expõe as consultas e o catálogo do Colibri Cloud como ferramentas MCP,
protegidas por login Google (OAuth 2.1 + PKCE, DCR e CIMD via FastMCP
OAuthProxy) e restritas aos e-mails listados em OWNER_EMAILS.

Clientes compatíveis: Claude.ai / Claude Desktop / Cowork, Claude Code,
ChatGPT (developer mode) e Codex CLI — todos via a mesma URL pública
`{PUBLIC_URL}/mcp`.

Variáveis de ambiente (ver .env.example):
  PUBLIC_URL             URL pública HTTPS do servidor (ex.: https://colibri.celestiaflights.com)
  OWNER_EMAILS           e-mails Google autorizados, separados por vírgula
  GOOGLE_CLIENT_ID       OAuth client (Web application) do Google Cloud
  GOOGLE_CLIENT_SECRET   segredo do OAuth client
  COLIBRI_CLIENT_ID      chave do aplicativo registrado no Colibri Cloud
  COLIBRI_BASE_URL       (opcional) padrão https://cloud.ncrcolibri.com.br
  COLIBRI_ALLOW_WRITES   (opcional) "true" habilita POST/PUT/DELETE no catálogo
  COLIBRI_MAX_PAGINAS    (opcional) limite de páginas por consulta com todas_paginas (padrão 50)
  MCP_STATIC_TOKEN       (opcional) bearer fixo para clientes sem OAuth (n8n, scripts)
  JWT_SIGNING_KEY        (opcional) chave para assinar os tokens emitidos pelo servidor
  MCP_PATH, HOST, PORT   (opcionais) padrão /mcp, 0.0.0.0, 8000
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from colibri_client import DEFAULT_BASE_URL, ColibriClient, ColibriError

__version__ = "1.0.0"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("camelo-colibri-mcp")

BASE_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- config
def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "sim", "on"}


def _owner_emails() -> set[str]:
    raw = _env("OWNER_EMAILS", "") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


PUBLIC_URL = (_env("PUBLIC_URL", "http://localhost:8000") or "").rstrip("/")
MCP_PATH = _env("MCP_PATH", "/mcp") or "/mcp"
OWNER_EMAILS = _owner_emails()
COLIBRI_BASE_URL = _env("COLIBRI_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
COLIBRI_ALLOW_WRITES = _env_bool("COLIBRI_ALLOW_WRITES", False)
COLIBRI_MAX_PAGINAS = int(_env("COLIBRI_MAX_PAGINAS", "50") or 50)
MCP_STATIC_TOKEN = _env("MCP_STATIC_TOKEN")


# ----------------------------------------------------------------------------- auth
class OwnerOnlyVerifier(TokenVerifier):
    """Envolve o verificador do Google e só aceita os e-mails autorizados."""

    def __init__(self, inner: TokenVerifier, owner_emails: set[str]):
        super().__init__(required_scopes=inner.required_scopes)
        self._inner = inner
        self._owner_emails = owner_emails

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await self._inner.verify_token(token)
        if access is None:
            return None
        email = str(access.claims.get("email") or "").strip().lower()
        if email not in self._owner_emails:
            logger.warning("Acesso negado: conta Google não autorizada (%s)", email or "sem e-mail")
            return None
        return access


class CameloGoogleAuth(GoogleProvider):
    """GoogleProvider com restrição por e-mail e bearer fixo opcional."""

    def __init__(self, *, owner_emails: set[str], static_token: str | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._token_validator = OwnerOnlyVerifier(self._token_validator, owner_emails)
        self._static_token = static_token
        self._static_identity = sorted(owner_emails)[0] if owner_emails else "static-token"

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        if self._static_token and secrets.compare_digest(token, self._static_token):
            return AccessToken(
                token=token,
                client_id="static-token",
                scopes=list(self.required_scopes or []),
                expires_at=None,
                subject="static-token",
                claims={"email": self._static_identity, "auth_mode": "static_token"},
            )
        return await super().load_access_token(token)


def build_auth() -> CameloGoogleAuth | None:
    """Monta o provedor de autenticação; None só quando MCP_AUTH_DISABLED=true (testes)."""
    if _env_bool("MCP_AUTH_DISABLED", False):
        logger.warning("MCP_AUTH_DISABLED=true — servidor SEM autenticação (apenas testes locais)")
        return None
    client_id = _env("GOOGLE_CLIENT_ID")
    client_secret = _env("GOOGLE_CLIENT_SECRET")
    missing = [
        name
        for name, value in {
            "GOOGLE_CLIENT_ID": client_id,
            "GOOGLE_CLIENT_SECRET": client_secret,
            "OWNER_EMAILS": ",".join(sorted(OWNER_EMAILS)) or None,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Variáveis obrigatórias ausentes: {', '.join(missing)}")
    if not PUBLIC_URL.startswith("https://"):
        logger.warning("PUBLIC_URL sem https (%s): só aceitável em testes locais", PUBLIC_URL)
    jwt_key = _env("JWT_SIGNING_KEY")
    return CameloGoogleAuth(
        owner_emails=OWNER_EMAILS,
        static_token=MCP_STATIC_TOKEN,
        client_id=client_id,
        client_secret=client_secret,
        base_url=PUBLIC_URL,
        required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
        # Evita consultar o tokeninfo do Google a cada chamada de ferramenta.
        cache_ttl_seconds=300,
        # Renova o token upstream 60 s antes de expirar.
        token_expiry_threshold_seconds=60,
        jwt_signing_key=jwt_key,
        extra_authorize_params={"prompt": "select_account consent", "access_type": "offline"},
    )


# --------------------------------------------------------------------------- server
INSTRUCTIONS = """Servidor MCP da Pizzaria Camelo para a API Colibri Cloud (NCR Colibri — PDV).
Uso recomendado:
1. Chame `listar_redes_lojas` para obter idRede/idLoja; use-os nos filtros `redes`/`lojas`.
2. Datas sempre no formato yyyy-MM-dd; `dtinicio` e `dtfim` são obrigatórios nas consultas de movimento.
3. Antes de somar vendas de um dia, confira `status_periodo` — garante que os dados do dia já subiram para a nuvem.
4. Consultas são paginadas (totalItens/totalPaginas/proximaPagina); use `todas_paginas=true` para consolidar.
5. `comprovantes` = cupons/NF com meios de pagamento; `itens_venda` = itens vendidos (com cancelamentos);
   `movimento_caixa` = totais por caixa/atendente; `mapa_ecf` = redução Z; `turnos` = turnos dos operadores.
6. Catálogo (`catalogo_listar`/`catalogo_obter`) cobre grupos, materiais, combos, classes, observações,
   motivos de cancelamento, tabelas de preço, locais de produção e meios de pagamento.
Escrita no catálogo fica desabilitada por padrão (COLIBRI_ALLOW_WRITES).
"""

mcp = FastMCP(
    name="Camelo Colibri",
    instructions=INSTRUCTIONS,
    version=__version__,
    auth=build_auth(),
)

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}

_client: ColibriClient | None = None


def colibri() -> ColibriClient:
    global _client
    if _client is None:
        client_id = _env("COLIBRI_CLIENT_ID")
        if not client_id:
            raise ToolError("COLIBRI_CLIENT_ID não configurado no servidor.")
        _client = ColibriClient(client_id=client_id, base_url=COLIBRI_BASE_URL)
    return _client


def set_client(client: ColibriClient | None) -> None:
    """Usado pelos testes para injetar um cliente com transporte simulado."""
    global _client
    _client = client


async def call(method: str, path: str, query: dict[str, Any] | None = None, body: Any = None) -> Any:
    try:
        return await colibri().request(method, path, query=query, body=body)
    except ColibriError as exc:
        detail = f" Resposta: {exc.body}" if exc.body else ""
        raise ToolError(f"{exc}.{detail}") from exc


async def paginated(path: str, query: dict[str, Any], todas_paginas: bool) -> Any:
    try:
        return await colibri().get_paginated(
            path, query, todas_paginas=todas_paginas, max_paginas=COLIBRI_MAX_PAGINAS
        )
    except ColibriError as exc:
        detail = f" Resposta: {exc.body}" if exc.body else ""
        raise ToolError(f"{exc}.{detail}") from exc


# ------------------------------------------------------------------------ tipos
Data = Annotated[str, Field(description="Data no formato yyyy-MM-dd", pattern=r"^\d{4}-\d{2}-\d{2}$")]
Redes = Annotated[list[int] | None, Field(description="IDs das redes (ver listar_redes_lojas)")]
Lojas = Annotated[list[int] | None, Field(description="IDs das lojas (ver listar_redes_lojas)")]
Pagina = Annotated[int, Field(description="Página inicial (1 = primeira)", ge=1)]
TodasPaginas = Annotated[
    bool,
    Field(description="true = percorre todas as páginas a partir de `pagina` e concatena `data`"),
]

CatalogoRecurso = Literal[
    "grupo-material",
    "grupo-material/tipos",
    "material",
    "combo",
    "combo/precificador",
    "combo/quantificador",
    "combo/slot",
    "classe",
    "classe/item",
    "observacao",
    "observacao/material-observacao",
    "motivo-cancelamento",
    "tabela-preco",
    "tabela-preco/item-preco",
    "local-producao",
    "meio-pagamento",
    "meio-pagamento/tipos-tef",
]


# ------------------------------------------------------------------- consultas
@mcp.tool(annotations=READ_ONLY)
async def listar_redes_lojas() -> Any:
    """Lista redes e lojas cadastradas (idRede, nome, lojas[idLoja, nome]).

    Use os IDs retornados nos filtros `redes` e `lojas` das demais consultas.
    """
    return await call("GET", "/api/v1/rede-loja")


@mcp.tool(annotations=READ_ONLY)
async def status_periodo(
    dtinicio: Data,
    dtfim: Data,
    redes: Redes = None,
    lojas: Lojas = None,
    pagina: Pagina = 1,
    todas_paginas: TodasPaginas = False,
) -> Any:
    """Status do período contábil por loja/dia (ex.: sem_movimento, fechado).

    Boa prática da documentação: consultar antes das demais consultas para
    confirmar que todos os dados do dia já subiram ao Colibri Cloud.
    """
    return await paginated(
        "/api/v1/status-periodo",
        {"redes": redes, "lojas": lojas, "dtinicio": dtinicio, "dtfim": dtfim, "pagina": pagina},
        todas_paginas,
    )


@mcp.tool(annotations=READ_ONLY)
async def turnos(
    dtinicio: Data,
    dtfim: Data,
    redes: Redes = None,
    lojas: Lojas = None,
    pagina: Pagina = 1,
    todas_paginas: TodasPaginas = False,
) -> Any:
    """Turnos dos operadores de caixa (abertura, fechamento, conferências por meio de pagamento)."""
    return await paginated(
        "/api/v1/turno",
        {"redes": redes, "lojas": lojas, "dtinicio": dtinicio, "dtfim": dtfim, "pagina": pagina},
        todas_paginas,
    )


@mcp.tool(annotations=READ_ONLY)
async def movimento_caixa(
    dtinicio: Data,
    dtfim: Data,
    redes: Redes = None,
    lojas: Lojas = None,
    modosvenda: Annotated[
        list[int] | None, Field(description="IDs dos modos de venda (ver modos_venda)")
    ] = None,
    tipos: Annotated[
        list[int] | None,
        Field(
            description="Tipos de movimento: 1 venda, 2 conta assinada, 3 sangria, 4 suprimento. "
            "Sem o parâmetro a API retorna apenas vendas."
        ),
    ] = None,
    pagina: Pagina = 1,
    todas_paginas: TodasPaginas = False,
) -> Any:
    """Movimento de caixa consolidado (totais por loja/hora/atendente: descontos, acréscimos, recebidos, serviço)."""
    return await paginated(
        "/api/v1/movimentocaixa",
        {
            "redes": redes,
            "lojas": lojas,
            "dtinicio": dtinicio,
            "dtfim": dtfim,
            "modosvenda": modosvenda,
            "tipos": tipos,
            "pagina": pagina,
        },
        todas_paginas,
    )


@mcp.tool(annotations=READ_ONLY)
async def comprovantes(
    dtinicio: Data,
    dtfim: Data,
    redes: Redes = None,
    lojas: Lojas = None,
    cancelados: Annotated[bool | None, Field(description="Incluir itens cancelados")] = None,
    consumidores: Annotated[
        list[str] | None, Field(description="CPF/CNPJ dos consumidores (até 30 caracteres cada)")
    ] = None,
    denominacao: Annotated[str | None, Field(description="Denominação do documento (até 5 caracteres, ex.: NF)")] = None,
    usarDtMov: Annotated[
        bool | None,
        Field(description="true = filtra pela data do movimento; padrão filtra pela data contábil"),
    ] = None,
    pagina: Pagina = 1,
    todas_paginas: TodasPaginas = False,
) -> Any:
    """Comprovantes (cupons/NF) com itens (detalhes) e meios de pagamento (meios) por período."""
    return await paginated(
        "/api/v1/comprovante",
        {
            "redes": redes,
            "lojas": lojas,
            "dtinicio": dtinicio,
            "dtfim": dtfim,
            "cancelados": cancelados,
            "consumidores": consumidores,
            "denominacao": denominacao,
            "usarDtMov": usarDtMov,
            "pagina": pagina,
        },
        todas_paginas,
    )


@mcp.tool(annotations=READ_ONLY)
async def itens_venda(
    dtinicio: Data,
    dtfim: Data,
    redes: Redes = None,
    lojas: Lojas = None,
    cancelados: Annotated[bool | None, Field(description="Incluir itens cancelados")] = None,
    consumidores: Annotated[list[str] | None, Field(description="CPF/CNPJ dos consumidores")] = None,
    modosvenda: Annotated[
        list[int] | None, Field(description="IDs dos modos de venda (ver modos_venda)")
    ] = None,
    pagina: Pagina = 1,
    todas_paginas: TodasPaginas = False,
) -> Any:
    """Itens vendidos por período (produto, grupo, quantidade, valores, atendente, modo de venda, cancelamentos)."""
    return await paginated(
        "/api/v1/itemvenda",
        {
            "redes": redes,
            "lojas": lojas,
            "dtinicio": dtinicio,
            "dtfim": dtfim,
            "cancelados": cancelados,
            "consumidores": consumidores,
            "modosvenda": modosvenda,
            "pagina": pagina,
        },
        todas_paginas,
    )


@mcp.tool(annotations=READ_ONLY)
async def mapa_ecf(
    dtinicio: Data,
    dtfim: Data,
    redes: Redes = None,
    lojas: Lojas = None,
    pagina: Pagina = 1,
    todas_paginas: TodasPaginas = False,
) -> Any:
    """Mapa ECF / redução Z por loja e dia (totalizadores fiscais, venda bruta)."""
    return await paginated(
        "/api/v1/mapaEcf",
        {"redes": redes, "lojas": lojas, "dtinicio": dtinicio, "dtfim": dtfim, "pagina": pagina},
        todas_paginas,
    )


@mcp.tool(annotations=READ_ONLY)
async def modos_venda() -> Any:
    """Modos de venda disponíveis (ex.: BALCAO, ENTREGA, MESA, FICHA) com seus IDs."""
    return await call("GET", "/api/v1/modo-venda")


# -------------------------------------------------------------------- catálogo
@mcp.tool(annotations=READ_ONLY)
async def catalogo_listar(
    recurso: Annotated[CatalogoRecurso, Field(description="Recurso do catálogo a listar")],
    rede_id: Annotated[int | None, Field(description="ID da rede (obrigatório na maioria das listas)")] = None,
    loja_id: Annotated[int | None, Field(description="ID da loja (material) ou `lojas` (tabela-preco)")] = None,
    filtros: Annotated[
        dict[str, str | int | bool] | None,
        Field(
            description="Filtros extras por recurso. material: codigo, descricao, grupo, local_producao_id, "
            "page, limit, o (ordenação, ex.: -descricao). classe: filtro, o. classe/item: classe, material_item. "
            "observacao/material-observacao: ids, material, combo. tabela-preco: lojas. "
            "tabela-preco/item-preco: tabela_id, material_id."
        ),
    ] = None,
) -> Any:
    """Lista um recurso do catálogo do Colibri Cloud (GET /api/v1/catalogo/<recurso>/)."""
    query: dict[str, Any] = {}
    if rede_id is not None:
        query["rede_id"] = rede_id
    if loja_id is not None:
        query["lojas" if recurso == "tabela-preco" else "loja_id"] = loja_id
    if filtros:
        query.update(filtros)
    return await call("GET", f"/api/v1/catalogo/{recurso}/", query)


@mcp.tool(annotations=READ_ONLY)
async def catalogo_obter(
    recurso: Annotated[CatalogoRecurso, Field(description="Recurso do catálogo")],
    id: Annotated[int, Field(description="ID do registro")],
) -> Any:
    """Obtém um registro do catálogo pelo ID (GET /api/v1/catalogo/<recurso>/<id>/)."""
    return await call("GET", f"/api/v1/catalogo/{recurso}/{id}/")


# ----------------------------------------------------------------- genéricos
def _check_path(path: str) -> str:
    path = path.strip()
    if not path.startswith("/api/"):
        raise ToolError("O caminho deve começar com /api/ (ex.: /api/v1/comprovante).")
    return path


@mcp.tool(annotations=READ_ONLY)
async def colibri_get(
    path: Annotated[str, Field(description="Caminho da API, ex.: /api/v1/catalogo/material/")],
    query: Annotated[dict[str, str | int | bool] | None, Field(description="Parâmetros de consulta")] = None,
) -> Any:
    """GET genérico em qualquer endpoint documentado da API Colibri (ver resource colibri://docs/api)."""
    return await call("GET", _check_path(path), query)


@mcp.tool(annotations=WRITE)
async def colibri_escrever(
    method: Annotated[Literal["POST", "PUT", "DELETE"], Field(description="Método HTTP")],
    path: Annotated[str, Field(description="Caminho da API, ex.: /api/v1/catalogo/material/")],
    body: Annotated[dict[str, Any] | None, Field(description="Corpo JSON (POST/PUT)")] = None,
    query: Annotated[dict[str, str | int | bool] | None, Field(description="Parâmetros de consulta")] = None,
) -> Any:
    """Escrita no catálogo (POST/PUT/DELETE). Exige COLIBRI_ALLOW_WRITES=true no servidor.

    Altera dados de produção do PDV: use somente com confirmação explícita.
    """
    if not COLIBRI_ALLOW_WRITES:
        raise ToolError(
            "Escrita desabilitada neste servidor (COLIBRI_ALLOW_WRITES=false). "
            "Habilite a variável no deploy para permitir POST/PUT/DELETE."
        )
    return await call(method, _check_path(path), query, body)


# --------------------------------------------------------------- diagnóstico
@mcp.tool(annotations=READ_ONLY)
async def diagnostico() -> dict[str, Any]:
    """Verifica a sessão (quem está autenticado) e se o servidor consegue obter token no Colibri."""
    token = get_access_token()
    claims = dict(token.claims) if token else {}
    result: dict[str, Any] = {
        "versao": __version__,
        "usuario": claims.get("email"),
        "auth_mode": claims.get("auth_mode", "google_oauth" if token else "sem_auth"),
        "colibri_base_url": COLIBRI_BASE_URL,
        "escrita_habilitada": COLIBRI_ALLOW_WRITES,
        "max_paginas": COLIBRI_MAX_PAGINAS,
    }
    try:
        await colibri().get_token(force=True)
        result["colibri_autenticacao"] = "ok"
    except (ColibriError, ToolError) as exc:
        result["colibri_autenticacao"] = f"falhou: {exc}"
    return result


@mcp.resource("colibri://docs/api", mime_type="text/markdown")
def docs_api() -> str:
    """Referência resumida da Colibri Cloud REST API (extraída da coleção Postman oficial)."""
    doc = BASE_DIR / "docs" / "colibri-api.md"
    return doc.read_text(encoding="utf-8") if doc.exists() else "Documentação não encontrada."


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "camelo-colibri-mcp", "version": __version__})


_PAGE_STYLE = (
    "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:3rem auto;padding:0 1rem;"
    "line-height:1.5;color:#222}code{background:#f3f3f3;padding:.1em .3em}</style>"
)


@mcp.custom_route("/", methods=["GET"], include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<title>Camelo Colibri MCP</title>{_PAGE_STYLE}</head><body>"
        "<h1>Camelo Colibri MCP</h1>"
        "<p>Servidor MCP privado da Pizzaria Camelo que integra a API Colibri Cloud (NCR) "
        "aos assistentes do proprietário (Claude, ChatGPT, Codex). Acesso restrito por login Google.</p>"
        f"<p>Endpoint MCP: <code>{PUBLIC_URL}{MCP_PATH}</code></p>"
        "<p><a href='/privacidade'>Política de privacidade</a> · <a href='/termos'>Termos de uso</a></p>"
        "</body></html>"
    )


@mcp.custom_route("/privacidade", methods=["GET"], include_in_schema=False)
async def privacidade(request: Request) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<title>Política de privacidade</title>{_PAGE_STYLE}</head><body>"
        "<h1>Política de privacidade</h1>"
        "<p>Este serviço é de uso pessoal e restrito ao proprietário. O login Google é usado apenas para "
        "confirmar a identidade (nome e e-mail da conta) e autorizar o acesso. Nenhum dado da conta Google "
        "é compartilhado com terceiros nem usado para qualquer outra finalidade.</p>"
        "<p>Os tokens de acesso ficam armazenados de forma criptografada no servidor e podem ser revogados a "
        "qualquer momento em <a href='https://myaccount.google.com/permissions'>myaccount.google.com/permissions</a>.</p>"
        "<p>Contato: o endereço de suporte informado na tela de consentimento do Google.</p>"
        "</body></html>"
    )


@mcp.custom_route("/termos", methods=["GET"], include_in_schema=False)
async def termos(request: Request) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<title>Termos de uso</title>{_PAGE_STYLE}</head><body>"
        "<h1>Termos de uso</h1>"
        "<p>Serviço privado, sem garantia, destinado exclusivamente ao proprietário e às contas que ele "
        "autorizar. Qualquer outro acesso é negado.</p>"
        "</body></html>"
    )


if __name__ == "__main__":
    host = _env("HOST", "0.0.0.0") or "0.0.0.0"
    port = int(_env("PORT", "8000") or 8000)
    logger.info("camelo-colibri-mcp %s em %s%s (Colibri: %s)", __version__, PUBLIC_URL, MCP_PATH, COLIBRI_BASE_URL)
    try:
        mcp.run(transport="http", host=host, port=port, path=MCP_PATH, stateless_http=True)
    except KeyboardInterrupt:
        sys.exit(0)
