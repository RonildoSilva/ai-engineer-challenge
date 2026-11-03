# Conversas de exemplo

Cinco conversas com o agente real, gravadas com `emporio chat --transcript` pelo
[`scripts/record_examples.sh`](../scripts/record_examples.sh). Modelo: **Bonsai-1.7B** (o baseline do desafio, 1 bit,
em CPU de notebook), com a mesma pré-busca, os mesmos guardrails e o mesmo servidor MCP da avaliação.

**As respostas estão como o modelo as escreveu, sem edição.** Onde ele erra, o comentário abaixo diz o quê. Cada
turno mostra as tools usadas (`(pré-busca)` = chamada feita pelo agente antes do modelo), quantas chamadas ao LLM
houve e o tempo. Turnos com **0 chamadas** são respostas prontas, montadas a partir dos dados das tools ou de um
guardrail, sem passar pelo modelo. E-mails e telefones digitados pelo cliente aparecem mascarados na transcrição
(`[e-mail]`), como no histórico.

```bash
./scripts/record_examples.sh                      # regrava as cinco com o Bonsai (~15–20 min em CPU)
PROFILE=qwen3-1.7b ./scripts/record_examples.sh   # com o Qwen3-1.7B (download opcional: EVAL_MODELS=1)
ONLY=04 ./scripts/record_examples.sh              # só uma
```

| # | Conversa | O que mostra | Avaliação |
|---|---|---|---|
| [01](01_catalogo_ate_1000.md) | Violões até R$ 1.000 → "dá pra fazer o Tagima Memphis em 3x?" | Exemplo do enunciado: filtros inferidos do texto (categoria + faixa), preço com PIX; na segunda pergunta, o produto é encontrado e o preço dele alimenta a simulação de parcelas | ✅ dados certos; repete a nota "PIX não é promoção" sem necessidade |
| [02](02_endereco_horario.md) | Endereço → "abrem no domingo?" | Contato e horário como **dado puro**: respostas prontas, lidas do manual (via MCP) e do relógio da loja (fuso de Campo Grande, feriados) | ✅ |
| [03](03_preco_gd20.md) | Preço do GD20 → 10x no cartão → desconto no PIX | Pergunta do enunciado; turnos seguintes **sem citar o produto** usam o preço dele (R$ 2.199) na simulação; promoção × PIX | ✅ valores certos (10x de R$ 219,90; PIX R$ 2.089,05); o 3º turno repete a simulação inteira em vez de responder só o PIX |
| [04](04_devolucao.md) | Devolver o pedido 7 por arrependimento → e-mail → "veio com defeito" | **Não trivial:** verificação do titular (LGPD) em dois turnos, prazos contados da data de referência (25/03/2026), arrependimento fora do prazo, troca direta por defeito também vencida, caminho da garantia | ⚠️ conclusões certas ("não há mais tempo"; "o caminho agora é a garantia"), mas o 2º turno termina com uma frase sem sentido ("pode esperar até 24/02/2026 para receber a devolução") e o 3º não cita a garantia legal até 18/05/2026, que estava nos dados |
| [05](05_fora_do_escopo_acessorios.md) | Palheta → corda de violão → receita de bolo → violão para iniciante | Acessórios (a loja só vende instrumentos), assunto fora do escopo e retomada normal depois da recusa | ⚠️ três turnos corretos e prontos; no 4º, o preço está certo (R$ 459,90), mas o modelo **trocou o nome** "Rozini RC-104" por "Ryuzan RC-104" e inventou uma "alternativa" |

## O que as gravações ensinaram

As primeiras gravações expuseram bugs do agente (não do modelo), corrigidos antes desta versão, cada um com teste:

- depois de recusar a receita de bolo, o pedido seguinte de violão virou "aqui está a receita de bolo" → turnos
  bloqueados pelos guardrails não voltam ao contexto do modelo;
- "tem desconto no PIX?" com o GD20 em contexto trazia também duas seções do manual, e o modelo as despejava,
  chamando o PIX de "preço promocional" → a simulação de pagamento basta;
- "abrem no domingo?" era respondido com o horário de hoje, e o endereço vinha com telefone e WhatsApp trocados (numa
  tentativa, com valores em R$ inventados, barrados pela checagem de preço) → horário e contato viraram respostas
  prontas;
- "e dá pra fazer em 10x?" logo depois do GD20 não tinha valor para simular → o agente lembra o último produto.

O que sobra (nome de produto trocado, frase sem sentido, repetição) é limite de um modelo de 1,7B parâmetros
quantizado em 1 bit. Na avaliação, o Qwen3-1.7B sem essa quantização passou em 18 de 18 cenários, contra 17 do
Bonsai (ver [`evals/resultados/`](../evals/resultados/README.md)).
