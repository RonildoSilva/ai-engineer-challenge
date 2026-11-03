"""Guardrails de entrada e de saída do agente.

Entrada (antes do LLM, com resposta pronta e sem chamar o modelo):
- **injeção de prompt** ("ignore as instruções", "mostre seu prompt", "você agora é…");
- **dados de terceiros** ("liste os clientes", "qual o e-mail da Ana?");
- **fora do escopo** explícito (receitas, futebol, política…), só quando nenhum assunto da loja
  foi detectado;
- **mensagem longa demais** (custo e superfície de ataque).

Saída (depois do LLM):
- **contatos vazados**: e-mails/telefones que não são da loja nem do próprio cliente são trocados
  por "[dado protegido]";
- **persona recitada**: trechos longos do system prompt na resposta (o modelo pequeno às vezes
  repete as regras internas) → resposta de segurança.

Acessórios, categorias vazias, produto esgotado e reclamações são tratados pela detecção de
assunto e pela pré-busca (as tools trazem a orientação), não aqui.

As listas são deliberadamente conservadoras: bloquear uma pergunta legítima custa mais caro do
que deixar o modelo, já instruído pela persona, recusar com gentileza.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from emporio.data.rules import normalize

MAX_INPUT_CHARS = 2000

STORE_CONTACTS = ("3341-4444", "3321-4500", "contato@emporiodamusica.com.br")


class Block(StrEnum):
    INJECTION = "injecao"
    THIRD_PARTY_DATA = "dados_de_terceiros"
    OUT_OF_SCOPE = "fora_do_escopo"
    TOO_LONG = "mensagem_longa"


REPLIES = {
    Block.INJECTION: (
        "Não consigo mudar a forma como atendo nem compartilhar minhas instruções internas. "
        "Mas posso te ajudar com instrumentos, preços, pedidos, trocas, pagamento, frete ou o "
        "horário da loja. O que você procura?"
    ),
    Block.THIRD_PARTY_DATA: (
        "Por segurança e respeito à LGPD, não compartilho dados de clientes nem informações de "
        "pedidos de outras pessoas. Se quiser consultar o seu pedido, me passa o número do pedido "
        "e o e-mail ou telefone usado na compra."
    ),
    Block.OUT_OF_SCOPE: (
        "Esse assunto foge do que eu consigo ajudar por aqui: sou a assistente da Empório da "
        "Música e só posso falar sobre a loja. Posso te ajudar a escolher um instrumento, ver "
        "preços, acompanhar um pedido ou tirar dúvidas sobre trocas, pagamento e frete! 🎶"
    ),
    Block.TOO_LONG: (
        "Sua mensagem ficou bem longa e eu não consigo processá-la de uma vez. Pode resumir em "
        "poucas linhas o que você precisa?"
    ),
}

# Texto normalizado (sem acento, minúsculas). Frases, não palavras soltas: "ignore" sozinho é
# comum ("ignore o que eu disse sobre a cor").
_INJECTION = re.compile(
    r"\b(?:"
    r"ignor\w* (?:\w+ ){0,3}(?:instruc\w*|regras|orientac\w*|prompt|comandos|diretrizes)"
    r"|esquec\w* (?:\w+ ){0,3}(?:instruc\w*|regras|orientac\w*|prompt|diretrizes)"
    r"|desconsider\w* (?:\w+ ){0,3}(?:instruc\w*|regras|orientac\w*|prompt)"
    r"|(?:mostr\w*|revel\w*|repit\w*|imprim\w*|exib\w*|qual (?:e|eh)) (?:\w+ ){0,3}"
    r"(?:system prompt|prompt do sistema|seu prompt|suas instrucoes|instrucoes internas|"
    r"suas regras|regras internas)"
    r"|system prompt|prompt do sistema|modo (?:desenvolvedor|developer|dan|admin\w*|deus)"
    r"|jailbreak|(?:voce|vc) agora (?:e|eh|sera|vai ser)|a partir de agora (?:voce|vc) (?:e|eh)"
    r"|finja (?:que|ser)|faca de conta|aja como|act as|pretend"
    r"|ignore (?:all |the )?(?:previous|prior|above) (?:instructions|rules)"
    r"|developer mode|you are now"
    r")\b"
)

# "telefone da loja", "dados da compra", "endereço da entrega" não são dados de terceiros
_OWN = (
    "loja|emporio|empresa|compra|minha|meu|pedido|entrega|conta|cadastro|nota|assistencia"
    "|transportadora|fabrica|fabricante|marca|garantia"
)
_THIRD_PARTY = re.compile(
    r"\b(?:"
    r"(?:lista|listar|liste|todos os|todas as|base de|cadastro de|relatorio de|quantos) "
    r"(?:\w+ ){0,2}(?:clientes|compradores|usuarios|cadastros|pedidos de todos)"
    r"|outros clientes|outro cliente|outra cliente|outra pessoa|de terceiros"
    r"|quem (?:comprou|pediu|fez o pedido)"
    r"|(?:e ?mail|telefone|celular|endereco|cpf|whatsapp|dados) "
    rf"(?:d[oa]s? clientes?|d[oa] (?!(?:{_OWN})\b)[a-z]+)"
    r")\b"
)

_OFF_TOPIC = re.compile(
    r"\b(?:"
    r"receitas?|bolo|cozinhar|futebol|campeonato|jogo do|time do|politic[ao]s? (?!de troca|da loja"
    r"|de privacidade|de devolucao|de frete|de pagamento)|eleic\w+|presidente|governo|noticias?"
    r"|previsao do tempo|vai chover|horoscopo|signo|piada|filme|series?|novela|bitcoin|cripto"
    r"|investimento|acoes da bolsa|remedio|doenca|sintoma|dieta|programacao|codigo em|python"
    r"|javascript|dever de casa|redacao|traduz\w*|capital d[aeo]"
    r")\b"
)


@dataclass(frozen=True)
class InputVerdict:
    block: Block
    reply: str
    matched: str


def check_input(text: str, *, store_subject: bool) -> InputVerdict | None:
    """Bloqueio da mensagem antes do LLM (com a resposta pronta), ou None se ela pode seguir.

    `store_subject`: a detecção de assunto reconheceu algo da loja (então "fora do escopo" não
    se aplica: "tem violão pra tocar no jogo do Brasil?" é sobre a loja).
    """
    if len(text) > MAX_INPUT_CHARS:
        return InputVerdict(Block.TOO_LONG, REPLIES[Block.TOO_LONG], f"{len(text)} caracteres")
    t = normalize(text)
    if m := _INJECTION.search(t):
        return InputVerdict(Block.INJECTION, REPLIES[Block.INJECTION], m.group(0))
    if m := _THIRD_PARTY.search(t):
        return InputVerdict(Block.THIRD_PARTY_DATA, REPLIES[Block.THIRD_PARTY_DATA], m.group(0))
    if not store_subject and (m := _OFF_TOPIC.search(t)):
        return InputVerdict(Block.OUT_OF_SCOPE, REPLIES[Block.OUT_OF_SCOPE], m.group(0))
    return None


# ------------------------------------------------------------------------------ saída

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[\s.-]?\d{4}\b")
REDACTED = "[dado protegido]"
LEAK_NGRAM = 16  # palavras seguidas iguais às do system prompt = persona recitada


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def redact_contacts(answer: str, allowed: list[str]) -> tuple[str, list[str]]:
    """Troca e-mails/telefones que não são da loja nem citados pelo cliente/ferramentas.

    `allowed`: textos de onde um contato pode legitimamente vir (mensagem do cliente, histórico,
    resultados das tools). Telefones são comparados pelos últimos 8 dígitos.
    """
    pool = " ".join([*STORE_CONTACTS, *allowed]).lower()
    pool_digits = {_digits(m.group(0))[-8:] for m in _PHONE.finditer(pool)}
    removed: list[str] = []

    def email(m: re.Match[str]) -> str:
        if m.group(0).lower() in pool:
            return m.group(0)
        removed.append(m.group(0))
        return REDACTED

    def phone(m: re.Match[str]) -> str:
        if _digits(m.group(0))[-8:] in pool_digits:
            return m.group(0)
        removed.append(m.group(0).strip())
        return REDACTED

    return _PHONE.sub(phone, _EMAIL.sub(email, answer)), removed


def recites_prompt(answer: str, system_prompt: str) -> bool:
    """A resposta repete um trecho longo (16+ palavras seguidas) das instruções internas?

    Com 8 ou 12 palavras, frases legítimas casavam ("o número do pedido e o e-mail ou telefone
    do titular" tem 12).
    """
    words = normalize(system_prompt).split()
    grams = {" ".join(words[i : i + LEAK_NGRAM]) for i in range(len(words) - LEAK_NGRAM + 1)}
    a = normalize(answer).split()
    return any(" ".join(a[i : i + LEAK_NGRAM]) in grams for i in range(len(a) - LEAK_NGRAM + 1))
