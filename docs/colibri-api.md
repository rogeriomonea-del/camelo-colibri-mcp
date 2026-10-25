# Colibri Cloud REST API — referência resumida

Fonte: coleção Postman pública "Colibri Cloud API" (NCR Colibri),
https://documenter.getpostman.com/view/31362201/2s9YeHbBMM (74 requisições,
extraídas em 2026-10-25). Base URL dos exemplos: `https://cloud.ncrcolibri.com.br` — **esse domínio
não existe mais no DNS (NXDOMAIN em 2026-10-25)**; o serviço atual responde com o mesmo contrato em
`https://cloud.colibricloud.com` (`/oauth/authenticate` sem chave → 401 `client_id not found`;
`/api/v1/...` sem token → 401 `Invalid token`). O portal do cliente fica em `<tenant>.colibricloud.com`.
Formato de entrada/saída: JSON (`Content-Type: application/json; charset=utf-8` em POST/PUT).

## Autenticação

- Chave de aplicativo: gerada no Colibri Cloud em **Usuários e lojas > Aplicativos registrados > Criar aplicativo**.
- `GET /oauth/authenticate?client_id={{clientID}}` → `{"expires_in": "5", "access_token": "...", "token_type": "Bearer"}`.
  O token vale **5 minutos**; enviar em todas as chamadas como `Authorization: Bearer <token>`.
- Códigos de erro: 400 requisição inválida · 401 não autorizado / client_id não encontrado · 403 proibido · 404 não encontrado · 500 erro interno.

## Consultas (somente leitura)

Boa prática da documentação: consultar primeiro `status-periodo` para garantir que todos os dados do dia já subiram.
Parâmetros comuns: `redes` (Array[long], `id1,id2`), `lojas` (Array[long]), `dtinicio`/`dtfim` (yyyy-MM-dd, obrigatórios), `pagina` (int, padrão 1).
Resposta paginada: `{ totalItens, totalItensPagina, totalPaginas, paginaAtual, paginaAnterior, proximaPagina, data: [...] }`.

| Endpoint | Descrição | Parâmetros adicionais |
|---|---|---|
| `GET /api/v1/rede-loja` | Redes e lojas: `[{idRede, nome, idRedePai, lojas:[{idLoja, nome}]}]` | — |
| `GET /api/v1/status-periodo` | Status do período por loja/dia: `{dataContabil, redeId, lojaId, status}` (ex.: `sem_movimento`) | — |
| `GET /api/v1/turno` | Turnos: `idTurno, idFuncionario, codFuncionario, nomeFuncionario, dataAbertura, dataFechamento, dataContabil, idLoja, nomeLoja, idRede, nomeRede, conferencias[{idTurno, idMeioPagamento, ...}]` | — |
| `GET /api/v1/movimentocaixa` | Movimento de caixa: `idMovimentoCaixa, redeId, rede, lojaId, loja, hora, idAtendente, codAtendente, nomeAtendente, vlDesconto, vlAcrescimo, vlTotalReceber, vlTotalRecebido, vlServicoRecebido, vlConsumacaoRecebido, vlTrocoContravale, ...` | `modosvenda` (Array[int]); `tipos` (Array[int]: 1 venda, 2 conta assinada, 3 sangria, 4 suprimento; sem o parâmetro retorna venda) |
| `GET /api/v1/comprovante` | Comprovantes: `denominacao, valor, maquinaId, nfNumero, nfSerie, nfSubserie, consumidor, desconto, acrescimo, chaveNfe, statusNfe, protocoloNfe, dataContabil (yyyyMMdd), dataEmissao, idOperacao, troco, dataMovimento, cancelado, detalhes[{ordem, codigo, descricao, quantidade, valor, valorUnitario, aliquota, baseIcms, codNcm, cfop, ..., cancelado}], meios[{id, descricao, valor, numAutorizacao, credenciadora, credenciadoraRede}]` | `cancelados` (bool), `consumidores` (Array[string(30)] CPF/CNPJ), `denominacao` (string(5)), `usarDtMov` (bool: filtra por data do movimento em vez da contábil) |
| `GET /api/v1/itemvenda` | Itens de venda: `idItemVenda, redeId, rede, lojaId, loja, quantidade, idMaterial, codMaterial, codMaterialStr, descricao, valorTotal, valorUnitario, codGrupo, grupoNome, comboId, combo, operacaoId, atendenteId, atendenteNome, modoVendaId, modoVendaNome, motivoCancelamento{id, nome, codigo}, consumidores[{documento, tipo}], cancelado, timestampLancamento, dtLancamento, horaLancamento, tipoCancelamento` | `cancelados` (bool), `consumidores`, `modosvenda` (Array[int]) |
| `GET /api/v1/mapaEcf` | Mapa ECF / redução Z: `id, idLoja, nomeLoja, idRede, nomeRede, dataContabil, cnpj, inscricaoEstadual, numeroEcf, numeroSerieEcf, dataReducaoZ, contOrdemOperacao, contReducaoZEcf, valorGrandeEcf, valorVendaBruta, detalhes[...], totalizadores[{contador, valor, operacao}]` | — |
| `GET /api/v1/modo-venda` | Modos de venda: `[{id, nome}]` (ex.: 1 BALCAO, 2 ENTREGA, 3 MESA, 4 FICHA) | — |

Observação: alguns exemplos da coleção usam caminhos legados (`/rede-loja`, `{{url}}/terceiros/restful/turno`);
as definições das requisições usam `/api/v1/...`, adotado aqui.

## Catálogo (`/api/v1/catalogo/...`)

Padrão REST por recurso: `GET <recurso>/` (lista), `POST <recurso>/` (cria, 201), `GET <recurso>/:id/`,
`PUT <recurso>/:id/` (altera), `DELETE <recurso>/:id/` (204). Exemplos da coleção às vezes omitem o
segmento `/catalogo/` — se um caminho retornar 404, tentar sem ele.

| Recurso | Lista: parâmetros | Corpo (POST/PUT) |
|---|---|---|
| `grupo-material` | `rede_id` (long, obrigatório) | `ativo bool, codigo int*, descricao string(30)*, parent int, rede int*, tipo string(2)*` |
| `grupo-material/tipos` | — (`AL` Alimentos, `BL` Alcoólicos, `BN` Não alcoólicos, `OU` Outros) | — |
| `material` | `rede_id`, `loja_id` (Array[long]); `codigo`, `descricao`, `grupo`, `local_producao_id`, `page`, `limit`, `o` (ordenação, ex. `-descricao`) | `ativo, balanca, cod_externo string(30), codigo int, consumacao, descricao_producao string(50), descricao_touch string(60), descricao string(30), grupo int, imagem string(80), local_producao int, loja long, qtde_frac, rede long, requer_obs, servico, sub_rede long, tecla_prog string(1), unidade string(6), venda, vende_combo, vende_web` |
| `combo` | `rede_id` (obrigatório) | `rede, loja, sub_rede, codigo, descricao_touch, descricao_prod, imagem, local_producao_id int, requer_obs bool, vende_web bool, grupo int` |
| `combo/precificador` | — (1 Preço fixo, 2 Média, 3 Maior preço, 4 Rodizio) | — |
| `combo/quantificador` | — (1 Inteiro, 2 Fracionado) | — |
| `combo/slot` | — | `classe int, combo int, local_producao_id int, material int, maximo int, minimo int, ordem int, precificador int, preco float, quantificador int` |
| `classe` | `rede_id` (obrigatório), `filtro`, `o` | `rede long, sub_rede long, loja long, classes [int], materiais [int], combos [int], descricao string(20)*, descricao_touch string(40), permite_combinado bool, ordem_alfabetica bool, imagem string(80)` |
| `classe/item` | `classe` (int), `material_item` (int) | `tipo string(2) 'CL'/'MA'/'CO'*, classe int*, classe_item int, material_item int, combo_item int, ordem int*` |
| `observacao` | `rede_id` (obrigatório) | `ativo bool, loja long, nome string(20), rede long, sub_rede long` |
| `observacao/material-observacao` | `ids`, `material`, `combo` | `combo int, material int, observacao int, posicao int` |
| `motivo-cancelamento` | `rede_id` (obrigatório) | `ativo bool, baixa_estoque bool, codigo int, loja long, nome string, rede long, sub_rede long` |
| `tabela-preco` | `lojas` (long) | `ativo bool, dt_vigencia yyyy-MM-dd, loja long, modo_venda int, nome string(40)` |
| `tabela-preco/item-preco` | `tabela_id` (int), `material_id` (int) | `ativo bool, material int, preco float, tabela int` |
| `local-producao` | `rede_id` (obrigatório) — somente GET lista | — |
| `meio-pagamento` | `rede_id` | `cartao bool, codigo int, cor string, descricao string, imagem string, loja, multiplo bool, ordem int, rede, sub_rede, tef bool, troco_contravale bool, troco_dinheiro bool, troco_repique bool` |
| `meio-pagamento/tipos-tef` | — (`D` Débito, `C` Crédito, `V` Voucher) | — |

`*` = marcado como obrigatório na coleção.
