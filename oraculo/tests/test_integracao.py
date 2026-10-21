import json
from pathlib import Path

import pytest
from eth_account.signers.local import LocalAccount
from web3 import Web3

from oraculo.agregacao import Agregador, Disparo
from oraculo.contas import conta_do_papel
from oraculo.oraculo import Motivo, Oraculo, ServicoSemSla, carregar_abi
from oraculo.relogio import INICIO_PADRAO, RelogioVirtual
from oraculo.simulador import Base, Dispositivo

pytestmark = pytest.mark.cadeia

URL_NO = "http://127.0.0.1:8545"
ARTEFATOS = Path(__file__).resolve().parents[2] / "contracts" / "artifacts" / "contracts"
SERVICO = 1
BASE = Base(temperatura=50, umidade=60)


@pytest.fixture(scope="module")
def w3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(URL_NO, request_kwargs={"timeout": 2}))
    if not w3.is_connected():
        pytest.skip(f"sem nó em {URL_NO}; rode `npx hardhat node` em contracts/")
    return w3


def _artefato(nome: str) -> dict:
    caminho = ARTEFATOS / f"{nome}.sol" / f"{nome}.json"
    if not caminho.exists():
        pytest.skip(f"{caminho} ausente; rode `npx hardhat compile` em contracts/")
    return json.loads(caminho.read_text(encoding="utf-8"))


def _implantar(w3: Web3, nome: str, *argumentos) -> str:
    dono = conta_do_papel("dono")
    artefato = _artefato(nome)
    fabrica = w3.eth.contract(abi=artefato["abi"], bytecode=artefato["bytecode"])
    tx = fabrica.constructor(*argumentos).build_transaction(
        {"from": dono.address, "nonce": w3.eth.get_transaction_count(dono.address)}
    )
    recibo = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(dono.sign_transaction(tx).raw_transaction)
    )
    return recibo["contractAddress"]


# Contratos novos por teste, como o estado reiniciado entre repetições que a
# divisão 3 exige.
@pytest.fixture
def escrow(w3: Web3) -> str:
    twin = _implantar(w3, "AssetTwin", conta_do_papel("dono").address)
    return _implantar(w3, "ShipmentEscrow", twin, conta_do_papel("oraculo").address)


# O nó é compartilhado entre testes e só anda para frente; o relógio parte de
# depois do último bloco para que `sincronizar_no` sempre consiga alinhar.
@pytest.fixture
def relogio(w3: Web3) -> RelogioVirtual:
    ultimo = w3.eth.get_block("latest")["timestamp"]
    return RelogioVirtual(max(INICIO_PADRAO, ultimo + 100))


def _oraculo(
    w3: Web3, escrow: str, relogio: RelogioVirtual, remetente: LocalAccount
) -> Oraculo:
    return Oraculo(
        w3,
        conta_oraculo=remetente,
        endereco_escrow=escrow,
        relogio=relogio,
        dispositivos_por_servico={SERVICO: conta_do_papel("dispositivo").address},
    )


def _checkpoint(relogio: RelogioVirtual):
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )
    # SLA fixo aqui porque o teste exercita só o envio; o caminho que lê o SLA
    # em cadeia está em test_receber_leitura_valida_registra_checkpoint.
    return Agregador(20, 80, 60).adicionar(dispositivo.emitir())


def test_checkpoint_do_oraculo_e_aceito(w3, escrow, relogio):
    oraculo = _oraculo(w3, escrow, relogio, conta_do_papel("oraculo"))
    resultado = oraculo.enviar_checkpoint(_checkpoint(relogio))

    assert resultado.status == "ok"
    assert resultado.erro == ""
    assert resultado.funcao == "registrarCheckpoint"
    assert resultado.gas_usado > 0
    assert resultado.instante_injecao == relogio.agora()
    assert resultado.latencia_ms > 0
    recibo = w3.eth.get_transaction_receipt(resultado.tx_hash)
    assert recibo["blockNumber"] == resultado.bloco
    assert recibo["from"] == conta_do_papel("oraculo").address
    # O bloco sai com o instante virtual, não com o relógio real.
    assert w3.eth.get_block(resultado.bloco)["timestamp"] == relogio.agora()
    assert oraculo.transacoes == [resultado]


@pytest.mark.parametrize("papel", ["dispositivo", "contratante"])
def test_checkpoint_de_outra_conta_reverte(w3, escrow, relogio, papel):
    impostor = _oraculo(w3, escrow, relogio, conta_do_papel(papel))
    resultado = impostor.enviar_checkpoint(_checkpoint(relogio))

    assert resultado.status == "revert"
    assert resultado.erro == "apenas oraculo"
    # A rejeição foi minerada: o gás dela entra na conta do cenário 7.
    assert resultado.gas_usado > 0
    assert resultado.bloco is not None
    assert w3.eth.get_transaction_receipt(resultado.tx_hash)["status"] == 0


def test_leitura_maliciosa_nunca_gera_transacao(w3, escrow, relogio):
    oraculo = _oraculo(w3, escrow, relogio, conta_do_papel("oraculo"))
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )
    # A primeira leitura normal fica de fora do oráculo: só serve de original
    # para o reenvio, e não depende de SLA em cadeia.
    original = dispositivo.emitir()
    oraculo.validador.validar(original)
    relogio.avancar(10)

    dispositivo.assinatura_invalida("outra_chave")
    dispositivo.nonce_repetido(inicio=relogio.agora() + 10)
    dispositivo.valor_impossivel("temperatura", inicio=relogio.agora() + 20)
    bloco_antes = w3.eth.block_number
    motivos = []
    for _ in range(3):
        resultado = oraculo.receber(dispositivo.emitir())
        motivos.append(resultado.motivo)
        assert resultado.transacao is None
        assert resultado.checkpoint is None
        relogio.avancar(10)

    assert motivos == [
        Motivo.ASSINATURA_INVALIDA,
        Motivo.NONCE_REPETIDO,
        Motivo.VALOR_IMPOSSIVEL,
    ]
    assert w3.eth.block_number == bloco_antes
    assert oraculo.transacoes == []


def test_servico_nao_aberto_nao_tem_sla(w3, escrow, relogio):
    oraculo = _oraculo(w3, escrow, relogio, conta_do_papel("oraculo"))
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )
    # Sem SLA não há faixa para agregar; seguir com zeros mandaria tudo como
    # fora da faixa e esconderia o erro de roteiro.
    with pytest.raises(ServicoSemSla):
        oraculo.receber(dispositivo.emitir())
    assert oraculo.transacoes == []


def _enviar(w3: Web3, papel: str, chamada, valor: int = 0) -> None:
    remetente = conta_do_papel(papel)
    tx = chamada.build_transaction(
        {
            "from": remetente.address,
            "nonce": w3.eth.get_transaction_count(remetente.address),
            "value": valor,
        }
    )
    w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(remetente.sign_transaction(tx).raw_transaction)
    )


def _abrir_e_aceitar(w3: Web3, escrow: str, relogio: RelogioVirtual) -> int:
    contrato = w3.eth.contract(address=escrow, abi=carregar_abi())
    twin = w3.eth.contract(
        address=contrato.functions.assetTwin().call(), abi=carregar_abi("AssetTwin")
    )
    contratante = conta_do_papel("contratante").address
    _enviar(w3, "dono", twin.functions.criarTwin(contratante, b"\x01" * 32))
    _enviar(
        w3,
        "contratante",
        contrato.functions.abrirServico(
            1,
            conta_do_papel("transportador").address,
            conta_do_papel("recebedor").address,
            20,
            80,
            60,
            relogio.agora() + 3600,
        ),
        valor=10**18,
    )
    servico = contrato.functions.ultimoServicoId().call()
    # Com o esqueleto da divisão 1 os corpos são vazios: nenhum serviço é
    # aberto e não há SLA para ler. O teste passa a valer sozinho quando eles
    # forem preenchidos.
    if servico == 0:
        pytest.skip("contrato ainda é esqueleto: abrirServico não abre serviço")
    _enviar(w3, "transportador", contrato.functions.aceitarServico(servico))
    return servico


def test_receber_leitura_valida_registra_checkpoint(w3, escrow, relogio):
    servico = _abrir_e_aceitar(w3, escrow, relogio)
    oraculo = Oraculo(
        w3,
        conta_oraculo=conta_do_papel("oraculo"),
        endereco_escrow=escrow,
        relogio=relogio,
        dispositivos_por_servico={servico: conta_do_papel("dispositivo").address},
    )
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), servico, relogio, 10, BASE, semente=42
    )
    resultado = oraculo.receber(dispositivo.emitir())

    assert resultado.aceita
    assert resultado.transacao.status == "ok"
    assert oraculo.agregadores[servico].periodicidade_seg == 60


def test_encerrar_envia_o_lote_pendente(w3, escrow, relogio):
    oraculo = _oraculo(w3, escrow, relogio, conta_do_papel("oraculo"))
    # SLA fixo no agregador, como em _checkpoint: o esqueleto não abre serviço.
    oraculo.agregadores[SERVICO] = Agregador(20, 80, 60)
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), SERVICO, relogio, 10, BASE, semente=42
    )
    for _ in range(4):
        oraculo.receber(dispositivo.emitir())
        relogio.avancar(10)
    # Só a primeira leitura disparou; as outras três esperam no lote.
    assert len(oraculo.transacoes) == 1

    resultado = oraculo.encerrar(SERVICO)

    assert resultado is not None
    assert resultado.status == "ok"
    assert oraculo.transacoes[-1] == resultado
    final = oraculo.agregadores[SERVICO].checkpoints[-1]
    assert final.disparo is Disparo.FINAL
    assert len(final.lote) == 3
    assert oraculo.encerrar(SERVICO) is None
    assert len(oraculo.transacoes) == 2


def test_encerrar_servico_sem_leitura_aceita_nao_envia(w3, escrow, relogio):
    oraculo = _oraculo(w3, escrow, relogio, conta_do_papel("oraculo"))
    assert oraculo.encerrar(SERVICO) is None
    assert oraculo.transacoes == []


def test_rescisao_por_lacre_para_os_checkpoints(w3, escrow, relogio):
    servico = _abrir_e_aceitar(w3, escrow, relogio)
    oraculo = Oraculo(
        w3,
        conta_oraculo=conta_do_papel("oraculo"),
        endereco_escrow=escrow,
        relogio=relogio,
        dispositivos_por_servico={servico: conta_do_papel("dispositivo").address},
    )
    dispositivo = Dispositivo(
        conta_do_papel("dispositivo"), servico, relogio, 10, BASE, semente=42
    )
    oraculo.receber(dispositivo.emitir())
    relogio.avancar(10)
    # Sem janela autorizada: a troca de lacre dispara o checkpoint que rescinde.
    dispositivo.abrir_lacre(30)
    violacao = oraculo.receber(dispositivo.emitir())

    assert violacao.transacao.status == "ok"
    assert violacao.servico_encerrado
    enviadas = len(oraculo.transacoes)
    for _ in range(10):
        relogio.avancar(10)
        assert oraculo.receber(dispositivo.emitir()).servico_encerrado
    assert oraculo.encerrar(servico) is None
    assert len(oraculo.transacoes) == enviadas
