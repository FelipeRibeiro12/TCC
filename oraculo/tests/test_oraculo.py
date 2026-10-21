import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from oraculo.agregacao import Agregador
from oraculo.contas import conta_do_papel
from oraculo.log_rejeicoes import LogRejeicoes
from oraculo.oraculo import Motivo, Oraculo, Rejeicao, _resumo_revert, carregar_abi
from oraculo.relogio import INICIO_PADRAO, RelogioVirtual
from oraculo.simulador import Base, Dispositivo


def test_abi_carrega_de_qualquer_diretorio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    nomes = {item.get("name") for item in carregar_abi()}
    assert {"registrarCheckpoint", "verServico"} <= nomes


def test_resumo_revert_extrai_o_motivo_do_require() -> None:
    mensagem = (
        "Error: VM Exception while processing transaction: "
        "reverted with reason string 'apenas oraculo'"
    )
    assert _resumo_revert(mensagem) == "apenas oraculo"


def test_resumo_revert_extrai_o_custom_error() -> None:
    mensagem = (
        "Error: VM Exception while processing transaction: "
        "reverted with custom error 'ChamadorNaoAutorizado(\"0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65\")'"
    )
    assert (
        _resumo_revert(mensagem)
        == 'ChamadorNaoAutorizado("0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65")'
    )


def test_resumo_revert_extrai_o_panic() -> None:
    mensagem = (
        "Error: VM Exception while processing transaction: "
        "reverted with panic code 0x11 (Arithmetic operation overflowed "
        "outside of an unchecked block)"
    )
    assert _resumo_revert(mensagem) == "panic 0x11"


def test_resumo_revert_sem_motivo_mantem_a_mensagem() -> None:
    assert _resumo_revert("nonce too low") == "nonce too low"


SERVICO = 1
BASE = Base(temperatura=50, umidade=60)
RECIBO_OK = {"status": 1, "gasUsed": 50_000, "blockNumber": 7, "logs": []}
RECIBO_REVERT = {"status": 0, "gasUsed": 30_000, "blockNumber": 7, "logs": []}


class _W3SemNo:
    # Só o que o construtor do Oraculo consulta; o envio é trocado no teste.
    def __init__(self) -> None:
        self.eth = SimpleNamespace(contract=Web3().eth.contract, chain_id=31337)


@pytest.fixture
def oraculo_sem_no(
    relogio: RelogioVirtual, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Oraculo:
    monkeypatch.setattr("oraculo.oraculo.sincronizar_no", lambda w3, relogio: None)
    oraculo = Oraculo(
        _W3SemNo(),
        conta_oraculo=conta_do_papel("oraculo"),
        endereco_escrow="0x" + "11" * 20,
        relogio=relogio,
        dispositivos_por_servico={SERVICO: conta_do_papel("dispositivo").address},
        log_rejeicoes=LogRejeicoes(tmp_path / "rejeicoes.csv", "teste", 1),
    )
    # SLA fixo: sem nó não há verServico para ler.
    oraculo.agregadores[SERVICO] = Agregador(20, 80, 60)
    return oraculo


def _contar_envios(
    oraculo: Oraculo, monkeypatch: pytest.MonkeyPatch, recibo: dict, terminou: bool
) -> list[str]:
    envios: list[str] = []

    def transmitir(funcao: str, argumentos: tuple):
        envios.append(funcao)
        return "0x" + "ab" * 32, recibo, ""

    monkeypatch.setattr(oraculo, "_transmitir", transmitir)
    monkeypatch.setattr(oraculo, "_terminou", lambda recibo: terminou)
    return envios


def test_envio_que_termina_o_servico_bloqueia_os_seguintes(
    oraculo_sem_no: Oraculo, relogio: RelogioVirtual, monkeypatch: pytest.MonkeyPatch
) -> None:
    envios = _contar_envios(oraculo_sem_no, monkeypatch, RECIBO_OK, terminou=True)
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )

    primeiro = oraculo_sem_no.receber(dispositivo.emitir())
    assert primeiro.transacao is not None
    assert primeiro.servico_encerrado
    assert SERVICO in oraculo_sem_no.encerrados

    # 70 s depois o batimento dispararia um checkpoint; encerrado, nada sai.
    relogio.avancar(70)
    depois = oraculo_sem_no.receber(dispositivo.emitir())
    assert depois.aceita
    assert depois.servico_encerrado
    assert depois.checkpoint is None
    assert depois.transacao is None
    assert envios == ["registrarCheckpoint"]
    assert oraculo_sem_no.encerrar(SERVICO) is None
    assert envios == ["registrarCheckpoint"]


def test_rejeicao_depois_do_encerramento_continua_no_log(
    oraculo_sem_no: Oraculo, relogio: RelogioVirtual, monkeypatch: pytest.MonkeyPatch
) -> None:
    envios = _contar_envios(oraculo_sem_no, monkeypatch, RECIBO_OK, terminou=True)
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )
    oraculo_sem_no.receber(dispositivo.emitir())
    relogio.avancar(10)
    dispositivo.assinatura_invalida()

    resultado = oraculo_sem_no.receber(dispositivo.emitir())

    assert resultado.motivo is Motivo.ASSINATURA_INVALIDA
    assert resultado.servico_encerrado
    linhas = oraculo_sem_no.validador.log.caminho.read_text().splitlines()
    assert len(linhas) == 2  # cabeçalho e a rejeição
    assert envios == ["registrarCheckpoint"]


def test_rejeicao_fica_em_memoria_mesmo_sem_csv(relogio: RelogioVirtual) -> None:
    oraculo = Oraculo(
        _W3SemNo(),
        conta_oraculo=conta_do_papel("oraculo"),
        endereco_escrow="0x" + "11" * 20,
        relogio=relogio,
        dispositivos_por_servico={SERVICO: conta_do_papel("dispositivo").address},
    )
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )
    dispositivo.valor_impossivel("temperatura")
    emitida = dispositivo.emitir()

    resultado = oraculo.receber(emitida)

    assert resultado.motivo is Motivo.VALOR_IMPOSSIVEL
    assert oraculo.rejeicoes == [
        Rejeicao(emitida.leitura, relogio.agora(), Motivo.VALOR_IMPOSSIVEL)
    ]


def test_latencia_nao_conta_a_leitura_do_sla(
    oraculo_sem_no: Oraculo, relogio: RelogioVirtual, monkeypatch: pytest.MonkeyPatch
) -> None:
    _contar_envios(oraculo_sem_no, monkeypatch, RECIBO_OK, terminou=False)
    del oraculo_sem_no.agregadores[SERVICO]

    def ver_servico_lento(servico_id: int) -> dict:
        time.sleep(0.05)
        return {"tempMin": 20, "tempMax": 80, "periodicidadeSeg": 60, "estado": 2}

    monkeypatch.setattr(oraculo_sem_no, "ver_servico", ver_servico_lento)
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )

    resultado = oraculo_sem_no.receber(dispositivo.emitir())

    assert resultado.transacao is not None
    # Com os 50 ms do verServico na conta, nunca ficaria abaixo disso.
    assert 0 < resultado.transacao.latencia_ms < 50


def test_servico_em_andamento_segue_enviando(
    oraculo_sem_no: Oraculo, relogio: RelogioVirtual, monkeypatch: pytest.MonkeyPatch
) -> None:
    envios = _contar_envios(oraculo_sem_no, monkeypatch, RECIBO_OK, terminou=False)
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )
    oraculo_sem_no.receber(dispositivo.emitir())
    relogio.avancar(70)

    resultado = oraculo_sem_no.receber(dispositivo.emitir())

    assert not resultado.servico_encerrado
    assert resultado.transacao is not None
    assert len(envios) == 2


def test_revert_nao_encerra_o_servico(
    oraculo_sem_no: Oraculo, relogio: RelogioVirtual, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mesmo que o recibo trouxesse o evento, transação revertida não muda
    # estado nenhum no contrato.
    _contar_envios(oraculo_sem_no, monkeypatch, RECIBO_REVERT, terminou=True)
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )

    resultado = oraculo_sem_no.receber(dispositivo.emitir())

    assert resultado.transacao.status == "revert"
    assert not resultado.servico_encerrado
    assert oraculo_sem_no.encerrados == set()


# Log montado à mão a partir da ABI, para testar a leitura do recibo sem nó.
def _log(oraculo: Oraculo, evento: str, *argumentos) -> dict:
    abi = next(
        item
        for item in oraculo.escrow.abi
        if item.get("type") == "event" and item["name"] == evento
    )
    assinatura = f"{evento}({','.join(e['type'] for e in abi['inputs'])})"
    indexados = [a for a, e in zip(argumentos, abi["inputs"]) if e["indexed"]]
    livres = [
        (e["type"], a) for a, e in zip(argumentos, abi["inputs"]) if not e["indexed"]
    ]
    return {
        "address": oraculo.escrow.address,
        "topics": [Web3.keccak(text=assinatura)]
        + [HexBytes(encode(["uint256"], [a])) for a in indexados],
        "data": HexBytes(encode([t for t, _ in livres], [a for _, a in livres])),
        "logIndex": 0,
        "transactionIndex": 0,
        "transactionHash": HexBytes("0x" + "ab" * 32),
        "blockHash": HexBytes("0x" + "cd" * 32),
        "blockNumber": 7,
        "removed": False,
    }


@pytest.mark.parametrize(
    ("evento", "argumentos"),
    [("ServicoRescindido", (SERVICO, 10**18)), ("ServicoConcluido", (SERVICO, 1, 2))],
)
def test_terminou_reconhece_rescisao_e_conclusao(
    oraculo_sem_no: Oraculo, evento: str, argumentos: tuple
) -> None:
    recibo = {**RECIBO_OK, "logs": [_log(oraculo_sem_no, evento, *argumentos)]}
    assert oraculo_sem_no._terminou(recibo)


def test_terminou_ignora_outros_eventos(oraculo_sem_no: Oraculo) -> None:
    checkpoint = _log(
        oraculo_sem_no, "CheckpointRegistrado", SERVICO, b"\x01" * 32, INICIO_PADRAO
    )
    assert not oraculo_sem_no._terminou({**RECIBO_OK, "logs": [checkpoint]})
    assert not oraculo_sem_no._terminou(RECIBO_OK)
