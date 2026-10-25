import json

import pytest
from fastmcp import Client

import server
from colibri_client import ColibriClient, ColibriError, build_query
from tests.fake_colibri import FakeColibri


@pytest.fixture
def fake():
    fake = FakeColibri()
    server.set_client(ColibriClient("chave-de-teste", base_url="https://colibri.test", transport=fake.transport()))
    yield fake
    server.set_client(None)


def result_json(result):
    return json.loads(result.content[0].text)


# ------------------------------------------------------------------ cliente HTTP
def test_build_query_serializa_listas_booleanos_e_remove_nulos():
    assert build_query({"lojas": [11, 12], "cancelados": True, "x": None, "y": "", "pagina": 1}) == {
        "lojas": "11,12",
        "cancelados": "true",
        "pagina": "1",
    }


async def test_token_e_reutilizado_entre_chamadas(fake):
    client = ColibriClient("chave-de-teste", base_url="https://colibri.test", transport=fake.transport())
    await client.request("GET", "/api/v1/rede-loja")
    await client.request("GET", "/api/v1/rede-loja")
    assert fake.tokens_issued == 1


async def test_renova_token_e_repete_uma_vez_apos_401(fake):
    client = ColibriClient("chave-de-teste", base_url="https://colibri.test", transport=fake.transport())
    await client.request("GET", "/api/v1/rede-loja")
    fake.revoke_next = True
    data = await client.request("GET", "/api/v1/rede-loja")
    assert data[0]["idRede"] == 1
    assert fake.tokens_issued == 2


async def test_client_id_invalido_gera_erro_claro():
    fake = FakeColibri(client_id="outra")
    client = ColibriClient("chave-errada", base_url="https://colibri.test", transport=fake.transport())
    with pytest.raises(ColibriError) as exc:
        await client.request("GET", "/api/v1/rede-loja")
    assert exc.value.status_code == 401


async def test_paginacao_completa(fake):
    client = ColibriClient("chave-de-teste", base_url="https://colibri.test", transport=fake.transport())
    result = await client.get_paginated(
        "/api/v1/comprovante", {"dtinicio": "2026-10-01", "dtfim": "2026-10-01"}, todas_paginas=True
    )
    assert [item["id"] for item in result["data"]] == [1, 2, 3, 4, 5]
    assert result["paginasLidas"] == 3 and result["paginacaoCompleta"] is True


async def test_paginacao_respeita_limite(fake):
    client = ColibriClient("chave-de-teste", base_url="https://colibri.test", transport=fake.transport())
    result = await client.get_paginated(
        "/api/v1/comprovante", {"dtinicio": "2026-10-01", "dtfim": "2026-10-01"}, todas_paginas=True, max_paginas=2
    )
    assert len(result["data"]) == 4 and result["paginacaoCompleta"] is False


# ------------------------------------------------------------------- ferramentas
async def test_listar_redes_lojas(fake):
    async with Client(server.mcp) as c:
        data = result_json(await c.call_tool("listar_redes_lojas", {}))
    assert data[0]["lojas"][0]["idLoja"] == 11


async def test_comprovantes_envia_filtros_no_formato_da_api(fake):
    async with Client(server.mcp) as c:
        data = result_json(
            await c.call_tool(
                "comprovantes",
                {"dtinicio": "2026-10-01", "dtfim": "2026-10-02", "lojas": [11, 12], "cancelados": True, "pagina": 2},
            )
        )
    assert data["paginaAtual"] == 2
    method, path, query = [c for c in fake.calls if c[1] == "/api/v1/comprovante"][0]
    assert query == {
        "lojas": ["11,12"],
        "dtinicio": ["2026-10-01"],
        "dtfim": ["2026-10-02"],
        "cancelados": ["true"],
        "pagina": ["2"],
    }


async def test_data_invalida_e_rejeitada_antes_de_chamar_a_api(fake):
    async with Client(server.mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("itens_venda", {"dtinicio": "01/10/2026", "dtfim": "2026-10-02"})
    assert not [c for c in fake.calls if c[1] == "/api/v1/itemvenda"]


async def test_todas_paginas_consolida(fake):
    async with Client(server.mcp) as c:
        data = result_json(
            await c.call_tool(
                "status_periodo", {"dtinicio": "2026-10-01", "dtfim": "2026-10-01", "todas_paginas": True}
            )
        )
    assert data["totalItens"] == 5 and len(data["data"]) == 5


async def test_catalogo_listar_e_obter(fake):
    async with Client(server.mcp) as c:
        lista = result_json(
            await c.call_tool(
                "catalogo_listar", {"recurso": "material", "rede_id": 1, "loja_id": 11, "filtros": {"descricao": "Pizza"}}
            )
        )
        item = result_json(await c.call_tool("catalogo_obter", {"recurso": "material", "id": 7}))
    assert lista[0]["query"] == {"rede_id": ["1"], "loja_id": ["11"], "descricao": ["Pizza"]}
    assert item["descricao"] == "Calabresa"


async def test_escrita_bloqueada_por_padrao(fake, monkeypatch):
    monkeypatch.setattr(server, "COLIBRI_ALLOW_WRITES", False)
    async with Client(server.mcp) as c:
        with pytest.raises(Exception) as exc:
            await c.call_tool("colibri_escrever", {"method": "DELETE", "path": "/api/v1/catalogo/material/7/"})
    assert "COLIBRI_ALLOW_WRITES" in str(exc.value)
    assert not [c for c in fake.calls if c[0] == "DELETE"]


async def test_escrita_habilitada_envia_json(fake, monkeypatch):
    monkeypatch.setattr(server, "COLIBRI_ALLOW_WRITES", True)
    async with Client(server.mcp) as c:
        data = result_json(
            await c.call_tool(
                "colibri_escrever",
                {"method": "POST", "path": "/api/v1/catalogo/grupo-material/", "body": {"codigo": 9, "descricao": "Pizzas"}},
            )
        )
    assert data["id"] == 3 and fake.last_body == {"codigo": 9, "descricao": "Pizzas"}


async def test_colibri_get_rejeita_caminho_fora_da_api(fake):
    async with Client(server.mcp) as c:
        with pytest.raises(Exception) as exc:
            await c.call_tool("colibri_get", {"path": "/oauth/authenticate"})
    assert "/api/" in str(exc.value)


async def test_erro_da_api_vira_mensagem_legivel(fake):
    async with Client(server.mcp) as c:
        with pytest.raises(Exception) as exc:
            await c.call_tool("colibri_get", {"path": "/api/v1/erro"})
    assert "HTTP 500" in str(exc.value) and "boom" in str(exc.value)


async def test_diagnostico(fake):
    async with Client(server.mcp) as c:
        data = result_json(await c.call_tool("diagnostico", {}))
    assert data["colibri_autenticacao"] == "ok" and data["escrita_habilitada"] is False


async def test_resource_docs(fake):
    async with Client(server.mcp) as c:
        content = await c.read_resource("colibri://docs/api")
    assert "Colibri Cloud REST API" in content[0].text


# ----------------------------------------------------------------- autenticação
async def test_owner_only_verifier_bloqueia_outros_emails():
    from fastmcp.server.auth import AccessToken, TokenVerifier

    class Inner(TokenVerifier):
        def __init__(self, email):
            super().__init__(required_scopes=["openid"])
            self.email = email

        async def verify_token(self, token):
            return AccessToken(token=token, client_id="x", scopes=["openid"], claims={"email": self.email})

    allowed = server.OwnerOnlyVerifier(Inner("Dono@Gmail.com"), {"dono@gmail.com"})
    denied = server.OwnerOnlyVerifier(Inner("intruso@gmail.com"), {"dono@gmail.com"})
    assert (await allowed.verify_token("t")) is not None
    assert (await denied.verify_token("t")) is None


async def test_static_token_autentica_como_dono(monkeypatch):
    auth = server.CameloGoogleAuth(
        owner_emails={"dono@gmail.com"},
        static_token="segredo-fixo",
        client_id="cid.apps.googleusercontent.com",
        client_secret="GOCSPX-teste",
        base_url="https://exemplo.test",
        required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
    )
    ok = await auth.load_access_token("segredo-fixo")
    assert ok is not None and ok.claims["email"] == "dono@gmail.com"
    assert await auth.load_access_token("outro") is None
