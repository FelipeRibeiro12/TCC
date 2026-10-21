"""Oráculo: qualifica a leitura assinada antes de qualquer contato com a cadeia.

A ordem das checagens é parte da garantia, não detalhe de implementação:

1. assinatura — sem ela nada na leitura é confiável, nem o nonce. Validar o
   nonce antes deixaria um atacante queimar nonces do dispositivo com
   leituras forjadas;
2. replay — nonce estritamente crescente por dispositivo. O nonce é consumido
   logo aqui, antes das demais checagens: uma leitura autêntica mas
   implausível não pode ser reapresentada depois;
3. atualidade — timestamp perto do relógio do oráculo e nunca anterior ao da
   última leitura aceita;
4. plausibilidade — faixa física absoluta e taxa de variação.

Só a leitura que passa nas quatro segue para agregação e para a cadeia.
"""

from dataclasses import dataclass
from enum import StrEnum

from eth_utils import to_checksum_address

from oraculo.assinatura import LeituraAssinada, signatario
from oraculo.log_rejeicoes import LogRejeicoes
from oraculo.relogio import RelogioVirtual


class Motivo(StrEnum):
    DISPOSITIVO_NAO_CADASTRADO = "dispositivo_nao_cadastrado"
    ASSINATURA_INVALIDA = "assinatura_invalida"
    NONCE_REPETIDO = "nonce_repetido"
    TIMESTAMP_DESATUALIZADO = "timestamp_desatualizado"
    TIMESTAMP_FUTURO = "timestamp_futuro"
    TIMESTAMP_REGRESSIVO = "timestamp_regressivo"
    VALOR_IMPOSSIVEL = "valor_impossivel"
    VARIACAO_IMPOSSIVEL = "variacao_impossivel"


@dataclass(frozen=True)
class Limites:
    # Faixa de operação de sensores comerciais de contêiner refrigerado, em
    # décimos de grau. Fora dela a leitura é defeito ou fraude, não excursão.
    temperatura_min: int = -400
    temperatura_max: int = 850
    umidade_min: int = 0
    umidade_max: int = 100
    # A variação admitida entre duas leituras aceitas é
    # `variacao_base_decimos + max_variacao_decimos_por_seg * dt`. A parcela
    # fixa absorve o ruído e o degrau de uma excursão real (porta aberta, falha
    # do compressor), que aparece inteiro de uma amostra para a outra mesmo com
    # amostragem de 1 s; a parcela por segundo cobre a deriva lenta depois de
    # um intervalo longo. Com os padrões, 40 °C entre amostras é barrado para
    # qualquer período de amostra abaixo de 2,5 min.
    variacao_base_decimos: int = 100
    max_variacao_decimos_por_seg: int = 2
    # O relógio é virtual e compartilhado, então atraso real é zero; as
    # tolerâncias existem para que o oráculo continue correto com um
    # dispositivo de relógio próprio. Para o futuro é mais apertada porque
    # leitura adiantada não tem explicação benigna além de deriva.
    tolerancia_atraso_seg: int = 30
    tolerancia_futuro_seg: int = 5

    def __post_init__(self) -> None:
        if self.temperatura_min > self.temperatura_max:
            raise ValueError("temperatura_min maior que temperatura_max")
        if self.umidade_min > self.umidade_max:
            raise ValueError("umidade_min maior que umidade_max")
        negativos = [
            nome
            for nome in (
                "variacao_base_decimos",
                "max_variacao_decimos_por_seg",
                "tolerancia_atraso_seg",
                "tolerancia_futuro_seg",
            )
            if getattr(self, nome) < 0
        ]
        if negativos:
            raise ValueError(f"limites negativos: {', '.join(negativos)}")


class Validador:
    def __init__(
        self,
        dispositivos_por_servico: dict[int, str],
        relogio: RelogioVirtual,
        limites: Limites = Limites(),
        log: LogRejeicoes | None = None,
    ) -> None:
        # Endereço em checksum dos dois lados da comparação: a divisão 3 pode
        # cadastrar em minúsculas, e é o mesmo endereço nos bytes assinados.
        self.dispositivos_por_servico = {
            servico: to_checksum_address(endereco)
            for servico, endereco in dispositivos_por_servico.items()
        }
        self.relogio = relogio
        self.limites = limites
        self.log = log
        # Por dispositivo, e não por serviço: o nonce é do aparelho, e um
        # aparelho reaproveitado em outro serviço não volta a contar do zero.
        self.ultimo_nonce: dict[str, int] = {}
        self._ultima_aceita: dict[str, LeituraAssinada] = {}

    def validar(self, leitura_assinada: LeituraAssinada) -> Motivo | None:
        agora = self.relogio.agora()
        motivo = self._checar(leitura_assinada, agora)
        if motivo is not None and self.log is not None:
            self.log.registrar(leitura_assinada.leitura, agora, motivo)
        return motivo

    def _checar(self, leitura_assinada: LeituraAssinada, agora: int) -> Motivo | None:
        leitura = leitura_assinada.leitura
        cadastrado = self.dispositivos_por_servico.get(leitura.servico_id)
        if cadastrado is None:
            return Motivo.DISPOSITIVO_NAO_CADASTRADO

        # 1. assinatura. Leitura que nem codifica (campo fora do tipo ABI) cai
        # aqui também: sem mensagem bem formada não há o que ter sido assinado.
        # Exigir que o campo `dispositivo` também seja o cadastrado impede que
        # o aparelho legítimo assine em nome de outro.
        try:
            recuperado = signatario(leitura, leitura_assinada.assinatura)
        except ValueError:
            return Motivo.ASSINATURA_INVALIDA
        if (
            recuperado != cadastrado
            or to_checksum_address(leitura.dispositivo) != cadastrado
        ):
            return Motivo.ASSINATURA_INVALIDA

        # 2. replay
        if leitura.nonce <= self.ultimo_nonce.get(cadastrado, 0):
            return Motivo.NONCE_REPETIDO
        self.ultimo_nonce[cadastrado] = leitura.nonce

        # 3. atualidade
        limites = self.limites
        if agora - leitura.timestamp > limites.tolerancia_atraso_seg:
            return Motivo.TIMESTAMP_DESATUALIZADO
        if leitura.timestamp - agora > limites.tolerancia_futuro_seg:
            return Motivo.TIMESTAMP_FUTURO
        anterior = self._ultima_aceita.get(cadastrado)
        if anterior is not None and leitura.timestamp < anterior.leitura.timestamp:
            return Motivo.TIMESTAMP_REGRESSIVO

        # 4. plausibilidade
        faixa_temperatura = (limites.temperatura_min, limites.temperatura_max)
        if not faixa_temperatura[0] <= leitura.temperatura <= faixa_temperatura[1]:
            return Motivo.VALOR_IMPOSSIVEL
        if not limites.umidade_min <= leitura.umidade <= limites.umidade_max:
            return Motivo.VALOR_IMPOSSIVEL
        # Compara com a última aceita, nunca com a última recebida: senão um
        # único salto rejeitado faria a volta ao normal parecer outro salto.
        if anterior is not None:
            dt = leitura.timestamp - anterior.leitura.timestamp
            admitida = limites.variacao_base_decimos + (
                limites.max_variacao_decimos_por_seg * dt
            )
            if abs(leitura.temperatura - anterior.leitura.temperatura) > admitida:
                return Motivo.VARIACAO_IMPOSSIVEL

        self._ultima_aceita[cadastrado] = leitura_assinada
        return None
