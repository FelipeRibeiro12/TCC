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

Só a leitura que passa nas quatro segue para agregação e para a cadeia. O
`Oraculo` junta as três etapas e é o único que assina transação: a chave do
dispositivo nunca sai do simulador.
"""

import json
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from eth_account.signers.local import LocalAccount
from eth_utils import to_checksum_address
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError
from web3.logs import DISCARD

from oraculo.agregacao import Agregador, Checkpoint, Modo
from oraculo.assinatura import LeituraAssinada, signatario
from oraculo.log_rejeicoes import LogRejeicoes
from oraculo.relogio import RelogioVirtual, sincronizar_no

# Relativo ao pacote, e não ao cwd: a divisão 3 roda o runner de outro
# diretório e a ABI publicada pela divisão 1 é a fonte de verdade.
DIRETORIO_ABI = Path(__file__).resolve().parent.parent / "abi"


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


def carregar_abi(nome: str = "ShipmentEscrow") -> list[dict]:
    with open(DIRETORIO_ABI / f"{nome}.json", encoding="utf-8") as arquivo:
        return json.load(arquivo)


class ServicoSemSla(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoTransacao:
    funcao: str
    tx_hash: str | None
    gas_usado: int | None
    bloco: int | None
    status: str  # "ok" ou "revert", o vocabulário do transacoes.csv
    erro: str
    instante_injecao: int  # relógio virtual
    latencia_ms: float  # tempo real, da injeção até o recibo, sem alinhar o nó


@dataclass(frozen=True)
class ResultadoRecebimento:
    motivo: Motivo | None
    checkpoint: Checkpoint | None = None
    transacao: ResultadoTransacao | None = None
    # O contrato já rescindiu ou concluiu: o runner pode sair do loop, porque
    # nada mais desta leitura em diante vai para a cadeia.
    servico_encerrado: bool = False

    @property
    def aceita(self) -> bool:
        return self.motivo is None


_MOTIVO_REVERT = re.compile(r"reverted with reason string '([^']*)'")
_ERRO_CUSTOMIZADO = re.compile(r"reverted with custom error '([^']*)'")
_PANIC = re.compile(r"reverted with panic code (0x[0-9a-fA-F]+)")


def _resumo_revert(mensagem: str) -> str:
    # Só o motivo, que é o que a divisão 3 compara; a mensagem inteira do
    # Hardhat muda de versão para versão. Os três formatos cobrem require com
    # texto, custom error (se a div. 1 trocar os require) e panic.
    if encontrado := _MOTIVO_REVERT.search(mensagem):
        return encontrado.group(1)
    if encontrado := _ERRO_CUSTOMIZADO.search(mensagem):
        return encontrado.group(1)
    if encontrado := _PANIC.search(mensagem):
        return f"panic {encontrado.group(1)}"
    return mensagem


class Oraculo:
    def __init__(
        self,
        w3: Web3,
        *,
        conta_oraculo: LocalAccount,
        endereco_escrow: str,
        relogio: RelogioVirtual,
        dispositivos_por_servico: dict[int, str],
        abi: list[dict] | None = None,
        log_rejeicoes: LogRejeicoes | None = None,
        modo_agregacao: Modo | str = Modo.AGREGADO,
        limites: Limites = Limites(),
        margem_seg: int = 0,
        # Limite fixo em vez de estimate_gas: a estimativa reverte antes de a
        # transação existir, e a rejeição precisa ir para um bloco para que o
        # gás gasto nela também seja medido (cenário 7).
        limite_gas: int = 500_000,
    ) -> None:
        self.w3 = w3
        self.conta = conta_oraculo
        self.relogio = relogio
        self.escrow = w3.eth.contract(
            address=to_checksum_address(endereco_escrow),
            abi=abi if abi is not None else carregar_abi(),
        )
        self.validador = Validador(
            dispositivos_por_servico, relogio, limites, log_rejeicoes
        )
        self.modo_agregacao = Modo(modo_agregacao)
        self.margem_seg = margem_seg
        self.limite_gas = limite_gas
        self.chain_id = w3.eth.chain_id
        self.agregadores: dict[int, Agregador] = {}
        self.transacoes: list[ResultadoTransacao] = []
        self.encerrados: set[int] = set()

    def receber(self, leitura_assinada: LeituraAssinada) -> ResultadoRecebimento:
        inicio_ns = time.perf_counter_ns()
        # Valida mesmo com o serviço encerrado: a rejeição continua sendo dado
        # de resultado e precisa chegar ao log.
        motivo = self.validador.validar(leitura_assinada)
        servico_id = leitura_assinada.leitura.servico_id
        if servico_id in self.encerrados:
            # Depois da rescisão ou da conclusão todo checkpoint reverteria:
            # gás jogado fora e linhas `revert` que o cenário 7 não mede.
            return ResultadoRecebimento(motivo, servico_encerrado=True)
        if motivo is not None:
            return ResultadoRecebimento(motivo)
        agregador = self._agregador(servico_id)
        checkpoint = agregador.adicionar(leitura_assinada)
        if checkpoint is None:
            return ResultadoRecebimento(None)
        transacao = self._enviar(
            "registrarCheckpoint", checkpoint.argumentos(), inicio_ns
        )
        return ResultadoRecebimento(
            None, checkpoint, transacao, servico_id in self.encerrados
        )

    def enviar_checkpoint(self, checkpoint: Checkpoint) -> ResultadoTransacao:
        return self._enviar(
            "registrarCheckpoint", checkpoint.argumentos(), time.perf_counter_ns()
        )

    # O runner chama no fim da rodada, antes de `confirmarEntrega`: o que
    # ficou no lote depois do último checkpoint ainda precisa ir para a cadeia.
    def encerrar(self, servico_id: int) -> ResultadoTransacao | None:
        agregador = self.agregadores.get(servico_id)
        if agregador is None or servico_id in self.encerrados:
            return None
        checkpoint = agregador.descarregar()
        if checkpoint is None:
            return None
        return self.enviar_checkpoint(checkpoint)

    def ver_servico(self, servico_id: int) -> dict:
        valores = self.escrow.functions.verServico(servico_id).call()
        # Nomes tirados da ABI para não depender da ordem dos campos da
        # struct, que a divisão 1 reordena para empacotar os slots.
        abi_funcao = next(
            item for item in self.escrow.abi if item.get("name") == "verServico"
        )
        saida = abi_funcao["outputs"][0]
        return dict(zip((c["name"] for c in saida["components"]), valores))

    def _agregador(self, servico_id: int) -> Agregador:
        agregador = self.agregadores.get(servico_id)
        if agregador is not None:
            return agregador
        # O SLA vem do contrato, não do roteiro: se a divisão 3 errar a faixa
        # no cenário, o oráculo continua avaliando pelo que foi contratado.
        servico = self.ver_servico(servico_id)
        if servico["periodicidadeSeg"] == 0:
            raise ServicoSemSla(
                f"serviço {servico_id} sem periodicidade em cadeia "
                f"(estado {servico['estado']}); foi aberto?"
            )
        agregador = Agregador(
            servico["tempMin"],
            servico["tempMax"],
            servico["periodicidadeSeg"],
            self.modo_agregacao,
            self.margem_seg,
        )
        self.agregadores[servico_id] = agregador
        return agregador

    def _terminou(self, recibo) -> bool:
        # Só os nomes dos eventos, que estão na ABI: a regra de quando o
        # contrato rescinde ou conclui é da divisão 1 e pode mudar.
        eventos = self.escrow.events
        return any(
            evento().process_receipt(recibo, errors=DISCARD)
            for evento in (eventos.ServicoRescindido, eventos.ServicoConcluido)
        )

    def _enviar(self, funcao: str, argumentos: tuple, inicio_ns: int) -> ResultadoTransacao:
        instante = self.relogio.agora()
        # O alinhamento do relógio do nó só existe porque o experimento usa
        # tempo virtual; numa rede real não acontece, então sai da latência
        # que vai para a tabela do artigo.
        inicio_sincronia_ns = time.perf_counter_ns()
        sincronizar_no(self.w3, self.relogio)
        sincronia_ns = time.perf_counter_ns() - inicio_sincronia_ns
        tx_hash, recibo, erro = self._transmitir(funcao, argumentos)
        latencia_ms = (time.perf_counter_ns() - inicio_ns - sincronia_ns) / 1e6

        ok = recibo is not None and recibo["status"] == 1
        if recibo is not None and not ok and not erro:
            erro = "revert sem motivo"
        # Toda função do escrow recebe o servicoId primeiro.
        if ok and self._terminou(recibo):
            self.encerrados.add(argumentos[0])
        resultado = ResultadoTransacao(
            funcao=funcao,
            tx_hash=tx_hash,
            gas_usado=recibo["gasUsed"] if recibo is not None else None,
            bloco=recibo["blockNumber"] if recibo is not None else None,
            status="ok" if ok else "revert",
            erro=erro,
            instante_injecao=instante,
            latencia_ms=latencia_ms,
        )
        self.transacoes.append(resultado)
        return resultado

    def _transmitir(self, funcao: str, argumentos: tuple) -> tuple[str | None, dict | None, str]:
        tx_hash: str | None = None
        recibo = None
        erro = ""
        try:
            tx = getattr(self.escrow.functions, funcao)(*argumentos).build_transaction(
                {
                    "from": self.conta.address,
                    "nonce": self.w3.eth.get_transaction_count(
                        self.conta.address, "pending"
                    ),
                    "gas": self.limite_gas,
                    "chainId": self.chain_id,
                }
            )
            assinada = self.conta.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(assinada.raw_transaction).to_0x_hex()
            recibo = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except (Web3RPCError, ContractLogicError) as excecao:
            # Revert não derruba o experimento. Em automine o Hardhat minera a
            # transação revertida e devolve o hash no erro; com ele o recibo
            # dá o gás e o bloco da rejeição.
            erro = _resumo_revert(str(getattr(excecao, "message", excecao)))
            resposta = getattr(excecao, "rpc_response", None) or {}
            dados = (resposta.get("error") or {}).get("data")
            if isinstance(dados, dict) and dados.get("txHash"):
                tx_hash = dados["txHash"]
                recibo = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return tx_hash, recibo, erro
