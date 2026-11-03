# Empório da Música — Agente de Atendimento

[![ci](https://github.com/RonildoSilva/ai-engineer-challenge/actions/workflows/ci.yml/badge.svg)](https://github.com/RonildoSilva/ai-engineer-challenge/actions/workflows/ci.yml)

Protótipo de agente de atendimento por texto para a loja fictícia **Empório da Música**, em Campo Grande/MS.
As regras de negócio ficam expostas por um **servidor MCP**. O agente conversa com **LLMs locais ou online**,
e o baseline é o `Bonsai-1.7B` rodando em CPU com o runtime oficial do llamafile.

> 🚧 **Em construção.** Status por etapa em [Roadmap](#roadmap). Diagramas marcados com *(planejado)*
> descrevem a arquitetura-alvo; o restante já existe no repositório.

---

## Sumário
- [Arquitetura](#arquitetura)
- [Pré-requisitos](#pré-requisitos)
- [Setup](#setup)
- [Como rodar](#como-rodar)
- [Verificação (testes e CI)](#verificação-testes-e-ci)
- [Pipeline de dados](#pipeline-de-dados)
- [Manual de políticas](#manual-de-políticas)
- [Busca híbrida](#busca-híbrida)
- [Catálogo](#catálogo)
- [Pedidos e regras da loja](#pedidos-e-regras-da-loja)
- [Servidor MCP (a API)](#servidor-mcp-a-api)
- [Agente](#agente)
- [Avaliação](#avaliação)
- [Conversas de exemplo](#conversas-de-exemplo)
- [Camada de dados](#camada-de-dados)
- [Escalabilidade e robustez](#escalabilidade-e-robustez)
- [Modelos locais](#modelos-locais)
- [Decisões técnicas](#decisões-técnicas)
- [Tratamento de dados e suposições](#tratamento-de-dados-e-suposições)
- [Roadmap](#roadmap)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Limitações conhecidas](#limitações-conhecidas)
- [Uso de assistentes de código](#uso-de-assistentes-de-código)

---

## Arquitetura

### Visão geral

```mermaid
flowchart LR
    U([Cliente]) --> CLI[CLI de chat]

    subgraph HOST[Agent Host]
        direction TB
        CLI --> R[Detecção de assunto<br/>+ pré-busca de contexto]
        R --> B[Orçamento de contexto]
        B --> L[Loop do agente<br/>function calling]
        L <--> H[(Histórico<br/>SQLite)]
    end

    L <-->|API compatível<br/>com OpenAI| LLM
    L <-->|MCP<br/>stdio / HTTP| MCP

    subgraph LLM[LLM plugável por perfil]
        direction TB
        BON[Bonsai-1.7B<br/>llamafile · offline]
        OTH[Outros pequenos ≤4B<br/>offline]
        API[APIs online<br/>referência]
    end

    subgraph MCP[Servidor MCP · emporio]
        direction TB
        T[Tools<br/>produtos · pedidos · devolução<br/>pagamento · frete · horário]
        RS[Resources<br/>politicas://secao]
        P[Prompt<br/>persona_atendente]
    end

    MCP --> D[(SQLite<br/>CSVs limpos)]
    MCP --> PO[Políticas<br/>markdown por seção]
```

**Princípio central:** preço, estoque, prazos e status nunca saem "da cabeça" do LLM. Eles vêm de código
Python determinístico e testado, exposto via MCP. O LLM decide o que consultar e redige a resposta com a
persona da loja.

### Fluxo de uma mensagem

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant A as Agent Host
    participant R as Detecção de assunto
    participant M as Servidor MCP
    participant L as LLM

    C->>A: "Quero devolver o pedido 8, me arrependi"
    A->>R: intenções + dados citados
    R-->>A: devolução · pedido 8 · sem e-mail/telefone
    A->>M: pré-busca: consultar_politica("troca devolução …")
    M-->>A: seção "Trocas e devoluções" (custo: ~479 tokens)
    A->>L: persona + orientação "peça e-mail/telefone" + dados (sem schemas de tools)
    L-->>A: explica o prazo e pede o e-mail ou telefone (LGPD)
    A-->>C: resposta
    C->>A: "ana@coldmail.com"
    A->>R: só dados → herda a intenção do turno anterior
    A->>M: pré-busca: verificar_devolucao(8, ana@…, "me arrependi")
    M-->>A: elegível? prazo final, condições, próximos passos
    A->>L: persona + histórico + resultado
    L-->>A: resposta final
    A-->>C: resposta no tom da loja
```

### Por que detecção de assunto *antes* do LLM

Testes com o Bonsai mostraram que modelos pequenos acertam a chamada de ferramenta só em parte dos casos.
O erro é **sistemático por tipo de pergunta**:

```mermaid
flowchart LR
    Q1["Quanto custa o Takamine GD20?"] -->|✅ 4/4| T1[detalhar_produto]
    Q2["Qual o horário da loja?"] -->|✅ 4/4| T2[consultar_politica]
    Q3["Tem violão Yamaha C40?"] -.->|❌ 0/4| X1[responde sem consultar]
    Q4["Posso devolver um produto de 5 dias?"] -.->|❌ 0/4| X2[responde genericamente]
```

Por isso o host identifica a intenção e já injeta o contexto certo. Isso vale para **todos** os modelos,
o que deixa o comportamento previsível e a comparação justa. As tools continuam disponíveis como rede de
segurança: quando o assunto não é reconhecido, quando uma pré-busca falha e, em janelas grandes, sempre.

### Orçamento de contexto

```mermaid
flowchart TB
    subgraph Perfil[Perfil do LLM]
        CW[context_window<br/>auto via /props no llamafile<br/>max_output_tokens]
    end
    subgraph MCPm[Servidor MCP]
        SZ[custo publicado<br/>tokens_estimados por seção do manual]
    end
    CW --> BUD{{Host: orçamento por turno}}
    SZ --> BUD
    BUD --> S0[1 · system prompt + mensagem atual<br/>sempre]
    BUD --> S1[2 · reserva para a resposta<br/>+ margem de 5%]
    BUD --> S2[3 · dados pré-buscados<br/>até 40% do disponível, resultado inteiro ou nada]
    BUD --> S3[4 · definições de tools]
    BUD --> S4[5 · histórico, do mais recente<br/>aos pares pergunta + resposta]
```

O MCP é agnóstico ao modelo (pode atender a CLI com o Bonsai em 8k e um cliente com 128k ao mesmo tempo).
Ele informa **quanto custa** cada informação, e o host decide **quanto cabe**:

| | Bonsai local (8k) | API online (128k) |
|---|---|---|
| Disponível para a entrada | 7.270 tokens | 120.576 tokens |
| Seções do manual pedidas | 2 | 3 |
| Produtos por busca | 5 | 5 |
| Tools junto com a pré-busca | só se a pré-busca não resolveu | as do assunto, sempre |

---

## Pré-requisitos

Tudo está fixado. Nada depende do que "já estava instalado" na máquina.

| Ferramenta | Versão | Como instalar | Para quê |
|---|---|---|---|
| Linux/macOS x86_64 ou arm64 (ou WSL2) | — | — | runtime llamafile em CPU |
| `bash`, `curl`, `sha256sum` | qualquer recente | gerenciador de pacotes do SO (`apt install curl coreutils`) | setup e downloads |
| [uv](https://docs.astral.sh/uv/) | **0.12.21** (≥ 0.12) | `curl -LsSf https://astral.sh/uv/0.12.21/install.sh \| sh` | Python e dependências |
| Python | **3.12.14**, gerenciado pelo uv (`.python-version`) | automático no setup | — |
| Dependências Python | travadas em `uv.lock` | `uv sync --locked` (automático) | — |
| Runtime llamafile | **0.10.6** *thin*, SHA-256 em `models/manifest.tsv` | `./scripts/download_model.sh llamafile-0.10.6` (automático) | servidor do LLM local |
| Modelo Bonsai-1.7B | GGUF oficial PrismML, SHA-256 em `models/manifest.tsv` | `./scripts/download_model.sh bonsai-1.7b` (automático) | LLM baseline |
| Modelo de embeddings | embeddinggemma-300m (GGUF via `ggml-org`), SHA-256 em `models/manifest.tsv` | `./scripts/download_model.sh embeddinggemma-300m` (automático) | busca semântica |

> Prefira o instalador oficial do uv. A versão via **snap** às vezes encerrava com código 120 sem repassar o
> stdout durante o desenvolvimento.

## Setup

```mermaid
flowchart LR
    A[./scripts/setup.sh] --> B{bash, curl,<br/>sha256sum?}
    B -->|não| X[[erro + instrução]]
    B -->|sim| C{uv instalado?}
    C -->|não| X
    C -->|sim| D[uv python install<br/>CPython 3.12.14]
    D --> E[uv sync --locked]
    E --> F[download runtime<br/>llamafile 0.10.6]
    F --> G[download GGUFs<br/>Bonsai-1.7B +<br/>embeddinggemma-300m]
    F & G --> H{SHA-256 confere?}
    H -->|não| X
    H -->|sim| I[cria .env]
    I --> P[emporio pipeline run<br/>incremental · sobe o servidor<br/>de embeddings se preciso]
    P --> V[verificação rápida<br/>lock · lint · unitários]
    V --> J([pronto: uv run emporio chat])
```

```bash
./scripts/setup.sh                 # tudo (~630 MB de download na primeira vez)
SKIP_MODEL=1 ./scripts/setup.sh    # sem runtime/modelo local (ex.: usar só API online)
EVAL_MODELS=1 ./scripts/setup.sh   # + Qwen3-1.7B (1,8 GB), o modelo de comparação da avaliação
SKIP_CHECK=1 ./scripts/setup.sh    # sem a verificação final (o CI roda o check.sh completo em seguida)
```

## Como rodar

Depois do `./scripts/setup.sh`, um comando abre o atendimento no terminal. Ele sobe o Bonsai e o servidor de
embeddings se não estiverem no ar e encerra ao sair os que ele mesmo subiu:

```bash
uv run emporio chat                     # conversa com a Mel (histórico salvo em data/processed/history.db)
uv run emporio chat --debug             # + assunto, pré-busca, tools e tokens de cada turno
uv run emporio chat --transcript conversa.md   # grava a conversa em markdown (com as tools usadas)
uv run emporio chat --list              # sessões salvas
uv run emporio chat --session 28d68dd5  # continua uma conversa
uv run emporio chat --profile anthropic # outro perfil de LLM (config/llm_profiles.yaml)
```

Dentro do chat: `/nova` (outra conversa), `/sessoes`, `/ajuda`, `/sair` (ou Ctrl+D). No Bonsai em CPU de
notebook, cada resposta leva de 1 a 2,5 minutos (ver [Agente](#agente)).

Servidores e comandos avulsos:

```bash
./scripts/start_llamafile.sh         # terminal 1: Bonsai-1.7B em http://127.0.0.1:8080/v1 (modelo "bonsai")
MODE=embedding ./scripts/start_llamafile.sh   # (opcional) embeddings em http://127.0.0.1:8081/v1
uv run python scripts/smoke_llm.py   # terminal 2: chat simples + chamada de ferramenta
```

`start_llamafile.sh` aceita variáveis de ambiente: `MODE` (`chat` ou `embedding`), `MODEL`, `ALIAS`, `PORT`,
`CTX`, `THREADS` (padrão: núcleos físicos), `GPU` (`disable` nos dois modos; `auto` só em placa validada) e
`REASONING` (`off` por padrão, só no chat).

```bash
uv run emporio pipeline run            # pipeline de dados (pula o que está em dia)
uv run emporio pipeline status         # estado de cada etapa
uv run emporio pipeline graph          # DAG em Mermaid
uv run emporio policies list           # seções do manual de políticas publicadas
uv run emporio policies search "posso devolver?"   # seção mais relevante para a pergunta
uv run emporio search "violão pra ligar na caixa de som" [--kind product|policy]   # busca híbrida
uv run emporio catalog search "Quais violões custando até R$1000?" [--all]   # filtros inferidos + preço
uv run emporio catalog show "Quanto custa o Takamine GD20?"                  # detalhe / alternativas
uv run emporio order show 8 --contato "(67) 99654-7821"                      # pedido (verificação LGPD)
uv run emporio order return 7 --contato leticia.rocha@jmail.com --motivo "veio com defeito"
uv run emporio rules pay 599.90 --parcelas 10      # PIX, débito, boleto e parcelamento válido
uv run emporio rules ship 349.90 --cidade "Campo Grande"
uv run emporio rules hours                         # aberta/fechada agora (fuso de Campo Grande)
uv run emporio mcp                                 # servidor MCP via stdio (para agentes e Claude Desktop)
uv run emporio mcp --http --port 8000              # servidor MCP via Streamable HTTP em /mcp
uv run emporio llm list                            # perfis de LLM (testados × referência)
uv run emporio llm check [--profile bonsai]        # conexão, contexto, chat e tool calling
uv run emporio ask "Quanto custa o Takamine GD20?" --debug   # uma pergunta ao agente, sem histórico
```

Avaliação (ver [Avaliação](#avaliação)):

```bash
uv run emporio eval intents             # classificador de intenção + guardrails (milissegundos)
uv run emporio eval retrieval           # busca lexical × híbrida (sobe os embeddings)
uv run emporio eval scenarios           # 18 cenários com o oráculo (segundos)
uv run emporio eval scenarios --profile bonsai --profile qwen3-1.7b   # com LLMs reais (~20 min cada)
```

## Verificação (testes e CI)

Cada push reproduz o setup **do zero numa máquina limpa** (GitHub Actions, Ubuntu 24.04) e roda a mesma
verificação disponível localmente. É a garantia de que o projeto funciona fora da máquina de
desenvolvimento.

```mermaid
flowchart LR
    P([push / PR]) --> CI
    subgraph CI[GitHub Actions · ubuntu-24.04 limpo]
        direction LR
        U[uv fixado<br/>instalador oficial] --> S[scripts/setup.sh<br/>Python + deps + modelo<br/>com SHA-256 + pipeline] --> K[scripts/check.sh]
    end
    subgraph K2[scripts/check.sh]
        direction TB
        L1[uv lock --check] --> L2[ruff check + format]
        L2 --> L3[pytest unitário<br/>570 testes, ~35 s, offline<br/>+44 com Postgres, job próprio]
        L3 --> L4[pytest -m integration<br/>9 testes, ~5 min,<br/>sobe os modelos reais]
    end
    K -.-> K2
```

```bash
./scripts/check.sh          # tudo: lock, lint, unitários e integração (~6 min com os modelos baixados; o teste de ponta a ponta do agente leva ~3,5)
FAST=1 ./scripts/check.sh   # sem integração
uv run pytest -m integration -rs   # só a integração com o LLM local
```

| Camada | O que garante | Arquivo |
|---|---|---|
| Qualidade de código | [ruff](https://docs.astral.sh/ruff/) com ~30 famílias de regras: correção (`F`, `B`, `PL`), **segurança** (`S`/bandit), exceções (`TRY`, `BLE`), pytest (`PT`), pathlib (`PTH`), datas com fuso (`DTZ`), estilo de docstring (`D`, Google) e formatação. Exceções justificadas uma a uma no `pyproject.toml` | `pyproject.toml` |
| Versões fixadas | Python exato e gerenciado, `requires-python`, uv no setup e no README, `uv.lock` atualizado; todo artefato do manifesto é baixado pelo setup | `tests/test_infra.py` (33 testes nas 5 linhas de infraestrutura) |
| Manifesto | URLs fixadas em tag/commit (nunca `main`/`latest`), SHA-256 válido, artefatos baixados íntegros | `tests/test_infra.py` |
| Download | baixa, é idempotente, **recusa checksum errado** sem deixar lixo, não sobrescreve arquivo corrompido sem `FORCE=1` | `tests/test_infra.py` |
| Scripts | sintaxe bash válida, executáveis, erro claro sem artefatos, `MODE` inválido rejeitado, política de GPU por modo | `tests/test_infra.py` |
| `.gitignore` | binários, `.env`, `_raw/` e gerados fora do git; manifesto, `.env.example` e dados brutos dentro | `tests/test_infra.py` |
| Relatório de dados | cada número de `docs/data_report.md` continua verdadeiro sobre `data/raw` (11) | `tests/test_data_findings.py` |
| Orquestrador | ordem topológica, ciclo/dependência inexistente rejeitados, execução incremental por conteúdo, invalidação por entrada/código/versão, saída adulterada detectada, falha isola dependentes, linhagem (19) | `tests/test_pipeline_core.py` |
| Config, logs e CLI | defaults, sobrescrita por ambiente, **config inválida falha cedo**, logs texto/JSON, `emporio pipeline` e códigos de saída (12) | `tests/test_config_cli.py` |
| Camada de dados | regras de tratamento, 7 tipos de linha inválida reportados com `arquivo:linha campo`, integridade referencial, **7 violações barradas pelos `CHECK`s do banco**, banco anterior intacto quando a validação falha, mudança de regra refaz o `transform` (41) | `tests/test_data_layer.py` |
| Repository | **suíte de contrato** (22, reutilizável para o Postgres) + somente leitura, injeção de SQL, uma conexão por thread (4) | `tests/contract/`, `tests/test_sqlite_repository.py` |
| Manual de políticas | extração em seções numeradas (listas não viram seção, números não quebram), curadoria completa e em dia com o PDF, **fato ausente / seção do PDF alterada / seção sem curadoria detectados**, decisões aplicadas, busca por assunto (15 perguntas reais + 3 fora de escopo), CLI (38) | `tests/test_policies.py` |
| Catálogo | preço com promoção × PIX (§6.2), faixa de preço e categoria extraídas do texto, acessórios × "violão de 7 cordas", exemplo do enunciado ("violões até R$1000": 12 opções), preço promocional nos filtros, nome aproximado e pergunta inteira ("Quanto custa o Takamine GD20?"), ambiguidade, alternativas para esgotado/descontinuado/em breve, CLI (62) | `tests/test_catalog.py` |
| Pedidos e regras | verificação por e-mail ou telefone **do titular** (com/sem DDD, +55) e mensagem neutra em qualquer falha, e-mail duplicado só abre o próprio pedido, pedido atrasado, prazos de arrependimento/defeito/preferência dentro e fora da janela, garantia legal, parcelamento pelas faixas da §3.1, frete metropolitano × outras cidades × frete especial, feriados (Páscoa calculada) e fuso de Campo Grande, CLI (70) | `tests/test_orders_rules.py` |
| Servidor MCP | exatamente as 8 tools, todas somente leitura e com saída estruturada; nenhuma forma de listar clientes; respostas de cada tool (GD20, promoção sem PIX, alternativas, escopos, pedido verificado e não verificado, devolução, pagamento, frete, horário, políticas); erro esperado amigável, argumento inválido rejeitado, **erro inesperado sem detalhes internos**, **logs sem e-mail/telefone**; resources e prompt; transportes **stdio e HTTP** com subprocesso real (26) | `tests/test_mcp_server.py` |
| Cliente LLM | perfis do projeto válidos (online marcados como referência), seleção e sobrescritas, **chave de API ausente com erro claro**, contexto lido de `/props` e fallback, temperatura e `max_tokens` do perfil, tools só se suportadas, **normalização de respostas de modelos pequenos** (JSON inválido nos argumentos, cercas de código, tool call escrita como texto, bloco `<think>`), servidor fora do ar e erro 5xx, CLI (25) | `tests/test_llm_client.py` |
| Detecção de assunto | 16 frases reais com uma intenção e 5 com várias ("quanto custa o GD20 e dá pra fazer em 10x?"), devolução não vira busca de catálogo, assunto desconhecido, subconjunto de tools, "18h"/"12x"/e-mail não viram código de modelo, dados citados (pedido, e-mail, telefone, valor, parcelas, cidade), "meu telefone é…" não é pergunta sobre a loja, **mensagem só com dados herda a intenção**, dados completados pelo histórico, trecho do produto na frase, reclamação (33) | `tests/test_router.py` |
| Guardrails | **injeção de prompt** (9 frases, PT e EN), **dados de terceiros** (8), **fora do escopo** (6), mensagem longa, **11 perguntas legítimas parecidas que precisam passar**; na saída, contato de terceiros mascarado (loja e o próprio cliente preservados) e persona recitada; no agente: mensagem bloqueada não chega ao LLM nem às tools, reclamação sozinha com resposta pronta e encaminhada, reclamação + troca com orientação, categoria vazia, instrução recitada → correção → resposta de segurança, diretrizes internas e notas de curadoria fora dos dados, **turno bloqueado não contamina o seguinte**, e-mail com acento da loja preservado (17 funções, 49 casos) | `tests/test_guardrails.py` |
| Orçamento de contexto | disponível = janela − resposta − margem, **8k × 128k** (seções e produtos pedidos), seções escolhidas pelo **custo publicado pelo MCP**, tudo cabe em 128k, em 8k o histórico antigo sai primeiro e aos pares, ordem das mensagens, **resultado de tool nunca cortado ao meio**, tools que não cabem saem e são reportadas (9) | `tests/test_budget.py` |
| Agente | persona e contexto (data de referência, loja aberta) no system prompt, tools do MCP no formato do LLM, loop tool → resultado → resposta, histórico só com perguntas e respostas, argumento inválido / tool inexistente / erro da tool devolvidos ao modelo, **nome de tool quase certo corrigido**, **resposta de segurança** (limite de rodadas, LLM fora do ar, resposta vazia), PII mascarada no rastro, agente via stdio; **pré-busca**: "posso devolver?" em 1 chamada sem tools com a regra dos 7 dias, preço pronto e "sem promoção" explícito, preço do produto alimentando a simulação de 10x, pedido sem dados → pede os dados → pré-busca no turno seguinte, acessório, assunto desconhecido, janela grande; **preço sem fonte**: uma correção e depois resposta de segurança; pedido sem dados respondido sem LLM, pedindo só o que falta; **respostas prontas** de acessório, horário (com o semanal) e contato (do manual); pergunta seguinte usa o preço do último produto; desconto com produto em contexto dispensa o manual (36) | `tests/test_agent.py` |
| Chat e histórico | e-mail/telefone mascarados ao gravar (pedido e preço preservados), sessão de ida e volta com metadados do turno, janela sempre começando pelo cliente, ordem das sessões e exclusão em cascata, uma conexão por thread, **sessão retomada devolve o histórico e o assunto** ("é o 8, telefone…" continua o pedido), sessão inexistente, `/nova` limpa, transcrição markdown com tools e sem PII, CLI `emporio chat` (conversa, retomada, `--list`, `/nova`, erro), turnos bloqueados fora do contexto ao retomar (14) | `tests/test_chat.py` |
| Avaliação | cenários, intenções e gabarito de busca válidos (rótulos existentes, ids únicos, exemplos do enunciado presentes), **frases da avaliação diferentes das dos testes**, semântica das asserções (sem acento, pontuação preservada, resposta de segurança reprova), **os 18 cenários passam com o oráculo** (o dado certo chega ao modelo), métricas do classificador (precisão/revocação por rótulo) e **piso de 90%**, métricas de busca, resumo regenerado de todos os JSON, CLI `emporio eval` (12) | `tests/test_evals.py` |
| Postgres + pgvector | **as mesmas suítes de contrato** do `Repository` (22) e do `Retriever` (híbrido sobre pgvector), documentos idênticos aos do SQLite, **ranking do pgvector igual ao do índice SQLite**, filtro por tipo no SQL, conexão somente leitura, injeção de SQL inerte, `CHECK`s equivalentes, carga idempotente, servidor MCP inteiro rodando sobre os dois backends por configuração (44; job `postgres` do CI, contra um Postgres real) | `tests/test_postgres.py` |
| Deploy | imagens fixadas por digest, uv do Dockerfile igual ao do setup, `.dockerignore` como lista de permissão sem `_raw`/`.env`/modelos, perfis do compose, mesma imagem Postgres no CI e no compose (6); job `docker` do CI constrói a imagem e responde dentro do container | `tests/test_deploy.py` |
| Busca | documentos (descrição suja não entra), índice vetorial, **suíte de contrato do `Retriever`** (lexical e híbrido), fusão RRF, limiar de similaridade, **degradação para lexical** (serviço fora do ar, índice vazio, modelo trocado) e recuperação, cache de vetores, etapa sem modelo, CLI (44) | `tests/test_retrieval.py`, `tests/contract/` |
| Modelos reais | **chat:** servidor sobe pelo script, contexto 8192, alias `bonsai`, responde e devolve **tool call estruturado**; **embeddings:** vetores de 768 dimensões e pergunta sem palavras em comum ("esquecer meu cadastro") mais próxima da seção certa; **busca híbrida com o modelo real** com a porta configurada ocupada por outro serviço (sobe em porta livre, acha privacidade, ignora "receita de bolo"); **encerrar o `emporio mcp` (SIGTERM ou SIGHUP) encerra o servidor de embeddings**; **`LLMClient` com o Bonsai real** (contexto 8192 lido de `/props` e tool call); **agente de ponta a ponta** (mesma conversa): "Quanto custa o Takamine GD20?" → pré-busca `detalhar_produto` → 1 chamada → R$ 2.199 sem "promocional"; "posso devolver?" → pré-busca da política → regra dos 7 dias (9) | `tests/test_llm_integration.py` |

## Pipeline de dados

Todo processamento de dados passa por um **DAG de etapas idempotentes**, executado pelo `setup.sh` e pela
CLI `emporio pipeline`. Etapas em verde já existem; as demais entram nos próximos commits.

```mermaid
flowchart LR
    S[snapshot_raw<br/>inventário + hashes<br/>das fontes]:::done --> V[validate_raw<br/>contratos dos CSVs<br/>+ integridade]:::done
    V --> T[transform<br/>decisões do relatório]:::done --> L[load<br/>SQLite com CHECKs]:::done --> Q[quality_checks<br/>invariantes]:::done
    S --> PE[extract_policies<br/>PDF → seções<br/>modo layout]:::done --> PS[build_policies<br/>curadoria verificada<br/>contra o PDF]:::done
    L --> E[embed_index<br/>vetores com cache<br/>por texto]:::done
    PS --> E
    classDef done fill:#bbf7d0,stroke:#15803d,color:#000
```

| Etapa | O que faz | Saída |
|---|---|---|
| `snapshot_raw` | Inventário das 7 fontes (hash, linhas, colunas); falha se faltar alguma | `raw_snapshot.json` |
| `validate_raw` | Contrato Pydantic por linha (tipo, domínio, formato: preço > 0, status válido, specs em JSON, rastreio `BR…BR`, parcelas 1–12…) + unicidade e integridade referencial. Reporta **todas** as falhas com `arquivo:linha campo` | `validation_report.json` |
| `transform` | Aplica as decisões do [relatório](docs/data_report.md): disponibilidade, descrição não confiável (7 itens), promoção vigente única, texto normalizado para busca, pagamento → forma + parcelas, dados mínimos do cliente | `staging/*.jsonl` |
| `load` | Cria o banco a partir do [`schema.sql`](src/emporio/data/schema.sql) numa transação única; a troca do arquivo é atômica, então um erro mantém o banco anterior | `store.db` |
| `quality_checks` | Invariantes no banco carregado (promoção coerente, nenhum pedido sem itens, descrição oculta…) + `PRAGMA integrity_check` | `quality_report.json` |
| `extract_policies` | Extrai o PDF em modo *layout* (preserva tabelas e listas), remove cabeçalho/rodapé de página e divide nas seções numeradas do manual, com hash por seção | `policies_extracted.json` / `.md` |
| `build_policies` | Valida a curadoria contra a extração (ver [Manual de políticas](#manual-de-políticas)) e publica as seções com palavras-chave e custo em tokens | `policies.json` |
| `embed_index` | Monta os 75 documentos de busca (produtos sem descrição suja + seções do manual) e grava os vetores. Reaproveita vetores de textos que não mudaram; sobe o servidor de embeddings se preciso; sem modelo, grava índice vazio e a busca fica só lexical | `vectors.db` |

**Como o orquestrador decide o que rodar:**

```mermaid
flowchart TD
    A([etapa na ordem topológica]) --> B{alguma dependência<br/>falhou?}
    B -->|sim| SK[skipped]
    B -->|não| F[calcula fingerprint:<br/>hash das entradas + código da etapa<br/>+ versão + fingerprints das dependências]
    F --> C{igual ao da última<br/>execução e saídas<br/>íntegras?}
    C -->|sim, sem --force| UP[up_to_date · pula]
    C -->|não| R[executa]
    R --> OK{sucesso e saídas<br/>declaradas existem?}
    OK -->|sim| W[ok · grava linhagem]
    OK -->|não| FL[failed · dependentes pulam,<br/>independentes seguem]
```

- **Incremental por conteúdo:** só refaz o que mudou. Se uma etapa refaz e gera o mesmo conteúdo, as
  seguintes continuam em dia. Mudar o código de uma etapa, ou de um módulo que ela declara usar (por exemplo
  as regras de `rules.py`), já a invalida, sem precisar mexer na versão.
- **Linhagem:** `data/processed/manifest.json` registra, por etapa, o hash de cada entrada e saída, o
  fingerprint, a duração, as métricas e o erro. Saídas adulteradas são detectadas.
- **Robusto a falhas:** o estado é salvo após cada etapa, com escrita atômica. Uma falha no meio não perde o
  progresso e não deixa estado inconsistente.
- **Portável:** cada etapa é uma função `f(ctx) -> métricas` que não depende do runner, e corresponde 1:1 a
  uma task do Airflow ou do Dagster.

## Manual de políticas

O PDF é a fonte, mas o agente consome uma **versão curada e versionada** (`data/curated/politicas/*.md`):
uma seção por tema, revisada à mão, com front matter (id, seções do PDF de origem, tipo, palavras-chave) e
**notas de curadoria** para cada decisão. O pipeline impede que curadoria e PDF se desencontrem.

```mermaid
flowchart LR
    PDF[politicas_da_loja.pdf] --> X[extract_policies<br/>seções + hash]
    X --> B{build_policies}
    CUR[curadoria .md<br/>+ _source.yaml] --> B
    B -->|tudo confere| J[policies.json]
    B -->|seção do PDF mudou<br/>desde a curadoria| F1[falha: aponta seção e arquivo<br/>→ revisar e `emporio policies lock`]
    B -->|número do PDF ausente<br/>na curadoria| F2[falha: lista seção e valores]
    B -->|seção sem curadoria| F3[falha]
```

- **Nenhum fato se perde na revisão:** todo número de cada seção do PDF (prazos, valores, horários,
  telefones, leis) precisa aparecer na seção curada correspondente. Na primeira execução, essa verificação
  pegou o rodapé "Versão 2.1 / 2025", que tinha ficado de fora.
- **PDF novo não passa despercebido:** `_source.yaml` guarda o hash de cada seção usada na curadoria. Se o PDF
  mudar, o pipeline aponta exatamente quais seções e arquivos revisar.
- **Decisões explícitas** (notas de curadoria): os dois telefones com seus papéis (3341-4444 cadastral,
  3321-4500 WhatsApp), o e-mail sem acento, e a regra de parcelamento da §3.1 prevalecendo sobre a tabela.
- **Tipos:** `policy` (regra para o cliente) e `guideline` (instrução para o atendente, como tom de voz e
  fluxo). O manual inteiro tem ~3,2 mil tokens estimados, então cabe até no contexto de 8k do Bonsai.
- **Busca por assunto** (`PolicyStore.search`): determinística, por palavras-chave curadas (inclusive
  coloquiais, como "me arrependi" e "dá pra parcelar"), título e corpo. Abaixo de um limiar mínimo, não
  retorna nada, porque é melhor não pré-buscar do que pré-buscar a seção errada. É a base da detecção de
  assunto; a busca semântica entra no próximo commit.

## Busca híbrida

```mermaid
flowchart LR
    Q([pergunta]) --> L[lexical<br/>palavras e palavras-chave<br/>curadas]
    Q --> EM{servidor de<br/>embeddings ok?}
    EM -->|sim| V[vetorial<br/>embeddinggemma<br/>cosseno ≥ 0,30]
    EM -->|não| D[degrada: só lexical<br/>tenta de novo na<br/>próxima pergunta]
    L --> F["fusão RRF<br/>Σ 1/(60 + posição)"]
    V --> F
    F --> H[resultados com a origem<br/>de cada um: lexical#2 vector#1]
    D --> H
```

| Pergunta | Lexical sozinha | Vetorial sozinha | Híbrida |
|---|---|---|---|
| "Takamine GD20" | ✔ GD20 | ✔ GD20 | ✔ GD20 (1º nas duas) |
| "como faço pra vocês esquecerem meu cadastro?" | ✘ nada | ✔ privacidade | ✔ privacidade |
| "violão que dá pra ligar na caixa de som" | ✘ eletroacústico em 2º | ✘ eletroacústico em 6º | ✔ eletroacústico de aço em 1º |
| "violão que dá pra ligar na caixa de som, com cordas de aço" | ✘ violão sem eletrônica | ✘ violão sem eletrônica | ✘ eletroacústico em 2º |
| "receita de bolo de cenoura" | nada | nada (abaixo do limiar) | nada |

- **Degradação graciosa:** sem modelo baixado, com o serviço fora do ar ou com índice de outro modelo, a busca
  continua funcionando só com a parte lexical, e informa o motivo.
- **Explicável:** cada resultado traz a posição que teve em cada busca.
- **Limiar calibrado** com o modelo real: resultados vetoriais com similaridade abaixo de 0,30 não contam
  (ver limitações).
- `uv run emporio search "pergunta"` sobe o servidor de embeddings se preciso e o encerra ao final.

## Catálogo

`CatalogService` (`src/emporio/domain/catalog.py`) concentra as regras do catálogo. O LLM nunca calcula preço
nem decide disponibilidade: recebe o resultado pronto, com uma nota de orientação quando é preciso.

| Regra | Origem | Como fica |
|---|---|---|
| Preço promocional sempre com o original e o percentual | manual §6.2 | `R$ 5.519,08 na promoção Desconto Exclusivo (8% de desconto sobre R$ 5.999,00)` |
| PIX com 5% de desconto, **mas não sobre promoção** | §3 e §6.2 | GD20 (promoção inativa): `R$ 2.199,00 (ou R$ 2.089,05 no PIX)`; produto em promoção: sem PIX |
| Filtros pelo preço efetivo | §6.2 | Ohana CK-20 (R$ 549 com 20%) aparece em "ukulele até 450" |
| Esgotado, descontinuado ou em breve → informar e sugerir alternativas | §7.3 | Até 3 disponíveis da mesma categoria, priorizando o mesmo tipo ("Dreadnought", "7 Cordas") e preço próximo |
| A loja não vende acessórios | §1 e §7.1 | "vocês vendem cordas?", "corda de violão", "amplificador pra guitarra" → redirecionar; "violão de **7 cordas**" continua busca de instrumento |
| Categorias sem produto | relatório de dados | "tem flauta?", "quero um violino" → informar com transparência |

**Pensado para um LLM pequeno**, que pode mandar a pergunta inteira em vez de preencher os parâmetros:
- **Filtros inferidos do texto:** "Quais opções de violões disponíveis custando até R$1000?" vira categoria
  Violões + até R$ 1.000 (12 opções, ordenadas por preço). "entre 5 mil e 8.000" também é entendido.
- **Nome aproximado:** "Quanto custa o Takamine GD20?", "takamine gd 20" e "TAKAMINE GD-20" encontram o
  mesmo produto. As palavras de pedido ("quanto custa o…") são descartadas antes da comparação.
- **Ambiguidade explícita:** "takamine" e "martin d28" devolvem os candidatos e a orientação de perguntar
  qual. "Quanto custa um violão?" (categoria, não modelo) orienta a usar a busca.

```mermaid
flowchart TD
    Q([nome, id ou pergunta]) --> ID{id?}
    ID -->|sim| F[encontrado]
    ID -->|não| AC{acessório?}
    AC -->|sim| X[acessório: redirecionar]
    AC -->|não| CAT{só cita categoria?}
    CAT -->|sim| S[orientar busca por categoria]
    CAT -->|não| FZ[similaridade com os nomes<br/>sem palavras de pedido]
    FZ -->|claro vencedor ou<br/>único candidato próximo| F
    FZ -->|vários próximos| AMB[ambíguo: até 3 candidatos]
    FZ -->|nada próximo| NF[não encontrado: não inventar]
    F --> DISP{vendável?}
    DISP -->|não| ALT[nota + alternativas]
    DISP -->|sim| OK[preço com promoção/PIX]
```

## Pedidos e regras da loja

Tudo calculado em código e testado (`src/emporio/domain/orders.py`, `calculators.py`, `store_hours.py`).
O LLM recebe a decisão pronta (elegível ou não, até quando, o que fazer em seguida) e só redige a resposta.

**Consulta de pedido (LGPD):** exige o número do pedido **e** o e-mail ou telefone do titular *daquele*
pedido. Qualquer falha (pedido inexistente, contato de outra pessoa, contato incompleto) devolve a mesma
mensagem neutra, que não revela se o pedido existe. O cliente com e-mail duplicado na base só abre o próprio
pedido.

**Troca e devolução** (manual §4 e §8), contadas a partir da `REFERENCE_DATE`:

```mermaid
flowchart TD
    A([pedido verificado + motivo]) --> S{status}
    S -->|cancelado| C[nada a devolver]
    S -->|pendente, confirmado<br/>ou enviado| N[ainda não entregue:<br/>prazo conta do recebimento]
    S -->|entregue| M{motivo}
    M -->|arrependimento| R{até 7 dias do<br/>recebimento?}
    R -->|sim| R1[devolução: frete por conta da loja,<br/>reembolso em até 10 dias úteis]
    R -->|não| R2[fora do prazo: perguntar<br/>se há defeito]
    M -->|defeito| D{até 30 dias<br/>da compra?}
    D -->|sim| D1[troca por defeito]
    D -->|não| D2[garantia legal de 90 dias<br/>+ garantia do fabricante]
    M -->|preferência| P{até 7 dias?}
    P -->|sim| P1[troca sujeita a estoque]
    P -->|não| P2[fora do prazo]
    M -->|não informado| Q[perguntar o motivo]
```

Com a data de referência padrão (25/03/2026), **nenhum pedido entregue ainda está dentro dos 7 dias de
arrependimento** (o mais recente foi entregue há 36 dias). Por isso o exemplo "me arrependi, posso
devolver?" responde "fora do prazo" e já oferece o caminho certo: o pedido 7 passou da troca por defeito,
mas a **garantia legal vale até 18/05/2026**. Os casos dentro do prazo são testados com outras datas de
referência. O pedido 8 aparece como **atrasado** (previsão de entrega vencida) e é encaminhado à equipe.

| Regra | Como fica |
|---|---|
| Pagamento (§3, §3.1) | PIX com 5% (só sem promoção), débito, boleto; parcelamento até 3x com parcela ≥ R$ 50, 4–6x ≥ R$ 80, 7–12x ≥ R$ 100. R$ 599,90 → até 6x de R$ 99,98; "10x?" é recusado com o motivo. Combinar formas só acima de R$ 2.000 |
| Frete (§5) | Campo Grande: motoboy, 1 a 3 dias úteis, grátis a partir de R$ 500 (senão R$ 35). Outras cidades: SEDEX, Jadlog e PAC com prazos e valor pelo CEP. Baterias, pianos e contrabaixos acústicos: frete especial |
| Horário (§2) | Seg–sex 09–18h, sáb 09–13h, domingo e feriados fechado, no fuso de Campo Grande. Feriados nacionais, Sexta-feira Santa (calculada a partir da Páscoa), 11/10 (MS) e 26/08 (Campo Grande). Diz quando a loja abre de novo |

## Servidor MCP (a API)

`src/emporio/mcp_server/` expõe o domínio pelo [Model Context Protocol](https://modelcontextprotocol.io)
(SDK oficial `mcp` 2.x). É uma camada fina: cada tool traduz a chamada para o domínio e devolve **saída
estruturada** com um campo `resposta` já redigido pelas regras e, quando preciso, uma `orientacao` para o
agente.

```mermaid
flowchart LR
    subgraph Clientes
        A[Agente da loja<br/>commit 11]
        CD[Claude Desktop /<br/>Claude Code]
        IN[MCP Inspector]
    end
    A & CD & IN -->|stdio ou<br/>Streamable HTTP| S
    subgraph S[emporio mcp]
        T[8 tools<br/>somente leitura]
        R[resources<br/>políticas · categorias]
        P[prompt<br/>persona_atendente]
    end
    T --> D[domínio:<br/>catálogo · pedidos · regras · políticas]
    R --> D
    D --> DB[(store.db ·<br/>policies.json ·<br/>vectors.db)]
```

| Tool | Para quê |
|---|---|
| `buscar_produtos(texto, categoria, preco_min, preco_max, apenas_disponiveis, limite)` | opções com preço, promoção e PIX; filtros inferidos do texto |
| `detalhar_produto(produto)` | um modelo (nome aproximado, pergunta inteira ou id), com alternativas se indisponível |
| `consultar_pedido(numero_pedido, email_ou_telefone)` | status, itens, total e rastreio, **só para o titular** |
| `verificar_devolucao(numero_pedido, email_ou_telefone, motivo)` | troca/devolução pelas regras do manual |
| `simular_pagamento(valor, preco_promocional, parcelas)` | PIX, débito, boleto e parcelamento válido |
| `calcular_frete(valor_pedido, cidade, produto_ou_categoria)` | Campo Grande, outras cidades, frete especial |
| `status_loja()` | aberta ou fechada agora, próxima abertura |
| `consultar_politica(pergunta, limite)` | seções do manual relevantes (busca híbrida) |

- **Resources:** `politicas://indice` (seções com **custo estimado em tokens**, para o orçamento de contexto),
  `politicas://{secao}` e `catalogo://categorias`.
- **Prompt** `persona_atendente`: a "Mel", com o tom da §7.1 e as regras que ela nunca quebra.
- **Segurança:** todas as tools são somente leitura (anotação `read_only_hint`); nenhuma lista clientes; erros
  inesperados chegam ao cliente sem detalhes internos; os logs mascaram e-mail e telefone.
- **Ciclo de vida:** `emporio mcp` sobe o servidor de embeddings junto e o encerra ao sair, inclusive com
  SIGTERM ou terminal fechado (há teste de regressão que falha sem essa proteção).

Para usar no Claude Desktop ou no Claude Code, aponte para o comando (stdio):

```json
{
  "mcpServers": {
    "emporio": {
      "command": "uv",
      "args": ["run", "--directory", "/caminho/para/ai-engineer-challenge", "emporio", "mcp"]
    }
  }
}
```

## Agente

`src/emporio/agent/` conecta o LLM ao servidor MCP. O agente só conhece a loja pelo protocolo MCP (em
processo ou via stdio): lista as tools, usa o prompt `persona_atendente` e chama as ferramentas.

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant A as Agente
    participant R as router.py
    participant M as Servidor MCP
    participant L as LLM (perfil)
    A->>M: persona_atendente + status_loja (início da conversa)
    C->>A: "quanto custa o GD20 e dá pra fazer em 10x?"
    A->>R: intenções e dados citados
    R-->>A: produto + pagamento · 10 parcelas
    A->>M: detalhar_produto("quanto custa o GD20")
    M-->>A: R$ 2.199,00 (ou R$ 2.089,05 no PIX) · sem promoção
    A->>M: simular_pagamento(2199, parcelas=10)
    M-->>A: 10x de R$ 219,90 sem juros…
    Note over A: budget.py encaixa persona, dados e histórico na janela
    A->>L: persona + histórico + resultados (como tool calls já respondidas), sem schemas
    L-->>A: resposta
    Note over A: valores em R$ conferidos com os dados
    A-->>C: "Sim: 10x sem juros de R$ 219,90. PIX: R$ 2.089,05…"
```

**Um turno:**
1. **Detecção de assunto** (`router.py`, palavras-chave sobre o texto normalizado): uma ou mais intenções
   (produto, catálogo, acessório, pedido, devolução, pagamento, frete, horário, política, saudação) e os
   dados citados (número do pedido, e-mail/telefone, valor, parcelas, cidade). Uma mensagem só com dados
   ("é o 8, telefone …") herda a intenção do turno anterior.
2. **Pré-busca** (`prefetch.py`): o host chama as tools do assunto via MCP e encadeia valores (o preço do
   produto encontrado alimenta a simulação de pagamento e o frete; numa pergunta seguinte sem produto, como "e
   em 10x?", vale o último produto da conversa). **Respostas prontas, sem LLM**, quando o modelo só atrapalharia:
   pedido ou devolução sem os dados do titular (pede só o que falta), acessório, reclamação, horário e contato
   (dado puro, lido das tools). Em todas elas o Bonsai errava: repetia a orientação interna, trocava telefone e
   WhatsApp, respondia "domingo?" com o horário de hoje. Nos demais casos, os resultados entram como uma chamada de tools já respondida, numa versão
   enxuta: os textos prontos (`resposta`, `resumo_preco`), a promoção explícita ("nenhuma; o desconto do
   PIX é forma de pagamento") e as orientações.
3. **Orçamento de contexto** (`budget.py`): ver [Orçamento de contexto](#orçamento-de-contexto).
4. **Loop**: o modelo responde. Se ainda pedir tools, o agente executa e devolve os resultados (até 5
   rodadas). Num modelo local, as definições de tools só vão quando a pré-busca não resolveu (assunto
   desconhecido ou falha).
5. **Preço sem fonte**: todo valor em R$ da resposta precisa aparecer nos dados do turno (tools, mensagem
   do cliente ou histórico). Se não aparecer, o modelo recebe uma correção; se insistir, vale a resposta
   de segurança.

**Proteções do loop:**
- argumento com JSON inválido, tool inexistente ou erro da tool viram mensagem para o modelo corrigir;
- nome de tool quase certo é corrigido (o Bonsai escreveu `detalhar_produtoArguments`);
- tool call escrita como JSON solto no texto é recuperada pelo cliente LLM;
- **resposta de segurança** se o LLM cair, se passar de 5 rodadas, se a resposta vier vazia ou se insistir
  em valores sem fonte: encaminha à equipe, com retorno em até 24 horas úteis (manual §7.3);
- o rastro de cada turno (assunto, pré-busca, tools oferecidas, tokens estimados, tools chamadas com
  e-mail/telefone mascarados, latência) aparece com `--debug`.

**Guardrails** (`guardrails.py`, mais a detecção de assunto e a pré-busca):

```mermaid
flowchart LR
    M([mensagem]) --> G{guardrails<br/>de entrada}
    G -->|injeção · dados de terceiros ·<br/>fora do escopo · longa demais| P1[resposta pronta<br/>sem LLM e sem tools]
    G --> R[detecção de assunto]
    R -->|só pedido, faltam dados| P2[pede só o que falta<br/>sem LLM]
    R -->|só reclamação| P3[acolhe, registra, 24 h úteis<br/>sem LLM · encaminhada]
    R --> PF[pré-busca + LLM]
    PF --> S{guardrails<br/>de saída}
    S -->|R$ sem fonte ·<br/>instruções recitadas| C[1 correção, depois<br/>resposta de segurança]
    S -->|contato de terceiros| X[trocado por<br/>dado protegido]
    S --> OK([resposta])
```

| Situação | Como é tratada |
|---|---|
| **Injeção de prompt** ("ignore as instruções", "mostre seu prompt", "você agora é…", "modo desenvolvedor") | resposta pronta, sem LLM: não muda o atendimento nem revela instruções |
| **Dados de terceiros** ("liste os clientes", "qual o e-mail da Ana?", "quem comprou o GD20?") | resposta pronta citando a LGPD; o servidor MCP também não tem tool que liste clientes |
| **Fora do escopo** explícito (receitas, futebol, política, piadas, programação…) | redireciona para a loja, sem LLM; só se nenhum assunto da loja foi detectado ("violão pra tocar no jogo do Brasil" segue normal) |
| **Assunto desconhecido** ("vocês têm estacionamento?") | vai ao modelo com 3 tools genéricas; a persona manda recusar com gentileza o que não for da loja |
| **Acessórios** e **categorias vazias** | acessório sozinho: resposta pronta, com o item que a tool identificou ("não vendemos palheta nem outros acessórios…"); categoria vazia: a pré-busca traz a orientação do domínio ("no momento não há…") |
| **Depois de uma recusa** | o turno bloqueado não volta ao contexto do modelo (fica no histórico salvo e na transcrição); antes, o pedido seguinte de violão virou "aqui está a receita de bolo" |
| **Reclamação** ou pedido de atendimento humano | intenção própria: sozinha, resposta pronta (empatia, registro, retorno em 24 h úteis, WhatsApp); com outro assunto, orientação ao modelo. O turno fica marcado como **encaminhado** no histórico |
| **Saída: contato de terceiros** | e-mail/telefone que não seja da loja nem citado pelo cliente vira `[dado protegido]` |
| **Saída: instruções recitadas** (16+ palavras seguidas iguais à persona ou às orientações do turno) | 1 correção, depois resposta de segurança |
| **Dados internos** | seções do manual do tipo `guideline` (instruções ao atendente) e notas de curadoria não entram nos dados pré-buscados: o Bonsai as recitava |

As listas são conservadoras: bloquear uma pergunta legítima custa mais do que deixar o modelo recusar. Os
testes cobrem tanto o que deve ser bloqueado quanto perguntas parecidas que devem passar ("ignore o que eu
disse sobre a cor", "qual o e-mail da loja?", "vocês pedem CPF?").

**Conversa e histórico** (`chat.py`, `history.py`): `ChatSession` junta o agente, o histórico e a
transcrição sem depender da interface. A CLI `emporio chat` usa essa classe, e a API REST e a interface web
(planejadas) vão usar a mesma.

```mermaid
erDiagram
    sessions ||--o{ messages : tem
    sessions {
        text id PK "8 hex"
        text profile "perfil de LLM"
        text created_at
        text updated_at
    }
    messages {
        int id PK
        text session_id FK
        text role "user | assistant"
        text content "e-mail e telefone mascarados"
        text meta "assunto, tools, chamadas, latência, fallback"
    }
```

- **Retomar** (`--session`): voltam as últimas `HISTORY_WINDOW` mensagens (padrão 20, sempre começando por
  uma do cliente) e o assunto da última pergunta. O orçamento de contexto ainda corta o que não couber.
- **Privacidade:** e-mail e telefone nunca são gravados (`[e-mail]`, `[telefone]`); número de pedido e
  preços ficam. Ao retomar uma sessão, o cliente informa o contato de novo para ver um pedido, como num
  atendimento novo.
- **Transcrição** (`--transcript f.md`): cada turno com as tools usadas (pré-busca ou chamadas pelo
  modelo), chamadas ao LLM e latência. As conversas de exemplo do projeto vão sair daqui.

**Resultado com o Bonsai real em CPU** (mesma máquina, i5-8265U):

| Pergunta | Antes (commit 11: 8 tools no prompt) | Agora (detecção de assunto + pré-busca) |
|---|---|---|
| "Quanto custa o Takamine GD20?" | 2 chamadas, ~2 mil tokens, **~219 s**, chamou o preço do PIX de "promocional" | 1 chamada, 854 tokens, **~65 s**, preço e PIX certos, "não há promoção" |
| "quanto custa o GD20 e dá pra fazer em 10x?" | — | 1 chamada, ~970 tokens (estimativa), ~105 s, 10x de R$ 219,90 e PIX certos |
| "posso devolver?" | — | 1 chamada, 1.575 tokens, ~150 s, regra dos 7 dias, reembolso em 10 dias úteis, frete por conta da loja |
| "cadê meu pedido?" → "é o 8, telefone …" (`emporio chat`) | — | 1º turno sem LLM (0 s) pedindo os dados; 2º com `consultar_pedido` pré-buscado, 1 chamada, ~105 s: status, itens, rastreio e o atraso com retorno em 24 h úteis |

O que sobra de latência é o processamento do prompt em CPU (~17–20 tokens/s neste notebook). Também ajudou
usar só os núcleos físicos: 4 threads processam o prompt 35% mais rápido que as 8 do modo automático
(19,8 × 14,6 tokens/s).

## Avaliação

`src/emporio/evals/` mede três coisas, com gabaritos versionados em `evals/` e resultados em
[`evals/resultados/`](evals/resultados/README.md) (um JSON por rodada e um resumo gerado):

```mermaid
flowchart LR
    subgraph E1[Cenários · evals/scenarios.yaml]
        S[18 conversas, 20 turnos<br/>asserções por turno] --> O[oráculo<br/>sem LLM]
        S --> B[Bonsai-1.7B]
        S --> Q[Qwen3-1.7B]
    end
    subgraph E2[Classificador · evals/intents.yaml]
        I[59 frases inéditas<br/>intenções + bloqueios]
    end
    subgraph E3[Busca · evals/retrieval.yaml]
        R[20 perguntas coloquiais] --> LX[lexical]
        R --> HY[híbrida]
    end
```

**Cenários de ponta a ponta** (o agente completo: assunto → pré-busca → LLM → guardrails). Cobrem os exemplos do
enunciado (preço do GD20, "violões até R$1000"), duas intenções numa frase, promoção, esgotado com
alternativas, categoria vazia, acessório, pedido em duas mensagens, contato errado (LGPD), **devolução fora do
prazo em dois turnos**, regras da loja e os guardrails. As asserções são por turno: assunto, tools usadas,
texto que precisa (ou não pode) aparecer, bloqueio, "sem LLM", encaminhamento e "sem resposta de segurança".

O **oráculo** é um "modelo" que só devolve os dados que recebeu das tools. Ele mede o encanamento (o dado
certo chega ao modelo?), roda em segundos e está no CI (`tests/test_evals.py`). A diferença entre o oráculo e
um modelo real é o erro de redação do modelo.

| Perfil | Cenários aprovados | Turnos com LLM | Latência por turno com LLM (p50 · p95) | Prompt médio |
|---|---|---|---|---|
| oráculo (sem LLM) | **18/18** | 12/20 | — | 934 tokens |
| **Qwen3-1.7B** Q8_0 (1,8 GB) | **18/18** | 13/20 | **68 s · 123 s** | 940 tokens |
| Bonsai-1.7B 1 bit (248 MB, baseline) | 17/18 | 13/20 | 108 s · 207 s | 940 tokens |

<sub>CPU i5-8265U (4 núcleos), 8k de contexto, mesmo runtime llamafile, mesma pré-busca. Os turnos sem LLM são
guardrails e respostas prontas. A rodada com os modelos é anterior à resposta pronta de contato (commit 17), por
isso tem um turno com LLM a mais que o oráculo; as latências desta rodada saíram ~1,5× as da anterior (57 s e 65 s
de p50) porque a máquina estava mais carregada.</sub>

- **Bonsai falhou em 1:** no `produto_esgotado`, disse que o Giannini GF-3D "já está disponível", mas ele está
  esgotado, e a tool disse isso (na rodada anterior ele acertou esse cenário; a temperatura é 0,2). É o erro mais
  grave que vimos e será coberto por uma pós-checagem de status (próximo commit).
- **Evolução do Bonsai:** 16/18 na 1ª rodada. As duas falhas eram de redação sobre dados certos: no `acessorio`,
  repetiu a orientação interna; na devolução fora do prazo, escreveu "há 0 dias restantes" em vez de dizer que o
  prazo acabou. Depois dos ajustes (acessório com resposta pronta, devolução com "não é mais possível" explícito e
  sem contador de dias vencido), passou nos dois.
- **Qwen3-1.7B** (mesma arquitetura do Bonsai, sem a quantização de 1 bit) passou em todos nas duas rodadas e foi
  mais rápido: a quantização extrema economiza 7× de disco e memória, mas custa qualidade de redação.
- **Asserções corrigidas depois de ver as respostas** (cada caso registrado como nota no resultado, com a resposta
  e a falha original): "está disponível" reprovava a resposta certa "não está disponível"; "Não tenho saxofone" e
  "não há mais tempo para devolver" eram formas certas que a lista não previa. Os turnos foram reavaliados a partir
  das respostas gravadas, sem rodar o modelo de novo.
- **`.env` local** com `LLM_BASE_URL`/`LLM_MODEL` sobrescrevia qualquer perfil (na 1ª rodada o Qwen3 subiu com o
  nome do Bonsai); as sobrescritas agora valem só para o perfil ativo.
- O oráculo achou **3 falhas de encanamento** antes de qualquer modelo rodar, todas corrigidas: o motivo da
  devolução se perdia quando a mensagem seguinte era só o e-mail; devolução com número do pedido e sem contato
  agora pede só o que falta, sem LLM; e "frete de um pedido de R$ 600" disparava consulta de pedido.

**Classificador de intenção e guardrails** (59 frases escritas para a avaliação, diferentes das dos testes):
a 1ª medição deu **88%** de acerto exato (52/59). Quatro erros eram lacunas claras de vocabulário ("funciona"
marcava horário em "como funciona o reembolso", "tampa rachada" não era defeito, "quanto tempo demora pra
chegar" não era frete, "jogo de ontem" não era fora do escopo) e foram corrigidos: **95%** (56/59), 0,4 ms por
frase. Depois disso o conjunto deixou de ser cego. Os 3 erros restantes ficam como limitação: "garantia de
um violão" também dispara catálogo, "manutenção em instrumento" vira catálogo em vez de "geral" e "me
recomenda um filme" passa como catálogo (palavra "recomenda").

**Busca** (os retrievers de produção, 20 perguntas coloquiais com gabarito, o mesmo da escolha do modelo de
embeddings):

| Busca | hit@1 | hit@3 | MRR | Latência p50 |
|---|---|---|---|---|
| lexical | 70% | 75% | 0,729 | 0,9 ms |
| **híbrida** (lexical + embeddinggemma, RRF) | **90%** | **95%** | **0,925** | 71 ms |

## Conversas de exemplo

Cinco conversas com o agente real e o **Bonsai-1.7B**, sem edição, comentadas turno a turno em
[`examples/README.md`](examples/README.md) (onde o modelo erra, o comentário diz o quê). Regravar:
`./scripts/record_examples.sh` (ou `PROFILE=qwen3-1.7b …`).

| # | Conversa | Destaque |
|---|---|---|
| [01](examples/01_catalogo_ate_1000.md) | Violões até R$ 1.000 → Tagima Memphis em 3x | exemplo do enunciado; preço do produto encontrado alimenta a simulação |
| [02](examples/02_endereco_horario.md) | Endereço → abrem no domingo? | contato e horário como dado puro, respostas prontas |
| [03](examples/03_preco_gd20.md) | GD20 → 10x no cartão → desconto no PIX | pergunta do enunciado; turnos seguintes sem citar o produto |
| [04](examples/04_devolucao.md) | Devolução do pedido 7 (arrependimento → defeito) | **não trivial**: verificação do titular, prazos vencidos, caminho da garantia |
| [05](examples/05_fora_do_escopo_acessorios.md) | Palheta → corda → receita de bolo → violão para iniciante | acessórios, fora do escopo e retomada depois da recusa |

As gravações também serviram de teste: as primeiras expuseram quatro bugs do agente (turno recusado contaminando
o seguinte, manual demais junto da simulação de pagamento, horário e contato mal respondidos), corrigidos com
testes antes da versão final.

## Camada de dados

```mermaid
flowchart LR
    subgraph Domínio
        M[modelos imutáveis<br/>Product · Order · Customer…]
    end
    P[[Repository<br/>Protocol]] --> M
    P --> SQ[SqliteRepository<br/>somente leitura · SQL parametrizado]:::done
    P -.escala.-> PG[PostgresRepository]
    CT{{suíte de contrato<br/>tests/contract}} -.valida.-> SQ
    CT -.valida.-> PG
    SQ --> DB[(store.db)]
    classDef done fill:#bbf7d0,stroke:#15803d,color:#000
```

- **As regras viram restrições do banco.** Por exemplo, um produto "disponível" sem estoque, um pedido
  pendente com rastreio, uma descrição não confiável exposta ou um preço promocional acima do cheio são
  rejeitados pelo próprio SQLite (`CHECK`). Há teste para cada violação.
- **`Repository`** é a interface de leitura usada pelo domínio. **Não existe método para listar clientes**
  (LGPD): dados pessoais só são lidos pontualmente, para verificar quem consulta o próprio pedido, e o
  modelo `Customer` nem carrega o nome completo.
- **Adaptador SQLite:** conexão somente leitura, uma conexão por thread e consultas sempre parametrizadas
  (há teste de injeção).
- **Suíte de contrato** (`tests/contract/repository_contract.py`): 22 testes que qualquer adaptador precisa
  passar. É ela que vai validar o adaptador Postgres.

## Escalabilidade e robustez

O princípio é **leve por padrão, escala por configuração, mesma interface, mesmos testes**. A banca roda tudo
com um comando, sem servidores. O caminho de crescimento fica implementado e testado atrás das mesmas
interfaces.

| Peça | Padrão (1 comando, sem servidor) | Escala (por configuração) | Status |
|---|---|---|---|
| Orquestração | DAG próprio, incremental, com linhagem | Airflow/Dagster (mesmas funções de etapa) | ✅ DAG · template Airflow *(planejado)* |
| Configuração | `.env` validado na inicialização (falha cedo) | variáveis de ambiente / secret manager | ✅ |
| Logs | texto no terminal | JSON, uma linha por evento (`LOG_FORMAT=json`) | ✅ |
| Dados | SQLite somente leitura atrás de `Repository` | Postgres (`STORE_BACKEND=postgres`), mesma suíte de contrato, no CI | ✅ |
| Busca | híbrida (lexical + vetores em SQLite, fusão RRF) atrás de `Retriever`; degrada para lexical | pgvector com índice HNSW (`VECTOR_BACKEND=pgvector`), mesma suíte de contrato, no CI | ✅ |
| Embeddings | embeddinggemma-300m no runtime llamafile, em CPU, atrás de `Embedder` | API de embeddings ou GPU | ✅ |
| API | servidor MCP via stdio ou Streamable HTTP (local, sem autenticação) | MCP HTTP atrás de API gateway com OAuth, réplicas | ✅ MCP · auth *(planejado)* |
| Serviços locais | sobem sob demanda e encerram ao final (também com SIGTERM/SIGHUP); porta ocupada → porta livre; log em arquivo | containers: `deploy/docker-compose.yml` (LLM, embeddings, pipeline, MCP; perfil `scale` com Postgres) | ✅ |
| LLM | perfil `bonsai` (local, CPU) | outros perfis: modelos maiores, GPU ou API | ✅ perfis · online *(referência)* |

### Escala: containers e Postgres + pgvector

```mermaid
flowchart LR
    subgraph default[compose padrão]
        LLM[llm<br/>llamafile · Bonsai] 
        EMB[embeddings<br/>llamafile · embeddinggemma]
        PIPE[pipeline<br/>one-shot, incremental] --> VOL[(volume<br/>data/processed)]
        EMB --> PIPE
        VOL --> MCP[mcp :8000<br/>SQLite]
        EMB --> MCP
        CHAT[chat<br/>docker compose run] --> LLM
    end
    subgraph scale[--profile scale]
        PG[(postgres<br/>pgvector · HNSW)]
        LOAD[db-load<br/>one-shot] --> PG
        VOL --> LOAD
        PG --> MCPS[mcp-scale :8001<br/>sem estado → réplicas]
        EMB --> MCPS
    end
```

```bash
./scripts/setup.sh                                                  # modelos em ./models (montados nos containers)
docker compose -f deploy/docker-compose.yml up -d                   # padrão: LLM, embeddings, pipeline, MCP :8000
docker compose -f deploy/docker-compose.yml run --rm chat           # conversa no terminal, dentro da rede do compose
docker compose -f deploy/docker-compose.yml --profile scale up -d   # + Postgres/pgvector e MCP :8001 lendo do banco
uv run python scripts/load_test.py --url http://127.0.0.1:8001/mcp  # carga no MCP via HTTP
```

- **Imagem** (`deploy/Dockerfile`): base Debian fixada por digest, uv na mesma versão do setup, CPython
  gerenciado e dependências exatas do `uv.lock`, usuário sem privilégios. O `.dockerignore` é uma lista de
  permissão: `.env`, `.venv` e anotações privadas nunca entram. Os **modelos não entram na imagem** (787 MB
  sem eles): são montados de `./models`, já verificados por SHA-256 pelo setup.
- **Postgres + pgvector** (`pip`: extra `scale`): `PostgresRepository` e `PgVectorIndex` implementam as mesmas
  interfaces e passam nas **mesmas suítes de contrato** do SQLite (`tests/test_postgres.py`, job `postgres` do CI
  com a mesma imagem do compose). O esquema espelha os `CHECK`s do SQLite com tipos nativos (`DATE`, `BOOLEAN`,
  `vector`); `emporio db load` copia o resultado do pipeline numa transação única. O ranking do pgvector é
  idêntico ao do índice SQLite (mesmos documentos, mesmas similaridades até a 4ª casa).
- **O servidor MCP não tem estado** (cada chamada lê do repositório): com o Postgres, várias réplicas atrás de
  um balanceador servem o mesmo banco. O caminho de produção está em [`docs/architecture.md`](docs/architecture.md).

**Teste de carga** (`scripts/load_test.py`: 20 sessões MCP simultâneas × 25 chamadas = 500, mistura de catálogo,
pedido, política, pagamento, frete e horário; mesmo notebook, com o LLM ocioso):

| Backend | Erros | Vazão | p50 | p95 | Sem as buscas semânticas: vazão · p95 |
|---|---|---|---|---|---|
| SQLite (`mcp` :8000) | 0/500 | 48–68 chamadas/s | 34–124 ms | 1,1–1,8 s | **233 chamadas/s · 227 ms** |
| Postgres + pgvector (`mcp-scale` :8001) | 0/500 | 43–57 chamadas/s | 53–112 ms | 1,4–1,7 s | **267 chamadas/s · 185 ms** |

<sub>Faixas de duas rodadas (o notebook tinha outras cargas). Resultados em `docs/load_test/`.</sub>

O banco não é o gargalo: sem as duas tools que embedam a pergunta (`buscar_produtos`, `consultar_politica`), a
vazão sobe ~4× e o Postgres fica igual ou melhor que o SQLite. O limite é o **servidor de embeddings, um processo
em CPU** atendendo todas as buscas. Em produção: réplicas dele (ou GPU), cache de embeddings de perguntas
frequentes, e o LLM escalado à parte (é ele que leva segundos por resposta).

## Modelos locais

```mermaid
flowchart LR
    subgraph SRC[Fontes oficiais fixadas]
        M1[mozilla-ai/llamafile<br/>release 0.10.6]
        M2[prism-ml/Bonsai-1.7B-gguf<br/>commit 210a9e9]
        M3[ggml-org/embeddinggemma-300M-GGUF<br/>commit 0f741b5]
    end
    M1 -->|SHA-256| R[models/runtime/<br/>llamafile-0.10.6-thin]
    M2 -->|SHA-256| G[Bonsai-1.7B.gguf]
    M3 -->|SHA-256| E[embeddinggemma-300M-Q8_0.gguf]
    R --> S1[start_llamafile.sh<br/>MODE=chat · CPU]
    G --> S1
    R --> S2[start_llamafile.sh<br/>MODE=embedding · CPU]
    E --> S2
    S1 --> API1[/:8080/v1/chat/completions/]
    S2 --> API2[/:8081/v1/embeddings/]
```

O mesmo runtime oficial serve os dois modelos. Detalhes, licenças e a política de CPU/GPU por modo:
[`models/README.md`](models/README.md).

### LLM de chat: Bonsai-1.7B

- **Bonsai-1.7B** (PrismML): arquitetura Qwen3, 1.72B params, quantização de 1 bit (~248 MB), contexto de
  treino 32k (usamos 8k).
- **Runtime e GGUF separados**, ambos de fontes oficiais com checksum. O `.llamafile` único usado no início
  do desenvolvimento é exatamente essa combinação: o GGUF embutido nele tem SHA-256 idêntico ao oficial.
- **Medições** (exploratórias, não fazem parte do CI; 4 perguntas × 2 execuções, 2 tools no prompt,
  máquina de desenvolvimento com 8 vCPU, Intel UHD 620 e NVIDIA MX150):

  | Configuração | Tool calls corretas | Tempo médio por chamada |
  |---|---|---|
  | `--reasoning off`, **CPU** (`--gpu disable`, padrão do script) | 4/8 | ~18,4 s |
  | `--reasoning off`, GPU automática (iGPU via Vulkan) | 4/8 | ~4,8–6,7 s |
  | `--reasoning on`, GPU automática | 4/8 | ~6,0 s |

  O llamafile usa a GPU automaticamente quando encontra uma. As primeiras medições foram registradas como
  "sem GPU", mas o log mostrou modo GPU; a tabela acima já está corrigida.

  ⚠️ **Segunda correção:** com prompts curtos como esses, a iGPU acertava o mesmo que a CPU, e por isso o
  chat chegou a usar `GPU=auto` por padrão. Com o prompt real do agente (persona + 8 tools, ~2 mil tokens),
  **a iGPU (Vulkan) passou a gerar lixo** ("texto texto texto…"), enquanto o mesmo prompt em CPU saía
  coerente. É erro de cálculo, não de desempenho. **O padrão voltou a ser CPU** nos dois modos;
  `GPU=auto` fica como opção para placas validadas.

### Perfis de LLM

`config/llm_profiles.yaml` define os modelos que o agente pode usar. Todos falam a API compatível com
OpenAI; trocar de modelo é mudar `LLM_PROFILE`, sem tocar no código.

| Perfil | Modelo | Contexto | Status |
|---|---|---|---|
| `bonsai` (padrão) | Bonsai-1.7B local, llamafile | `auto` (lido de `/props`: 8192) | ✅ testado; sobe sozinho se estiver fora do ar |
| `qwen3-1.7b` | Qwen3-1.7B Q8_0 local, llamafile, porta 8082 (download opcional: `EVAL_MODELS=1`) | `auto` | ✅ testado na avaliação: 18/18 cenários × 17/18 do Bonsai |
| `ollama-qwen2.5-3b` | Qwen2.5 3B via Ollama | 32768 | referência (Ollama não instalado aqui) |
| `anthropic` | Claude Haiku (endpoint compatível com OpenAI) | 128000 | referência (precisa de `ANTHROPIC_API_KEY`) |
| `openai` | `OPENAI_MODEL` | 128000 | referência (precisa de `OPENAI_API_KEY`) |

- **Chaves nunca ficam no arquivo:** `api_key_env` aponta a variável de ambiente; sem ela, o erro diz qual
  variável definir.
- **Contexto por modelo:** `auto` lê o `n_ctx` do servidor (`/props`); o orçamento de contexto do agente usa
  esse número.
- **Sobrescritas `LLM_*`** (`LLM_BASE_URL`, `LLM_MODEL`…) valem só para o perfil ativo (`LLM_PROFILE` ou o
  padrão); `--profile outro` usa o perfil como está no arquivo.
- **Tolerância a modelos pequenos:** o cliente normaliza argumentos de tool com JSON inválido ou entre cercas
  de código, recupera tool calls escritas como texto (`<tool_call>…</tool_call>`) e remove blocos `<think>`.
  Um argumento inválido vira um erro devolvido ao modelo, em vez de quebrar a conversa.
- `uv run emporio llm check` testa um perfil de ponta a ponta: contexto (8192 no Bonsai), uma resposta curta
  e uma chamada de ferramenta.

### Embeddings: embeddinggemma-300m

Escolhido numa [avaliação com 4 candidatos](docs/embeddings_eval.md) (20 perguntas com gabarito sobre os 65
produtos e as 10 seções de políticas, tudo em CPU):

| Modelo | Acerto no 1º | Acerto no top 3 | Consulta | Download | Licença |
|---|---|---|---|---|---|
| Busca lexical (atual) | 70% | 70% | ~0 ms | — | — |
| **embeddinggemma-300m** ✅ | **90%** | **95%** | **104 ms** | **334 MB** | Gemma Terms |
| bge-m3 | 85% | 95% | 170 ms | 635 MB | MIT |
| Qwen3-Embedding-0.6B | 70% | 90% | 386 ms | 639 MB | Apache-2.0 |
| nomic-embed-text-v2-moe | inviável: contexto de 512 tokens | | | 512 MB | Apache-2.0 |

O ganho real aparece em perguntas sem palavras em comum com o texto ("como faço pra vocês esquecerem meu
cadastro?" → privacidade) e em palavras ambíguas ("baixo de quatro cordas", que a busca lexical mandava para
"atendimento" por causa de *cordas*). Os embeddings rodam **sempre em CPU**: lotes grandes derrubaram o
driver da GPU integrada nos testes.
  O raciocínio não melhora o acerto nesse porte, então fica desligado.

Detalhes e como adicionar modelos: [`models/README.md`](models/README.md).

## Decisões técnicas

| Decisão | Escolha | Justificativa |
|---|---|---|
| API de negócio | **Servidor MCP** (SDK oficial `mcp` 2.x, `MCPServer`) | Separa regras de negócio da orquestração; interoperável (Claude Desktop, Inspector, llamafile); primitivas certas (tools, resources, prompts). |
| Abordagem | Function calling + **detecção de assunto com pré-busca** para todos os modelos | Dados exatos vêm de código testado; compensa a fraqueza de modelos pequenos em tool calling. |
| LLM | Cliente compatível com OpenAI (SDK `openai` 3.x), **perfis** em YAML | Um caminho de código para llamafile, Ollama e APIs; contexto lido do servidor quando possível; respostas fora do padrão de modelos pequenos normalizadas no cliente. Começamos por modelos pequenos (≤ 4B). |
| Baseline | Bonsai-1.7B via llamafile 0.10.6 | Gratuito, offline, ~250 MB; usa GPU se houver, CPU se não. |
| Embeddings | **embeddinggemma-300m** no mesmo runtime, sempre em CPU | Venceu a [avaliação](docs/embeddings_eval.md) em acerto, latência e tamanho. Mesmo runtime do LLM, sem stack nova; bge-m3 (MIT) como alternativa se a licença Gemma for um impedimento. |
| Políticas | **Curadoria versionada** do PDF, verificada pelo pipeline (cobertura, versão do PDF, fatos), publicada por seção | O manual tem ~3,2 mil tokens; vector DB seria desproporcional. A revisão humana resolve as contradições do PDF, e a verificação automática garante que ela não perde nem desatualiza nada. |
| Dados | CSV → validação → limpeza → **SQLite** (atrás de uma interface `Repository`) | Vem com o Python (zero dependência nova), um arquivo só, esquema com restrições que registram as decisões do relatório, SQL com parâmetros, mesmo motor para o histórico. Com 65 produtos, um servidor seria desproporcional; Postgres entra como adaptador de escala. |
| Busca | **Híbrida**: lexical + vetorial com fusão RRF, atrás da interface `Retriever` | A lexical acerta nomes exatos ("Takamine GD20"); a vetorial resolve perguntas sem palavras em comum ("esquecer meu cadastro"); a fusão às vezes acerta o que nenhuma das duas acerta sozinha (e às vezes não: ver a tabela em [Busca híbrida](#busca-híbrida)). Vetores em SQLite com busca exata em memória: com 75 documentos, um banco vetorial não se paga. **pgvector** fica como adaptador de escala, validado pela mesma suíte de contrato. |
| Orquestração | **DAG próprio** (`emporio pipeline`), sem Airflow | Não há nada para agendar (dados estáticos, processados em menos de 1 s). Airflow exigiria scheduler, webserver e banco de metadados. Cada etapa corresponde 1:1 a uma task do Airflow quando houver sync com um sistema real. |
| Reprodutibilidade | uv + `uv.lock` + Python gerenciado + manifesto SHA-256 | A equipe avaliadora reproduz o ambiente idêntico. |

## Tratamento de dados e suposições

Relatório completo, com modelo ER, gráficos e uma decisão para cada achado:
[`docs/data_report.md`](docs/data_report.md). Todos os números são recalculados por
`uv run python scripts/explore_data.py` e conferidos por testes (`tests/test_data_findings.py`).

| Achado | Decisão |
|---|---|
| 7 produtos com **nome e descrição de marcas diferentes** (135, 136, 137, 139, 140, 142, 144) | O nome é a fonte da verdade; a descrição desses itens não é usada |
| Status × estoque conflitantes: 96 (ativo, estoque 0), 136 (descontinuado, estoque 13), 130 (*coming soon*, já vendido) | O status prevalece; só `active` com estoque > 0 é vendável |
| Categorias de sopros e cordas orquestrais **sem nenhum produto** | O agente informa com transparência; nunca inventa produto |
| Promoções **sem datas**, só `is_active` (4 ativas) | `is_active` é a vigência; o PIX não se soma à promoção (manual §6.2) |
| Totais dos pedidos 3 e 20 ≠ soma dos itens | Vale o `total_brl` (valor cobrado) |
| Não há data real de entrega; os dados vão até mar/2026 | `estimated_delivery` como proxy; `REFERENCE_DATE=2026-03-25` define o "hoje" |
| Regras ambíguas no manual | Exatos R$ 500 → frete grátis (favorece o cliente); até 3x, parcela ≥ R$ 50; troca por preferência conta do recebimento; região metropolitana = Campo Grande (únicos clientes da região na base); Carnaval e Corpus Christi não fecham a loja (pontos facultativos); cancelamento antes da entrega vai para a equipe (o manual não trata) |
| E-mail duplicado (clientes 11 e 43) | Verificação por **pedido + e-mail/telefone do titular daquele pedido** (LGPD) |
| PDF: 2 telefones, e-mail com e sem acento, regras de parcelamento contraditórias; extração com ~1 palavra por linha | Regra mais específica prevalece; extração em modo layout + [curadoria versionada e verificada](#manual-de-políticas) |

## Roadmap

```mermaid
flowchart TD
    subgraph F1["① Fundação"]
        direction LR
        C1["1 · estrutura<br/>+ setup"]:::done --> C1b["1b · testes<br/>+ CI"]:::done --> C2["2 · relatório<br/>de dados"]:::done --> C3["3 · pipeline<br/>+ config + CLI"]:::done
    end
    subgraph F2["② Dados e conhecimento"]
        direction LR
        C4["4 · dados<br/>SQLite"]:::done --> C4b["4b · lint<br/>ampliado"]:::done --> C5["5 · políticas"]:::done --> C6a["6a · modelo de<br/>embeddings"]:::done --> C6b["6b · busca<br/>híbrida"]:::done
    end
    subgraph F3["③ Domínio e API"]
        direction LR
        C7["7 · catálogo"]:::done --> C8["8 · pedidos<br/>e regras"]:::done --> C9["9 · servidor<br/>MCP"]:::done
    end
    subgraph F4["④ Agente"]
        direction LR
        C10["10 · perfis<br/>de LLM"]:::done --> C11["11 · agente<br/>host MCP"]:::done --> C12["12 · detecção<br/>de assunto"]:::done --> C13["13 · chat +<br/>histórico"]:::done --> C14["14 · guardrails"]:::done
    end
    subgraph F5["⑤ Qualidade e entrega"]
        direction LR
        C15["15 · avaliação"]:::done --> C16["16 · escala<br/>Postgres/pgvector"]:::done --> C17["17 · exemplos"]:::done --> CX["19–21 · Airflow,<br/>REST, UI"] --> C18["README final"]
    end
    F1 --> F2 --> F3 --> F4 --> F5
    classDef next fill:#fde68a,stroke:#b45309,color:#000
    classDef done fill:#bbf7d0,stroke:#15803d,color:#000
```

Verde: concluído. Amarelo: próximo. Os extras (19–21: endpoint REST `/chat`, interface gráfica e template de
DAG do Airflow) entram antes do README final.

## Estrutura do repositório

```
config/          perfis de LLM (llm_profiles.yaml)
data/raw/        CSVs e manual de políticas originais
data/curated/    curadoria do manual (uma seção .md por tema + _source.yaml) — versionada
data/processed/  artefatos gerados (SQLite, policies.json, extração do PDF) — não versionado
docs/            relatório de dados (data_report.md), avaliação de embeddings (embeddings_eval.md/.json),
                 arquitetura e caminho de produção (architecture.md), teste de carga (load_test/);
                 workflow com IA (planejado)
evals/           cenários, gabaritos e resultados (evals/resultados/README.md)
deploy/          Dockerfile e docker-compose (padrão e perfil scale com Postgres + pgvector)
examples/        5 conversas com o Bonsai, comentadas (examples/README.md)
models/          manifest.tsv (versionado) + binários (não versionados)
scripts/         setup, verificação (check.sh), download de modelos, start do llamafile, smoke test,
                 exploração dos dados (explore_data.py), avaliação de embeddings (eval_embeddings.py)
src/emporio/     config.py (configuração validada), log.py (logs estruturados), cli.py (emporio),
                 pipeline/ (orquestrador + etapas), data/ (contratos, regras, schema.sql, policies.py),
                 ports.py (interfaces), adapters/ (SQLite, embedders, índice vetorial, buscas),
                 retrieval/ (documentos, montagem da busca), services.py (servidores locais),
                 domain/ (modelos, busca de políticas, catálogo, pedidos, pagamento/frete, horário),
                 mcp_server/ (servidor MCP, saídas estruturadas, persona),
                 agent/ (cliente LLM com perfis, cliente MCP, detecção de assunto, pré-busca,
                         orçamento de contexto, agente com loop de tools)
tests/contract/  suítes de contrato reutilizáveis por adaptador
tests/           testes automatizados (unitários + integração com o LLM local)
.github/         CI (GitHub Actions)
```

## Limitações conhecidas

- O baseline 1.7B em CPU é lento: com a pré-busca, **~1 a 2,5 minutos por pergunta** neste notebook
  (antes, ~3 a 4 com as 8 tools no prompt), quase tudo processando o prompt a ~17–20 tokens/s. A GPU
  integrada desta máquina não serve: corrompe prompts longos.
- A detecção de assunto é por palavras-chave: uma pergunta com vocabulário fora das listas cai no assunto
  "geral", e aí o modelo decide com 3 tools genéricas (e acerta tool calling só em parte dos casos).
- A checagem de preço sem fonte confere valores em R$, não outros números (prazos, percentuais).
- O Bonsai ainda erra a redação em parte das respostas: trocou o nome de um produto ("Ryuzan" no lugar de
  "Rozini", com o preço certo) e acrescentou uma frase sem sentido numa devolução (ver `examples/README.md`). A
  checagem de preço pega valores inventados; nomes de produto não são checados.
- Os guardrails de entrada são por padrões de texto: uma injeção reescrita com outras palavras passa por
  eles. A defesa em profundidade é o resto da arquitetura: as tools são somente leitura, os pedidos exigem a
  verificação do titular, nenhuma tool lista clientes e a saída tem contatos de terceiros mascarados.
- Com perguntas que juntam vários assuntos, o Bonsai tende a copiar trechos longos do manual em vez de
  resumir (os dados estão certos, mas a resposta fica longa).
- O limiar de similaridade separa pouco: no conjunto de avaliação, a resposta certa menos parecida teve 0,313
  e a pergunta fora de escopo mais parecida, 0,292 (limiar 0,30). Ele é um filtro auxiliar; a defesa principal
  contra perguntas fora de escopo será a detecção de assunto e os guardrails.
- Indexar os 75 documentos em CPU leva ~1 minuto na primeira vez (depois, só o que muda é recalculado).
- O valor do frete para outras cidades não é calculado (depende de CEP, peso, dimensões e APIs das
  transportadoras); o agente informa os prazos e que o valor sai no fechamento.
- Os perfis online (Anthropic, OpenAI) e o do Ollama são configurações de referência: não foram executados
  aqui (sem chaves de API e sem Ollama instalado). Só `bonsai` e `qwen3-1.7b` estão validados de ponta a ponta.
- A avaliação tem 18 cenários e uma rodada por modelo (temperatura 0,2): é um retrato, não uma estatística.
  Com o Bonsai levando ~1 min por resposta em CPU, repetir cada cenário várias vezes custaria horas.
- O servidor MCP via HTTP não tem autenticação: escuta só em `127.0.0.1` por padrão. Em produção, ficaria
  atrás de um gateway com OAuth (o SDK já oferece suporte).
- A verificação de identidade aceita o telefone sem DDD (8+ dígitos finais): é prática para o cliente, mas
  mais fraca que um código por SMS/e-mail (OTP), que seria o próximo passo em produção.
- *(Lista completa e "o que faria com mais tempo" ao final do projeto.)*

## Uso de assistentes de código

Este projeto está sendo desenvolvido com o **Claude Code** como par de programação. Workflow, com detalhes em
`docs/ai_workflow.md` *(planejado)*:
- **Entendimento e plano:** leitura do enunciado, dos CSVs e do PDF; levantamento de inconsistências nos dados;
  plano de implementação dividido em commits, revisado e ajustado por mim antes de cada etapa.
- **Validação empírica antes de decidir:** testes de viabilidade do modelo local (tool calling, A/B de
  `--reasoning`) e verificação da procedência do modelo por checksum.
- **Decisões minhas:** uso de MCP, baseline Bonsai, começar por modelos pequenos, detecção de assunto para
  todos os modelos, metadados de contexto, reprodutibilidade total, e a exigência de que a solução seja
  escalável e robusta e rode inteira com um comando.
- **Revisão crítica do que a IA propõe:** por exemplo, questionei pgvector, Airflow e SQLite antes da camada
  de dados. A discussão levou à arquitetura "leve por padrão, escala por configuração".
- **Testes pegando erros reais:** os testes do orquestrador encontraram dois bugs no código gerado
  (fingerprint calculado antes de as dependências rodarem; saída sem extensão tratada como diretório),
  corrigidos antes do commit. Ao desenhar a camada de dados apareceu uma falha de projeto (mudar uma regra
  não invalidava a etapa que a usa), corrigida com a declaração de dependências de código por etapa.
- **Padrões de qualidade definidos por mim:** pedi o lint ampliado (ruff com ~30 famílias, inclusive
  segurança); cada exceção às regras está justificada. Cada commit é verificado isoladamente, não só o
  último do push.
- **Verificações que pegam o que passaria batido:** a checagem de fatos da curadoria encontrou uma omissão
  na revisão manual do PDF na primeira execução.
- **Testes e investigação pegando problemas reais na busca:** o embedder de teste aproximava tudo de tudo
  por causa de stopwords; a fusão quebrava se um resultado lexical não estivesse no índice vetorial; a
  suíte passou de 10 s para 118 s porque uma fixture de sessão subia o modelo real (corrigido para 12 s); e
  a porta 8081 desta máquina estava ocupada por outro serviço, o que levou à troca automática de porta.
- **O teste de ponta a ponta derrubou uma decisão anterior:** a primeira conversa real do agente saiu como
  lixo ("de de de a da…"). Isolando variáveis (sem tools, uma tool, oito tools; CPU × iGPU), a causa foi a
  GPU integrada calculando errado com prompts longos. O padrão `GPU=auto` do chat, adotado com base em
  testes de prompts curtos, voltou para CPU, e o README foi corrigido.
- **Não confiar na memória sobre APIs:** o lock instalou o SDK `mcp` 2.x, em que `FastMCP` virou `MCPServer`
  e os campos passaram a snake_case, e o `openai` 3.x, que usa `httpx2` em vez de `httpx`. Antes de escrever
  cada camada, um protótipo validou os recursos usados; a dependência indireta usada diretamente (`httpx2`)
  foi declarada no `pyproject.toml`.
- **Lint e testes pegando bugs de produção no servidor MCP:** o campo `args` nos logs colidia com um
  atributo interno do `LogRecord` (quebraria na primeira chamada de tool); e encerrar o `emporio mcp` com
  SIGTERM ou fechando o terminal deixava o servidor de embeddings órfão. A correção tem teste de regressão,
  verificado falhando sem ela.
- **Olhar os dados antes de prometer o exemplo:** ao preparar as regras de devolução, o levantamento mostrou
  que nenhum pedido está na janela de arrependimento com a data de referência; o README e os exemplos
  assumem isso em vez de forçar um caso que os dados não sustentam.
- **Testar com a pergunta real, não com a ideal:** no catálogo, "Quanto custa o Takamine GD20?" (a pergunta
  do enunciado) saía ambígua, porque "quanto custa o" diluía o nome; e "quanto custa um violão?" virava três
  produtos aleatórios. Ambos corrigidos, com testes.
- **Medir antes de escolher, e corrigir o que foi publicado:** o modelo de embeddings foi escolhido por
  avaliação com gabarito, não por reputação. A investigação de uma queda revelou que o runtime usava a GPU
  integrada sem avisar, e que medições já publicadas como "sem GPU" estavam erradas; o README foi corrigido
  com nota explícita.
