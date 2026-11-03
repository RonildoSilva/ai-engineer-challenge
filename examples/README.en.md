# Example conversations

Five conversations with the real agent, recorded with `emporio chat --transcript` by
[`scripts/record_examples.sh`](../scripts/record_examples.sh). Model: **Bonsai-1.7B** (the challenge baseline, 1-bit,
on a laptop CPU), with the same prefetch, the same guardrails and the same MCP server as the evaluation.

**The replies are as the model wrote them, unedited.** Where it gets something wrong, the comment below says what. Each
turn shows the tools used (`(pré-busca)` = a call made by the agent before the model), how many LLM calls
there were and the time taken. Turns with **0 calls** are ready-made replies, built from the tool data or from a
guardrail, without going through the model. Emails and phone numbers typed by the customer appear masked in the transcript
(`[e-mail]`), as in the history.

```bash
./scripts/record_examples.sh                      # re-records all five with Bonsai (~15–20 min on CPU)
PROFILE=qwen3-1.7b ./scripts/record_examples.sh   # with Qwen3-1.7B (optional download: EVAL_MODELS=1)
ONLY=04 ./scripts/record_examples.sh              # just one
```

| # | Conversation | What it shows | Evaluation |
|---|---|---|---|
| [01](01_catalogo_ate_1000.md) | Violões até R$ 1.000 → "dá pra fazer o Tagima Memphis em 3x?" (Guitars up to R$ 1,000 → "can I do the Tagima Memphis in 3 installments?") | Example from the brief: filters inferred from the text (category + price range), price with PIX; on the second question, the product is found and its price feeds the installment simulation | ✅ correct data; needlessly repeats the note "PIX is not a promotion" |
| [02](02_endereco_horario.md) | Endereço → "abrem no domingo?" (Address → "are you open on Sunday?") | Contact and opening hours as **pure data**: ready-made replies, read from the manual (via MCP) and from the store clock (Campo Grande time zone, holidays) | ✅ |
| [03](03_preco_gd20.md) | Preço do GD20 → 10x no cartão → desconto no PIX (GD20 price → 10 installments on card → PIX discount) | Question from the brief; following turns **without naming the product** use its price (R$ 2.199) in the simulation; promotion vs. PIX | ✅ correct values (10x of R$ 219,90; PIX R$ 2.089,05); the 3rd turn repeats the entire simulation instead of answering only the PIX |
| [04](04_devolucao.md) | Devolver o pedido 7 por arrependimento → e-mail → "veio com defeito" (Return order 7 for change of mind → email → "it arrived defective") | **Non-trivial:** order holder verification (LGPD) over two turns, deadlines counted from the reference date (03/25/2026), change of mind past the deadline, direct exchange for defect also expired, warranty path | ⚠️ correct conclusions ("there is no time left"; "the path now is the warranty"), but the 2nd turn ends with a nonsensical sentence ("you can wait until 02/24/2026 to receive the return") and the 3rd does not mention the legal warranty through 05/18/2026, which was in the data |
| [05](05_fora_do_escopo_acessorios.md) | Palheta → corda de violão → receita de bolo → violão para iniciante (Guitar pick → guitar string → cake recipe → beginner guitar) | Accessories (the store only sells instruments), an out-of-scope topic and a normal resumption after the refusal | ⚠️ three correct, ready-made turns; on the 4th, the price is right (R$ 459,90), but the model **swapped the name** "Rozini RC-104" for "Ryuzan RC-104" and invented an "alternative" |

## What the recordings taught

The first recordings exposed agent bugs (not model bugs), fixed before this version, each with a test:

- after refusing the cake recipe, the next guitar request turned into "here is the cake recipe" → turns
  blocked by the guardrails do not go back into the model's context;
- "tem desconto no PIX?" ("is there a PIX discount?") with the GD20 in context also brought two sections of the manual, and the model dumped them,
  calling PIX a "promotional price" → the payment simulation is enough;
- "abrem no domingo?" ("are you open on Sunday?") was answered with today's hours, and the address came with phone and WhatsApp swapped (in one
  attempt, with made-up R$ values, caught by the price check) → hours and contact became ready-made
  replies;
- "e dá pra fazer em 10x?" ("can it be done in 10 installments?") right after the GD20 had no value to simulate → the agent remembers the last product.

What remains (swapped product name, nonsensical sentence, repetition) is a limit of a 1.7B-parameter model
quantized to 1 bit. In the evaluation, Qwen3-1.7B without that quantization passed 18 of 18 scenarios, versus 17 for
Bonsai (see [`evals/resultados/`](../evals/resultados/README.md)).
