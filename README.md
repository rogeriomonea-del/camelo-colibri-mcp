# camelo-colibri-mcp

Servidor MCP remoto que embute a **Colibri Cloud REST API** (NCR Colibri — PDV da
Pizzaria Camelo) e a expõe como ferramentas para **Claude.ai / Claude Desktop / Cowork,
Claude Code, ChatGPT (developer mode), Codex CLI e n8n**, com uma única URL:

```
https://colibri.celestiaflights.com/mcp
```

Autenticação: **login Google** (OAuth 2.1 + PKCE, Dynamic Client Registration e
Client ID Metadata Documents, via FastMCP `OAuthProxy`), restrito às contas em
`OWNER_EMAILS`. Qualquer outra conta Google é recusada com 401, mesmo depois de
autenticar no Google. O token da API Colibri (validade de 5 min) é obtido e renovado
pelo servidor; nenhum cliente vê a chave do aplicativo.

## Ferramentas

| Ferramenta | Endpoint Colibri | Observações |
|---|---|---|
| `listar_redes_lojas` | `GET /api/v1/rede-loja` | IDs usados nos filtros `redes`/`lojas` |
| `status_periodo` | `GET /api/v1/status-periodo` | conferir antes de consolidar um dia |
| `turnos` | `GET /api/v1/turno` | |
| `movimento_caixa` | `GET /api/v1/movimentocaixa` | `tipos`: 1 venda, 2 conta assinada, 3 sangria, 4 suprimento |
| `comprovantes` | `GET /api/v1/comprovante` | cupons/NF com itens e meios de pagamento |
| `itens_venda` | `GET /api/v1/itemvenda` | itens vendidos e cancelamentos |
| `mapa_ecf` | `GET /api/v1/mapaEcf` | redução Z |
| `modos_venda` | `GET /api/v1/modo-venda` | |
| `catalogo_listar` / `catalogo_obter` | `GET /api/v1/catalogo/<recurso>/[id/]` | grupos, materiais, combos, classes, observações, motivos, tabelas de preço, locais de produção, meios de pagamento |
| `colibri_get` | `GET /api/...` | qualquer endpoint documentado |
| `colibri_escrever` | `POST/PUT/DELETE /api/...` | só com `COLIBRI_ALLOW_WRITES=true` |
| `diagnostico` | — | quem está logado e se o token Colibri é obtido |

As consultas são paginadas; `todas_paginas=true` consolida até `COLIBRI_MAX_PAGINAS` e
devolve `paginacaoCompleta`/`ultimaPaginaLida`; uma página inesperada no meio interrompe
com erro em vez de subestimar totais. Respostas grandes (ex.: comprovantes de um mês inteiro)
podem exceder o limite do cliente — prefira intervalos curtos ou poucas páginas por chamada.
A referência da API está em [`docs/colibri-api.md`](docs/colibri-api.md) e também é
publicada como resource MCP `colibri://docs/api`.

## Variáveis de ambiente

Veja [`.env.example`](.env.example). Obrigatórias: `PUBLIC_URL`, `OWNER_EMAILS`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `COLIBRI_CLIENT_ID`.

## Pré-requisitos externos

1. **Google Cloud Console** → APIs e serviços → Credenciais → *Criar credenciais* →
   *ID do cliente OAuth* → tipo **Aplicativo da Web**:
   - Origens JavaScript autorizadas: `https://colibri.celestiaflights.com`
   - URIs de redirecionamento autorizados: `https://colibri.celestiaflights.com/auth/callback`
   - Tela de consentimento: tipo *Externo*, status **Em produção** (em *Teste* o refresh
     token do Google expira em 7 dias e o login teria de ser refeito toda semana).
     Escopos usados: `openid` e `userinfo.email` (não sensíveis; sem verificação).
2. **Colibri Cloud** (portal `https://<tenant>.colibricloud.com`) → *Usuários e lojas > Aplicativos
   registrados > Criar aplicativo* → copiar a chave para `COLIBRI_CLIENT_ID`.
   A API atual responde em `https://cloud.colibricloud.com` (padrão de `COLIBRI_BASE_URL`); o host
   `cloud.ncrcolibri.com.br` da documentação não existe mais no DNS.

## Deploy no Coolify (VPS Hostinger)

1. *New Resource* → *Public Repository* → URL deste repositório → *Build Pack: Dockerfile*.
2. *Ports Exposes*: `8000`. *Domains*: `https://colibri.celestiaflights.com` — o Coolify
   emite o certificado Let's Encrypt automaticamente (porta 80/443 abertas no VPS).
3. *Environment Variables*: as de `.env.example` (os segredos colados pelo dono). Nunca
   defina `MCP_AUTH_DISABLED` no deploy: com `PUBLIC_URL` https o servidor se recusa a subir.
4. *Persistent Storage* → **Volume Mount** (não *Directory Mount*) com destino `/data`:
   guarda os registros OAuth dos clientes e os tokens upstream (criptografados). Sem
   ele, cada redeploy obriga a reautenticar todos os clientes; com *Directory Mount* o
   diretório vem do host como root e o processo (uid 10001) não consegue gravar.
5. *Deploy*. Teste: `https://colibri.celestiaflights.com/healthz` → `{"status":"ok"}`
   e `https://colibri.celestiaflights.com/.well-known/oauth-authorization-server`.
6. Primeiro uso: conecte um cliente, faça o login Google e chame `diagnostico` — ele
   confirma a conta autenticada e se o token do Colibri foi obtido com a chave configurada.

## Conectar os clientes

**Claude.ai / Claude Desktop / Cowork** — Configurações → Conectores → *Adicionar conector
personalizado*: nome `Camelo Colibri`, URL `https://colibri.celestiaflights.com/mcp`,
autenticação *Sign in now* / *Register automatically* (DCR). No login, escolha a conta Google
autorizada. (Fonte: https://claude.com/docs/connectors/custom/remote-mcp)

**Claude Code** (na sua máquina):
```bash
claude mcp add --transport http --scope user colibri https://colibri.celestiaflights.com/mcp
claude mcp login colibri          # ou /mcp dentro da sessão
claude mcp get colibri            # deve mostrar "Connected"
```
(Fonte: https://code.claude.com/docs/en/mcp)

**ChatGPT** (Plus/Pro/Business/Enterprise/Edu, na web) — Configurações → Apps →
*Advanced settings* → ativar *Developer mode* → *Create app*: URL
`https://colibri.celestiaflights.com/mcp`, autenticação OAuth. Em cada conversa,
selecione o app no menu "+" → *Developer mode*. (Fonte:
https://developers.openai.com/api/docs/guides/developer-mode)

**Codex CLI / app** — `~/.codex/config.toml`:
```toml
[mcp_servers.colibri]
url = "https://colibri.celestiaflights.com/mcp"
```
depois `codex mcp login colibri`. (Fonte: https://developers.openai.com/codex/mcp)

**n8n / scripts** — defina `MCP_STATIC_TOKEN` no servidor e use o nó *MCP Client* com
*Header Auth* `Authorization: Bearer <token>`.

## Desenvolvimento

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                       # API Colibri simulada, sem rede
MCP_AUTH_DISABLED=true COLIBRI_CLIENT_ID=... python server.py   # local, sem OAuth
```

## Segurança

- Segredos só em variáveis de ambiente; nada no repositório.
- Escrita no catálogo desabilitada por padrão.
- Consentimento explícito (tela do FastMCP) antes de autorizar cada cliente OAuth.
- Tráfego do Claude.ai sai de `160.79.104.0/21`; o servidor precisa ser público em HTTPS.
