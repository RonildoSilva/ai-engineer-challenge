"""Modelos de saída das tools MCP (viram `structuredContent` + texto JSON para o LLM).

Convenção de todas as saídas:
- campos estruturados para quem consome programaticamente;
- `resposta`: texto pronto, já seguindo as regras do manual (preço com promoção e PIX, prazos…);
- `orientacao`: instrução para o agente quando há algo a fazer (perguntar, redirecionar,
  encaminhar à equipe). Ausente quando não há nada além de responder.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Produto(BaseModel):
    id: int
    nome: str
    categoria: str
    disponibilidade: str = Field(description="disponivel | esgotado | descontinuado | em_breve")
    preco: float = Field(description="preço de tabela")
    preco_final: float = Field(description="o que o cliente paga (promocional, se houver)")
    promocao: str | None = Field(default=None, description="ex.: 'Oferta Especial (10%)'")
    preco_pix: float | None = Field(
        default=None, description="com 5% no PIX; ausente se já em promoção (não acumula)"
    )
    resumo_preco: str
    especificacoes: dict[str, str] = Field(default_factory=dict)
    descricao: str | None = None


class BuscaProdutos(BaseModel):
    situacao: str = Field(description="ok | accessory | empty_category | no_match")
    filtros: dict[str, str | float | bool | None]
    produtos: list[Produto]
    orientacao: str | None = None


class DetalheProduto(BaseModel):
    situacao: str = Field(description="found | ambiguous | not_found | accessory")
    produto: Produto | None = None
    candidatos: list[Produto] = Field(default_factory=list)
    alternativas: list[Produto] = Field(default_factory=list)
    orientacao: str | None = None


class ItemPedido(BaseModel):
    produto: str
    quantidade: int


class Pedido(BaseModel):
    verificado: bool
    numero: int | None = None
    cliente: str | None = Field(default=None, description="primeiro nome do titular")
    status: str | None = None
    data: str | None = None
    itens: list[ItemPedido] = Field(default_factory=list)
    total: float | None = None
    pagamento: str | None = None
    rastreio: str | None = None
    previsao_entrega: str | None = None
    atrasado: bool = False
    resposta: str


class Devolucao(BaseModel):
    verificado: bool
    motivo: str | None = Field(default=None, description="arrependimento | defeito | preferencia")
    elegivel: bool = False
    regra: str | None = None
    prazo_final: str | None = None
    dias_restantes: int | None = None
    condicoes: list[str] = Field(default_factory=list)
    proximos_passos: list[str] = Field(default_factory=list)
    resposta: str


class Parcela(BaseModel):
    vezes: int
    valor: float


class Pagamento(BaseModel):
    valor: float
    preco_pix: float | None
    parcelas: list[Parcela]
    max_parcelas: int
    combinacao_permitida: bool
    parcelas_solicitadas_ok: bool | None = None
    resposta: str


class OpcaoFrete(BaseModel):
    modalidade: str
    prazo: str
    valor: float | None = Field(description="0 = grátis; ausente = calculado pelo CEP")


class Frete(BaseModel):
    regiao_metropolitana: bool
    opcoes: list[OpcaoFrete]
    frete_especial: bool
    resposta: str


class StatusLoja(BaseModel):
    aberta: bool
    agora: str
    fecha_as: str | None = None
    proxima_abertura: str | None = None
    motivo: str | None = None
    horario_semanal: str
    resposta: str
    orientacao: str


class SecaoPolitica(BaseModel):
    id: str
    titulo: str
    tipo: str = Field(description="policy (regra) | guideline (instrução ao atendente)")
    tokens_estimados: int
    conteudo: str | None = Field(default=None, description="ausente com detalhe='resumo'")
    uri: str = Field(description="resource MCP com o texto da seção")


class ConsultaPolitica(BaseModel):
    secoes: list[SecaoPolitica]
    orientacao: str | None = None
