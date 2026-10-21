"""Dispositivo IoT simulado: mede, assina na origem e obedece a comandos.

O dispositivo não conhece a faixa do serviço nem as janelas de lacre. Ele só
produz leituras; se uma leitura é desvio, violação ou conformidade, quem diz é
o contrato. Por isso os comandos descrevem o mundo físico (a temperatura subiu,
o lacre abriu, o aparelho calou), nunca o rótulo do evento.

Os maliciosos são ataques de verdade, não leituras etiquetadas: assinatura que
não recupera o dispositivo, reenvio byte a byte de uma leitura já emitida e
valor assinado corretamente mas fisicamente impossível. Cada um precisa ser
rejeitável pelo oráculo por um motivo próprio.
"""

import random
from dataclasses import dataclass, replace

from eth_account.signers.local import LocalAccount

from oraculo.assinatura import Leitura, LeituraAssinada, assinar
from oraculo.contas import conta
from oraculo.relogio import RelogioVirtual

# Fora dos papéis do deploy (0 a 5), para que a assinatura forjada nunca venha
# de uma conta que tenha qualquer autorização no sistema.
INDICE_ATACANTE = 9

# Décimos de grau e %, como na leitura. 120 °C e 250 % estão fora de qualquer
# faixa física de contêiner, mas cabem em int16/uint8: a leitura codifica e
# assina normalmente, e só a checagem de plausibilidade do oráculo a barra.
TEMPERATURA_IMPOSSIVEL = 1200
UMIDADE_IMPOSSIVEL = 250
# 40 °C entre duas amostras: valor isolado plausível, variação não.
SALTO_IMPOSSIVEL = 400

MODOS_ASSINATURA_INVALIDA = ("outra_chave", "adulterada")
MODOS_VALOR_IMPOSSIVEL = ("temperatura", "umidade", "salto")


@dataclass(frozen=True)
class Base:
    temperatura: int  # décimos de grau
    umidade: int  # %
    ruido_temperatura: int = 2  # amplitude máxima, em décimos
    ruido_umidade: int = 1


@dataclass(frozen=True)
class _Intervalo:
    inicio: int
    fim: int  # exclusivo: duração d a partir de t cobre [t, t + d)
    valor: int | None = None

    def contem(self, instante: int) -> bool:
        return self.inicio <= instante < self.fim


@dataclass
class _Injecao:
    tipo: str
    modo: str
    inicio: int
    restantes: int


class Dispositivo:
    def __init__(
        self,
        conta_dispositivo: LocalAccount,
        servico_id: int,
        relogio: RelogioVirtual,
        periodo_amostra_seg: int,
        base: Base,
        semente: int = 0,
        conta_atacante: LocalAccount | None = None,
    ) -> None:
        if periodo_amostra_seg <= 0:
            raise ValueError(f"período de amostra inválido: {periodo_amostra_seg}")
        if not 0 <= base.umidade <= 100:
            raise ValueError(f"umidade base fora de 0–100: {base.umidade}")
        self.conta = conta_dispositivo
        self.servico_id = servico_id
        self.relogio = relogio
        self.periodo_amostra_seg = periodo_amostra_seg
        self.base = base
        self.conta_atacante = conta_atacante or conta(INDICE_ATACANTE)
        # Gerador próprio e semeado: duas repetições com a mesma semente e o
        # mesmo roteiro produzem exatamente as mesmas leituras e assinaturas.
        self._rng = random.Random(semente)
        self.nonce = 0  # último usado; a primeira leitura sai com 1
        self._proxima_amostra: int | None = None
        self._ultima_legitima: LeituraAssinada | None = None
        self._excursoes: list[_Intervalo] = []
        self._lacre: list[_Intervalo] = []
        self._silencios: list[_Intervalo] = []
        self._injecoes: list[_Injecao] = []

    # --- comandos do mundo físico -------------------------------------------

    def excursao_temperatura(
        self, temperatura: int, duracao: int, inicio: int | None = None
    ) -> None:
        self._agendar(self._excursoes, "excursão", inicio, duracao, temperatura)

    # O dispositivo não sabe se existe janela autorizada: ele só relata que o
    # lacre abriu. Dentro ou fora de janela é decisão do contrato.
    def abrir_lacre(self, duracao: int, inicio: int | None = None) -> None:
        self._agendar(self._lacre, "lacre aberto", inicio, duracao)

    def silenciar(self, duracao: int, inicio: int | None = None) -> None:
        self._agendar(self._silencios, "silêncio", inicio, duracao)

    # --- comandos maliciosos ------------------------------------------------
    # São injeções discretas, contadas em amostras e não em segundos: cada uma
    # toma o lugar de uma amostra a partir de `inicio`, para que o roteiro
    # saiba exatamente quantas rejeições esperar.

    def assinatura_invalida(
        self, modo: str = "outra_chave", inicio: int | None = None, quantidade: int = 1
    ) -> None:
        self._injetar(
            "assinatura_invalida", modo, MODOS_ASSINATURA_INVALIDA, inicio, quantidade
        )

    def nonce_repetido(self, inicio: int | None = None, quantidade: int = 1) -> None:
        self._injetar("nonce_repetido", "reenvio", ("reenvio",), inicio, quantidade)

    def valor_impossivel(
        self, modo: str = "temperatura", inicio: int | None = None, quantidade: int = 1
    ) -> None:
        self._injetar(
            "valor_impossivel", modo, MODOS_VALOR_IMPOSSIVEL, inicio, quantidade
        )

    # --- emissão ------------------------------------------------------------

    def emitir(self) -> LeituraAssinada | None:
        agora = self.relogio.agora()
        # O dispositivo amostra no próprio ritmo; quem conduz pode avançar o
        # relógio de segundo em segundo sem multiplicar as leituras.
        if self._proxima_amostra is not None and agora < self._proxima_amostra:
            return None
        self._proxima_amostra = agora + self.periodo_amostra_seg

        # Silêncio vence tudo: aparelho desligado não mede nem é imitado, e a
        # lacuna precisa chegar ao contrato como tempo não observado.
        if any(s.contem(agora) for s in self._silencios):
            return None

        injecao = next(
            (i for i in self._injecoes if i.restantes > 0 and i.inicio <= agora), None
        )
        if injecao is not None:
            injecao.restantes -= 1
            return self._malicioso(injecao, agora)

        leitura_assinada = self._assinada(self._medir(agora))
        self._ultima_legitima = leitura_assinada
        return leitura_assinada

    # --- interno ------------------------------------------------------------

    def _agendar(
        self,
        lista: list[_Intervalo],
        nome: str,
        inicio: int | None,
        duracao: int,
        valor: int | None = None,
    ) -> None:
        if duracao <= 0:
            raise ValueError(f"{nome}: duração deve ser positiva, recebeu {duracao}")
        inicio = self.relogio.agora() if inicio is None else inicio
        novo = _Intervalo(inicio, inicio + duracao, valor)
        # Duas excursões sobrepostas não têm leitura única possível; recusar é
        # melhor que escolher uma em silêncio e medir o cenário errado.
        for existente in lista:
            if novo.inicio < existente.fim and existente.inicio < novo.fim:
                raise ValueError(
                    f"{nome} em [{novo.inicio}, {novo.fim}) sobrepõe "
                    f"[{existente.inicio}, {existente.fim})"
                )
        lista.append(novo)

    def _injetar(
        self,
        tipo: str,
        modo: str,
        modos: tuple[str, ...],
        inicio: int | None,
        quantidade: int,
    ) -> None:
        if modo not in modos:
            raise ValueError(
                f"{tipo}: modo {modo!r} desconhecido; esperados: {', '.join(modos)}"
            )
        if quantidade <= 0:
            raise ValueError(
                f"{tipo}: quantidade deve ser positiva, recebeu {quantidade}"
            )
        inicio = self.relogio.agora() if inicio is None else inicio
        self._injecoes.append(_Injecao(tipo, modo, inicio, quantidade))

    def _medir(self, agora: int) -> Leitura:
        # O ruído é sorteado sempre, nos dois campos, para que uma excursão
        # não desloque a sequência aleatória das leituras seguintes.
        base = self.base
        ruido_t = self._rng.randint(-base.ruido_temperatura, base.ruido_temperatura)
        ruido_u = self._rng.randint(-base.ruido_umidade, base.ruido_umidade)
        excursao = next((e for e in self._excursoes if e.contem(agora)), None)
        temperatura = excursao.valor if excursao is not None else self.base.temperatura
        self.nonce += 1
        return Leitura(
            dispositivo=self.conta.address,
            servico_id=self.servico_id,
            nonce=self.nonce,
            timestamp=agora,
            temperatura=temperatura + ruido_t,
            umidade=min(100, max(0, self.base.umidade + ruido_u)),
            lacre_aberto=any(l.contem(agora) for l in self._lacre),
        )

    def _assinada(
        self, leitura: Leitura, chave: LocalAccount | None = None
    ) -> LeituraAssinada:
        return LeituraAssinada(leitura, assinar(leitura, chave or self.conta))

    def _malicioso(self, injecao: _Injecao, agora: int) -> LeituraAssinada:
        if injecao.tipo == "nonce_repetido":
            if self._ultima_legitima is None:
                raise RuntimeError("nonce_repetido sem leitura anterior para reenviar")
            # O mesmo objeto, byte a byte: assinatura válida, nonce já usado.
            return self._ultima_legitima

        if injecao.tipo == "assinatura_invalida" and injecao.modo == "outra_chave":
            # Forjada por terceiro com o próximo nonce, que é o palpite mais
            # convincente. O contador do dispositivo não anda: quem forjou foi
            # o atacante, e a próxima leitura legítima reusa esse nonce — o
            # oráculo não pode tê-lo consumido.
            forjada = self._medir(agora)
            self.nonce -= 1
            return self._assinada(forjada, self.conta_atacante)

        leitura = self._medir(agora)
        if injecao.tipo == "assinatura_invalida":
            # Adulteração em trânsito: o dispositivo assinou a leitura real e
            # alguém inverteu o lacre depois — esconder uma abertura, ou forjar
            # uma para provocar rescisão.
            original = self._assinada(leitura)
            adulterada = replace(leitura, lacre_aberto=not leitura.lacre_aberto)
            return LeituraAssinada(adulterada, original.assinatura)

        # valor_impossivel: sensor defeituoso ou adulterado, mas a assinatura é
        # legítima. Só a plausibilidade separa esta leitura de uma real.
        if injecao.modo == "temperatura":
            leitura = replace(leitura, temperatura=TEMPERATURA_IMPOSSIVEL)
        elif injecao.modo == "umidade":
            leitura = replace(leitura, umidade=UMIDADE_IMPOSSIVEL)
        else:
            anterior = (
                self._ultima_legitima.leitura.temperatura
                if self._ultima_legitima is not None
                else self.base.temperatura
            )
            # Sobe a partir do frio e desce a partir do calor, para o salto
            # cair dentro da faixa física e ser barrado só pela variação.
            salto = SALTO_IMPOSSIVEL if anterior <= 0 else -SALTO_IMPOSSIVEL
            leitura = replace(leitura, temperatura=anterior + salto)
        return self._assinada(leitura)
