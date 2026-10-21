"""Aplica um roteiro sobre o dispositivo simulado, segundo a segundo.

O executor só produz leituras: não chama a cadeia, não grava CSV de
transações, não autoriza janela de lacre. Isso é do runner da divisão 3, que
itera as emissões e, a cada uma, entrega a leitura ao oráculo no instante em
que o relógio está parado nela.

Cada emissão diz de quais eventos do roteiro a leitura faz parte, para que o
runner ligue checkpoint e rejeição de volta ao `esperado` do cenário sem
reconstruir o roteiro por conta própria.
"""

from dataclasses import dataclass
from typing import Iterator

from eth_account.signers.local import LocalAccount

from oraculo.assinatura import LeituraAssinada
from oraculo.cenario import MALICIOSOS, Cenario, Janela, Tipo
from oraculo.relogio import RelogioVirtual
from oraculo.simulador import Base, Dispositivo


@dataclass(frozen=True)
class Emissao:
    instante: int  # relógio virtual
    segundo: int  # desde o início da rodada, na escala do roteiro
    leitura: LeituraAssinada
    eventos: tuple[int, ...]  # índices em `cenario.eventos`; vazio = leitura normal


class Executor:
    def __init__(
        self,
        cenario: Cenario,
        conta_dispositivo: LocalAccount,
        servico_id: int,
        relogio: RelogioVirtual,
        semente: int | None = None,
    ) -> None:
        if cenario.varrer:
            raise ValueError(
                f"{cenario.nome}: cenário com varredura; execute cada item de variantes()"
            )
        self.cenario = cenario
        self.relogio = relogio
        # O instante em que o executor nasce é o segundo 0 do roteiro; quem
        # conduz precisa abrir o serviço antes, não depois.
        self.t0 = relogio.agora()
        base = cenario.base
        self.dispositivo = Dispositivo(
            conta_dispositivo,
            servico_id,
            relogio,
            cenario.periodo_amostra_seg,
            Base(base.temperatura, base.umidade, base.ruido_temperatura, base.ruido_umidade),
            # A divisão 3 passa semente diferente por repetição; sem isso as 20
            # repetições teriam leituras idênticas byte a byte.
            semente=cenario.semente if semente is None else semente,
        )
        # Espelho das injeções, na mesma ordem em que o dispositivo as recebe:
        # ele consome a primeira pendente já iniciada, e o executor precisa
        # saber qual foi para rotular a leitura.
        self._injecoes: list[list[int]] = []
        for indice, evento in enumerate(cenario.eventos):
            self._agendar(indice, evento)
        self._iterado = False

    def janela_lacre(self) -> Janela | None:
        janela = self.cenario.janela_lacre
        if janela is None:
            return None
        return Janela(self.t0 + janela.inicio, self.t0 + janela.fim)

    def __iter__(self) -> Iterator[Emissao]:
        # Dispositivo, nonce e relógio andam junto com a iteração; repetir a
        # rodada exige um executor novo, como o estado reiniciado da div. 3.
        if self._iterado:
            raise RuntimeError("executor já percorrido; crie outro para nova rodada")
        self._iterado = True
        for segundo in range(self.cenario.duracao_seg):
            leitura = self.dispositivo.emitir()
            if leitura is not None:
                yield Emissao(self.relogio.agora(), segundo, leitura, self._eventos_em(segundo))
            self.relogio.avancar(1)

    def _agendar(self, indice: int, evento) -> None:
        d = self.dispositivo
        inicio = self.t0 + evento.inicio
        if evento.tipo is Tipo.EXCURSAO_TEMPERATURA:
            d.excursao_temperatura(evento.temperatura, evento.duracao, inicio)
        elif evento.tipo is Tipo.ABRIR_LACRE:
            d.abrir_lacre(evento.duracao, inicio)
        elif evento.tipo is Tipo.SILENCIO:
            d.silenciar(evento.duracao, inicio)
        elif evento.tipo is Tipo.ASSINATURA_INVALIDA:
            d.assinatura_invalida(evento.modo, inicio, evento.quantidade)
        elif evento.tipo is Tipo.NONCE_REPETIDO:
            d.nonce_repetido(inicio, evento.quantidade)
        else:
            d.valor_impossivel(evento.modo, inicio, evento.quantidade)
        if evento.tipo in MALICIOSOS:
            self._injecoes.append([indice, evento.inicio, evento.quantidade])

    def _eventos_em(self, segundo: int) -> tuple[int, ...]:
        injecao = next((i for i in self._injecoes if i[2] > 0 and i[1] <= segundo), None)
        if injecao is not None:
            injecao[2] -= 1
            return (injecao[0],)
        return tuple(
            indice
            for indice, evento in enumerate(self.cenario.eventos)
            if evento.tipo in (Tipo.EXCURSAO_TEMPERATURA, Tipo.ABRIR_LACRE)
            and evento.inicio <= segundo < evento.inicio + evento.duracao
        )
