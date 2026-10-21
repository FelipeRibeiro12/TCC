"""Agregação das leituras aceitas em checkpoints para `registrarCheckpoint`.

Cada checkpoint custa gás, então o modo `agregado` só escreve na cadeia quando
algo muda (evento) ou quando o contrato precisa de sinal de vida (batimento).
O valor enviado é sempre o pior da janela, nunca a média: 21 °C e 42 °C têm
média dentro de muitas faixas, e a excursão sumiria.

O agregador nunca inventa checkpoint. Se o dispositivo cala, nada é emitido,
e o intervalo chega ao contrato como lacuna entre dois checkpoints — é isso
que vira tempo não observado e impede que desligar o aparelho compense.

O modo `bruto` (uma leitura, um checkpoint) existe para o cenário 6, que mede
quanto a agregação economiza em relação a registrar tudo.
"""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from eth_utils import keccak

from oraculo.assinatura import LeituraAssinada, hash_leitura


class Modo(StrEnum):
    AGREGADO = "agregado"
    BRUTO = "bruto"


class Disparo(StrEnum):
    PRIMEIRA = "primeira"
    FAIXA = "faixa"
    LACRE = "lacre"
    BATIMENTO = "batimento"
    BRUTO = "bruto"
    FINAL = "final"


class Condicao(IntEnum):
    ABAIXO = -1
    DENTRO = 0
    ACIMA = 1


@dataclass(frozen=True)
class Checkpoint:
    servico_id: int
    hash_leitura: bytes
    temperatura: int
    umidade: int
    lacre_aberto: bool
    timestamp_leitura: int
    disparo: Disparo
    # Leituras que o hash resume, em ordem. Ficam fora da cadeia: é com elas
    # que um auditor recalcula `hash_leitura` e confere o que foi resumido.
    lote: tuple[LeituraAssinada, ...] = field(repr=False)

    # Na ordem dos parâmetros de `registrarCheckpoint`, depois do servicoId.
    def argumentos(self) -> tuple:
        return (
            self.servico_id,
            self.hash_leitura,
            self.temperatura,
            self.umidade,
            self.lacre_aberto,
            self.timestamp_leitura,
        )


def hash_lote(lote: tuple[LeituraAssinada, ...] | list[LeituraAssinada]) -> bytes:
    if not lote:
        raise ValueError("lote vazio não tem hash")
    return keccak(b"".join(hash_leitura(item.leitura) for item in lote))


class Agregador:
    def __init__(
        self,
        temp_min: int,
        temp_max: int,
        periodicidade_seg: int,
        modo: Modo | str = Modo.AGREGADO,
        margem_seg: int = 0,
    ) -> None:
        if temp_min > temp_max:
            raise ValueError("temp_min maior que temp_max")
        if periodicidade_seg <= 0:
            raise ValueError(
                f"periodicidade_seg deve ser positiva: {periodicidade_seg}"
            )
        if margem_seg < 0:
            raise ValueError(f"margem_seg negativa: {margem_seg}")
        self.temp_min = temp_min
        self.temp_max = temp_max
        self.periodicidade_seg = periodicidade_seg
        self.modo = Modo(modo)
        # Folga para jitter na entrega das leituras: o batimento sai quando a
        # próxima amostra prevista chegaria a menos de `margem_seg` do prazo.
        self.margem_seg = margem_seg
        self.checkpoints: list[Checkpoint] = []
        self._pendentes: list[LeituraAssinada] = []
        self._anterior: LeituraAssinada | None = None

    def condicao(self, temperatura: int) -> Condicao:
        if temperatura < self.temp_min:
            return Condicao.ABAIXO
        if temperatura > self.temp_max:
            return Condicao.ACIMA
        return Condicao.DENTRO

    def adicionar(self, leitura_assinada: LeituraAssinada) -> Checkpoint | None:
        leitura = leitura_assinada.leitura
        anterior = self._anterior
        if anterior is not None:
            # Quem chama passa só leituras já aceitas pelo Validador, que
            # garante isso; leitura fora de ordem aqui é erro de integração.
            if leitura.servico_id != anterior.leitura.servico_id:
                raise ValueError("um agregador atende um serviço só")
            if leitura.timestamp < anterior.leitura.timestamp:
                raise ValueError("leituras fora de ordem de timestamp")
        self._anterior = leitura_assinada
        self._pendentes.append(leitura_assinada)

        disparo = self._disparo(leitura_assinada, anterior)
        if disparo is None:
            return None
        return self._fechar(leitura_assinada, disparo)

    # Sem isso, as leituras depois do último checkpoint nunca seriam
    # ancoradas: o hash delas ficaria fora da cadeia e o pior valor do fim da
    # viagem não chegaria ao contrato.
    def descarregar(self) -> Checkpoint | None:
        if not self._pendentes:
            return None
        return self._fechar(self._pendentes[-1], Disparo.FINAL)

    def _disparo(
        self, atual: LeituraAssinada, anterior: LeituraAssinada | None
    ) -> Disparo | None:
        if self.modo is Modo.BRUTO:
            return Disparo.BRUTO
        if not self.checkpoints:
            return Disparo.PRIMEIRA
        ultimo = self.checkpoints[-1]
        leitura = atual.leitura
        # Compara com o último checkpoint, e não com a leitura anterior: é o
        # estado que o contrato conhece. Abaixo→acima também é troca de faixa.
        if self.condicao(leitura.temperatura) != self.condicao(ultimo.temperatura):
            return Disparo.FAIXA
        if leitura.lacre_aberto != ultimo.lacre_aberto:
            return Disparo.LACRE

        decorrido = leitura.timestamp - ultimo.timestamp_leitura
        # Prevê a próxima amostra pelo último intervalo observado. Sem isso,
        # amostra de 7 s com periodicidade de 60 s mandaria o batimento aos
        # 63 s, e o contrato contaria 3 s como não observados a cada janela.
        assert anterior is not None
        intervalo = leitura.timestamp - anterior.leitura.timestamp
        if decorrido + intervalo + self.margem_seg > self.periodicidade_seg:
            return Disparo.BATIMENTO
        return None

    def _fechar(self, gatilho: LeituraAssinada, disparo: Disparo) -> Checkpoint:
        lote = tuple(self._pendentes)
        self._pendentes.clear()
        if self.modo is Modo.BRUTO:
            hash_cp = hash_leitura(gatilho.leitura)
        else:
            hash_cp = hash_lote(lote)

        # Toda troca de faixa ou de lacre dispara na hora, então o lote só
        # mistura condições quando o disparo é justamente a troca: as leituras
        # antigas são da condição que o contrato já conhece, e mandar o pior
        # delas com o timestamp do gatilho faria a volta à faixa parecer mais
        # tempo fora. O pior sai então das leituras na condição do gatilho;
        # as demais continuam no hash, para auditoria.
        condicao = self.condicao(gatilho.leitura.temperatura)
        janela = [
            item.leitura
            for item in lote
            if self.condicao(item.leitura.temperatura) == condicao
            and item.leitura.lacre_aberto == gatilho.leitura.lacre_aberto
        ]
        checkpoint = Checkpoint(
            servico_id=gatilho.leitura.servico_id,
            hash_leitura=hash_cp,
            temperatura=self._pior_temperatura(
                [item.temperatura for item in janela], condicao
            ),
            # O contrato não tem faixa de umidade; o pior é o mais úmido, que é
            # o risco de condensação e mofo na carga.
            umidade=max(item.umidade for item in janela),
            lacre_aberto=any(item.lacre_aberto for item in janela),
            timestamp_leitura=gatilho.leitura.timestamp,
            disparo=disparo,
            lote=lote,
        )
        self.checkpoints.append(checkpoint)
        return checkpoint

    def _pior_temperatura(self, valores: list[int], condicao: Condicao) -> int:
        if condicao is Condicao.ACIMA:
            return max(valores)
        if condicao is Condicao.ABAIXO:
            return min(valores)
        # Dentro da faixa, o pior é o mais perto de sair dela.
        return min(valores, key=lambda t: min(t - self.temp_min, self.temp_max - t))
