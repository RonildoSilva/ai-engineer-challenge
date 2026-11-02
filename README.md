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
- [Escalabilidade e robustez](#escalabilidade-e-robustez)
- [Modelo local (baseline)](#modelo-local-baseline)
- [Decisões técnicas](#decisões-técnicas)
- [Tratamento de dados e suposições](#tratamento-de-dados-e-suposições)
- [Roadmap](#roadmap)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Limitações conhecidas](#limitações-conhecidas)
- [Uso de assistentes de código](#uso-de-assistentes-de-código)

---

## Arquitetura

### Visão geral *(planejado)*

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

### Fluxo de uma mensagem *(planejado)*

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant A as Agent Host
    participant R as Detecção de assunto
    participant M as Servidor MCP
    participant L as LLM

    C->>A: "Posso devolver meu pedido 8?"
    A->>R: classifica intenção
    R-->>A: devolução + pedido
    A->>M: pré-busca: politicas://trocas_devolucoes
    M-->>A: regras (7 dias, embalagem, frete)
    A->>L: persona + contexto + histórico + tools da intenção
    L-->>A: tool_call verificar_devolucao(pedido=8, contato=?)
    A-->>C: pede e-mail/telefone (LGPD)
    C->>A: "ana@coldmail.com"
    A->>M: verificar_devolucao(8, ana@…)
    M-->>A: elegível? prazo restante, condições
    A->>L: resultado da tool
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
segurança.

### Orçamento de contexto *(planejado)*

```mermaid
flowchart TB
    subgraph Perfil[Perfil do LLM]
        CW[context_window<br/>auto via /props no llamafile]
    end
    subgraph MCPm[Servidor MCP]
        SZ[custo de cada informação<br/>tokens estimados por resource<br/>tools com detalhe/limite]
    end
    CW --> BUD{{Host: orçamento por turno}}
    SZ --> BUD
    BUD --> S1[system prompt + schemas]
    BUD --> S2[contexto pré-buscado]
    BUD --> S3[histórico recente]
    BUD --> S4[reserva p/ resposta]
```

O MCP é agnóstico ao modelo (pode atender a CLI com o Bonsai em 8k e um cliente com 128k ao mesmo tempo).
Ele informa **quanto custa** cada informação, e o host decide **quanto cabe**.

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
    F --> G[download GGUF<br/>Bonsai-1.7B]
    F & G --> H{SHA-256 confere?}
    H -->|não| X
    H -->|sim| I[cria .env]
    I --> P[emporio pipeline run<br/>incremental]
    P --> J([pronto])
```

```bash
./scripts/setup.sh                 # tudo (~300 MB de download na primeira vez)
SKIP_MODEL=1 ./scripts/setup.sh    # sem runtime/modelo local (ex.: usar só API online)
```

## Como rodar

```bash
./scripts/start_llamafile.sh         # terminal 1: Bonsai-1.7B em http://127.0.0.1:8080/v1 (modelo "bonsai")
uv run python scripts/smoke_llm.py   # terminal 2: chat simples + chamada de ferramenta
```

`start_llamafile.sh` aceita variáveis de ambiente: `MODEL`, `ALIAS`, `PORT`, `CTX` (default 8192), `THREADS`
e `REASONING` (`off` por padrão).

```bash
uv run emporio pipeline run            # pipeline de dados (pula o que está em dia)
uv run emporio pipeline status         # estado de cada etapa
uv run emporio pipeline graph          # DAG em Mermaid
```

> Chat, servidor MCP e avaliação serão documentados aqui conforme forem implementados.

## Verificação (testes e CI)

Cada push reproduz o setup **do zero numa máquina limpa** (GitHub Actions, Ubuntu 24.04) e roda a mesma
verificação disponível localmente. É a garantia de que o projeto funciona fora da máquina de
desenvolvimento.

```mermaid
flowchart LR
    P([push / PR]) --> CI
    subgraph CI[GitHub Actions · ubuntu-24.04 limpo]
        direction LR
        U[uv fixado<br/>instalador oficial] --> S[scripts/setup.sh<br/>Python + deps + modelo<br/>com SHA-256] --> K[scripts/check.sh]
    end
    subgraph K2[scripts/check.sh]
        direction TB
        L1[uv lock --check] --> L2[ruff check + format]
        L2 --> L3[pytest unitário<br/>~1 s, offline]
        L3 --> L4[pytest -m integration<br/>sobe o Bonsai real]
    end
    K -.-> K2
```

```bash
./scripts/check.sh          # tudo: lock, lint, unitários e integração (~20 s com o modelo baixado)
FAST=1 ./scripts/check.sh   # sem integração
uv run pytest -m integration -rs   # só a integração com o LLM local
```

| Camada | O que garante | Arquivo |
|---|---|---|
| Versões fixadas | Python exato e gerenciado, `requires-python`, uv no setup e no README, `uv.lock` atualizado | `tests/test_infra.py` |
| Manifesto | URLs fixadas em tag/commit (nunca `main`/`latest`), SHA-256 válido, artefatos baixados íntegros | `tests/test_infra.py` |
| Download | baixa, é idempotente, **recusa checksum errado** sem deixar lixo, não sobrescreve arquivo corrompido sem `FORCE=1` | `tests/test_infra.py` |
| Scripts | sintaxe bash válida, executáveis, erro claro sem artefatos | `tests/test_infra.py` |
| `.gitignore` | binários, `.env`, `_raw/` e gerados fora do git; manifesto, `.env.example` e dados brutos dentro | `tests/test_infra.py` |
| LLM real | servidor sobe pelo script, contexto 8192, alias `bonsai`, responde chat e devolve **tool call estruturado** | `tests/test_llm_integration.py` |

## Pipeline de dados

Todo processamento de dados passa por um **DAG de etapas idempotentes**, executado pelo `setup.sh` e pela
CLI `emporio pipeline`. Hoje o DAG tem a etapa de inventário das fontes; as etapas abaixo marcadas como
*(planejado)* entram nos próximos commits.

```mermaid
flowchart LR
    S[snapshot_raw<br/>inventário + hashes<br/>das fontes]:::done --> V[validate_raw<br/>schema e regras]
    V --> T[transform<br/>decisões do relatório] --> L[load<br/>SQLite]
    S --> PE[extract_policies<br/>PDF → texto normalizado] --> PS[policies<br/>seções curadas]
    L --> C[chunk] --> E[embed] --> I[index<br/>vetores]
    PS --> C
    L --> Q[quality_checks]
    I --> Q
    classDef done fill:#bbf7d0,stroke:#15803d,color:#000
```

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
  seguintes continuam em dia. Mudar o código de uma etapa já a invalida, sem precisar mexer na versão.
- **Linhagem:** `data/processed/manifest.json` registra, por etapa, o hash de cada entrada e saída, o
  fingerprint, a duração, as métricas e o erro. Saídas adulteradas são detectadas.
- **Robusto a falhas:** o estado é salvo após cada etapa, com escrita atômica. Uma falha no meio não perde o
  progresso e não deixa estado inconsistente.
- **Portável:** cada etapa é uma função `f(ctx) -> métricas` que não depende do runner, e corresponde 1:1 a
  uma task do Airflow ou do Dagster.

## Escalabilidade e robustez

O princípio é **leve por padrão, escala por configuração, mesma interface, mesmos testes**. A banca roda tudo
com um comando, sem servidores. O caminho de crescimento fica implementado e testado atrás das mesmas
interfaces.

| Peça | Padrão (1 comando, sem servidor) | Escala (por configuração) | Status |
|---|---|---|---|
| Orquestração | DAG próprio, incremental, com linhagem | Airflow/Dagster (mesmas funções de etapa) | ✅ DAG · template Airflow *(planejado)* |
| Configuração | `.env` validado na inicialização (falha cedo) | variáveis de ambiente / secret manager | ✅ |
| Logs | texto no terminal | JSON, uma linha por evento (`LOG_FORMAT=json`) | ✅ |
| Dados | SQLite | Postgres | *(planejado)* |
| Busca | lexical + embeddings com vetores em SQLite | pgvector | *(planejado)* |
| Embeddings | modelo GGUF no mesmo runtime llamafile | API ou GPU | *(planejado)* |
| LLM | Bonsai-1.7B em CPU | modelos maiores, GPU ou API | ✅ baseline |

## Modelo local (baseline)

```mermaid
flowchart LR
    subgraph SRC[Fontes oficiais fixadas]
        M1[mozilla-ai/llamafile<br/>release 0.10.6]
        M2[prism-ml/Bonsai-1.7B-gguf<br/>commit 210a9e9]
    end
    M1 -->|SHA-256| R[models/runtime/<br/>llamafile-0.10.6-thin]
    M2 -->|SHA-256| G[models/bonsai-1.7b/<br/>Bonsai-1.7B.gguf]
    R --> S[start_llamafile.sh<br/>--server --jinja]
    G --> S
    S --> API[/v1/chat/completions<br/>compatível com OpenAI/]
```

- **Bonsai-1.7B** (PrismML): arquitetura Qwen3, 1.72B params, quantização de 1 bit (~248 MB), contexto de
  treino 32k (usamos 8k).
- **Runtime e GGUF separados**, ambos de fontes oficiais com checksum. O `.llamafile` único usado no início
  do desenvolvimento é exatamente essa combinação: o GGUF embutido nele tem SHA-256 idêntico ao oficial.
- **Medições iniciais** (exploratórias, não fazem parte do CI) (CPU 8 vCPU, sem GPU, 4 perguntas × 2 execuções, 2 tools no prompt):

  | Configuração | Tool calls corretas | Tempo médio |
  |---|---|---|
  | `--reasoning off` (default) | 4/8 | ~4,8–6,7 s |
  | `--reasoning on` | 4/8 | ~6,0 s |

  O raciocínio não melhora o acerto nesse porte, então fica desligado.

Detalhes e como adicionar modelos: [`models/README.md`](models/README.md).

## Decisões técnicas

| Decisão | Escolha | Justificativa |
|---|---|---|
| API de negócio | **Servidor MCP** (SDK oficial `mcp`) | Separa regras de negócio da orquestração; interoperável (Claude Desktop, Inspector, llamafile); primitivas certas (tools, resources, prompts). |
| Abordagem | Function calling + **detecção de assunto com pré-busca** para todos os modelos | Dados exatos vêm de código testado; compensa a fraqueza de modelos pequenos em tool calling. |
| LLM | Cliente compatível com OpenAI, **perfis** offline/online | Um caminho de código para llamafile, Ollama, APIs. Começamos por modelos pequenos (≤ 4B). |
| Baseline | Bonsai-1.7B via llamafile 0.10.6 | Gratuito, offline, CPU, ~250 MB. |
| Políticas | Markdown por seção (resource MCP + tool) | Manual de ~3k tokens; vector DB seria overkill. |
| Dados | CSV → validação → limpeza → **SQLite** (atrás de uma interface `Repository`) | Vem com o Python (zero dependência nova), um arquivo só, esquema com restrições que registram as decisões do relatório, SQL com parâmetros, mesmo motor para o histórico. Com 65 produtos, um servidor seria desproporcional; Postgres entra como adaptador de escala. |
| Busca vetorial | Embeddings atrás de uma interface `Retriever`, vetores em SQLite por padrão | As perguntas centrais (preço, estoque, pedido) são exatas, e com 65 itens um índice vetorial dedicado não se paga. **pgvector** fica como adaptador de escala, testado no CI. |
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
| E-mail duplicado (clientes 11 e 43) | Verificação por **pedido + e-mail/telefone do titular daquele pedido** (LGPD) |
| PDF: 2 telefones, e-mail com e sem acento, regras de parcelamento contraditórias; extração com ~1 palavra por linha | Regra mais específica prevalece; PDF → markdown normalizado e revisado à mão |

## Roadmap

```mermaid
flowchart LR
    C1[1 · estrutura +<br/>setup]:::done --> C1b[1b · testes + CI]:::done --> C2[2 · relatório<br/>de dados]:::done --> C3[3 · pipeline +<br/>config + CLI]:::wip
    C3 --> C4[4 · dados<br/>SQLite] --> C5[5 · políticas] --> C6[6 · busca híbrida<br/>embeddings] --> C7[7-8 · domínio]
    C7 --> C9[9 · servidor MCP] --> C10[10 · perfis LLM] --> C11[11 · agente] --> C12[12 · detecção<br/>de assunto] --> C13[13 · chat +<br/>histórico]
    C13 --> C14[14 · guardrails] --> C15[15 · eval] --> C16[16 · escala<br/>Postgres/pgvector] --> C17[17 · exemplos] --> C18[18 · README final]
    classDef wip fill:#fde68a,stroke:#b45309,color:#000
    classDef done fill:#bbf7d0,stroke:#15803d,color:#000
```

Extras previstos: endpoint REST `/chat`, interface gráfica e template de DAG do Airflow.

## Estrutura do repositório

```
config/          perfis de LLM                                   (planejado)
data/raw/        CSVs e manual de políticas originais
data/processed/  artefatos gerados (SQLite, políticas .md) — não versionado
docs/            relatório de dados (data_report.md); workflow com IA (planejado)
evals/           cenários e resultados por modelo                 (planejado)
examples/        conversas de exemplo                             (planejado)
models/          manifest.tsv (versionado) + binários (não versionados)
scripts/         setup, verificação (check.sh), download de modelos, start do llamafile, smoke test,
                 exploração dos dados (explore_data.py)
src/emporio/     config.py (configuração validada), log.py (logs estruturados), cli.py (emporio),
                 pipeline/ (orquestrador + etapas); agente e servidor MCP (planejado)
tests/           testes automatizados (unitários + integração com o LLM local)
.github/         CI (GitHub Actions)
```

## Limitações conhecidas

- O baseline 1.7B em CPU é lento (~3–14 s por chamada) e acerta tool calling só em parte dos casos. A detecção
  de assunto mitiga isso, mas não elimina.
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
  corrigidos antes do commit.
