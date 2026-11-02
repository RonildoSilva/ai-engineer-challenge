"""Horário de funcionamento (manual §2), no fuso de Campo Grande/MS (UTC-4, sem horário de verão).

- Segunda a sexta: 09:00–18:00 · sábado: 09:00–13:00 · domingo e feriados: fechado.
- Feriados considerados: nacionais fixos, Sexta-feira Santa (móvel), Divisão do Estado de MS
  (11/10) e Aniversário de Campo Grande (26/08). Carnaval e Corpus Christi são pontos
  facultativos e não fecham a loja (suposição documentada).
- Datas especiais (Black Friday, Aniversário da Loja, Natal) podem ter horário estendido; o
  manual manda consultar as redes sociais, então o agente apenas avisa dessa possibilidade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Campo_Grande")
SUNDAY = 6
HOURS: dict[int, tuple[time, time] | None] = {
    0: (time(9), time(18)),  # segunda
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),  # sexta
    5: (time(9), time(13)),  # sábado
    6: None,  # domingo
}
WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira",
            "sábado", "domingo"]  # fmt: skip
FIXED_HOLIDAYS = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (8, 26): "Aniversário de Campo Grande",
    (9, 7): "Independência do Brasil",
    (10, 11): "Divisão do Estado de Mato Grosso do Sul",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (11, 20): "Dia da Consciência Negra",
    (12, 25): "Natal",
}
SPECIAL_HOURS_NOTE = (
    "Em datas especiais (Black Friday, Aniversário da Loja, Natal) o horário pode ser estendido; "
    "as mudanças são avisadas nas redes sociais e no WhatsApp."
)


def easter(year: int) -> date:
    """Domingo de Páscoa (algoritmo anônimo gregoriano)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def holiday(d: date) -> str | None:
    if name := FIXED_HOLIDAYS.get((d.month, d.day)):
        return name
    if d == easter(d.year) - timedelta(days=2):
        return "Sexta-feira Santa"
    return None


def hours_on(d: date) -> tuple[time, time] | None:
    return None if holiday(d) else HOURS[d.weekday()]


@dataclass(frozen=True)
class StoreStatus:
    is_open: bool
    now: datetime
    closes_at: datetime | None  # se aberta
    next_opening: datetime | None  # se fechada
    reason: str | None  # feriado, domingo, fora do horário

    def describe(self) -> str:
        if self.is_open and self.closes_at:
            return (
                f"A loja está aberta agora e fecha às {self.closes_at:%H:%M}. O atendimento pelo "
                "WhatsApp segue o mesmo horário."
            )
        when = _when(self.now, self.next_opening) if self.next_opening else "em breve"
        why = f" ({self.reason})" if self.reason else ""
        return (
            f"A loja está fechada agora{why}. Abre {when}. Fora do expediente, o WhatsApp tem "
            "resposta automática."
        )


def _when(now: datetime, opening: datetime) -> str:
    days = (opening.date() - now.date()).days
    if days == 0:
        return f"hoje às {opening:%H:%M}"
    if days == 1:
        return f"amanhã ({WEEKDAYS[opening.weekday()]}) às {opening:%H:%M}"
    return f"{WEEKDAYS[opening.weekday()]}, {opening:%d/%m}, às {opening:%H:%M}"


def store_status(now: datetime | None = None) -> StoreStatus:
    """Aberta/fechada em `now` (horário de Campo Grande; datas sem fuso são tomadas como locais)."""
    now = datetime.now(TZ) if now is None else now
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    today = hours_on(now.date())
    if today and today[0] <= now.time() < today[1]:
        closes = datetime.combine(now.date(), today[1], TZ)
        return StoreStatus(is_open=True, now=now, closes_at=closes, next_opening=None, reason=None)

    reason = holiday(now.date()) or ("domingo" if now.weekday() == SUNDAY else "fora do horário")
    if today and now.time() < today[0]:
        nxt = datetime.combine(now.date(), today[0], TZ)
    else:
        d = now.date() + timedelta(days=1)
        while (h := hours_on(d)) is None:
            d += timedelta(days=1)
        nxt = datetime.combine(d, h[0], TZ)
    return StoreStatus(is_open=False, now=now, closes_at=None, next_opening=nxt, reason=reason)


def weekly_hours() -> str:
    return "Segunda a sexta: 09:00 às 18:00 · Sábado: 09:00 às 13:00 · Domingo e feriados: fechado."
