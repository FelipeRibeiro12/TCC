import csv
from dataclasses import replace
from pathlib import Path

import pytest
from eth_account.signers.local import LocalAccount

from oraculo.assinatura import Leitura, LeituraAssinada, assinar
from oraculo.log_rejeicoes import CABECALHO, LogRejeicoes
from oraculo.oraculo import Limites, Motivo, Validador
from oraculo.relogio import RelogioVirtual
from oraculo.simulador import Base, Dispositivo

SERVICO = 1
BASE = Base(temperatura=50, umidade=60)


@pytest.fixture
def disp(contas: dict[str, LocalAccount], relogio: RelogioVirtual) -> Dispositivo:
    return Dispositivo(contas["dispositivo"], SERVICO, relogio, 10, BASE, semente=42)


@pytest.fixture
def caminho_log(tmp_path: Path) -> Path:
    return tmp_path / "resultados" / "rejeicoes.csv"


@pytest.fixture
def validador(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, caminho_log: Path
) -> Validador:
    log = LogRejeicoes(caminho_log, "teste", 3)
    return Validador({SERVICO: contas["dispositivo"].address}, relogio, log=log)


# Valida no instante da emissão e só então avança um período de amostra, como
# o runner da divisão 3 vai fazer.
def emitir_e_validar(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> tuple[LeituraAssinada, Motivo | None]:
    leitura_assinada = disp.emitir()
    assert leitura_assinada is not None
    motivo = validador.validar(leitura_assinada)
    relogio.avancar(disp.periodo_amostra_seg)
    return leitura_assinada, motivo


def leitura_manual(
    contas: dict[str, LocalAccount], nonce: int, timestamp: int, **campos
) -> LeituraAssinada:
    leitura = Leitura(
        dispositivo=contas["dispositivo"].address,
        servico_id=campos.pop("servico_id", SERVICO),
        nonce=nonce,
        timestamp=timestamp,
        temperatura=campos.pop("temperatura", 50),
        umidade=campos.pop("umidade", 60),
        lacre_aberto=campos.pop("lacre_aberto", False),
    )
    return LeituraAssinada(leitura, assinar(leitura, contas["dispositivo"]))


def linhas_log(caminho: Path) -> list[dict[str, str]]:
    if not caminho.exists():
        return []
    with caminho.open(newline="", encoding="utf-8") as arquivo:
        return list(csv.DictReader(arquivo))


# --- caminho feliz ---------------------------------------------------------


def test_leituras_normais_passam(
    disp: Dispositivo,
    relogio: RelogioVirtual,
    validador: Validador,
    caminho_log: Path,
) -> None:
    for _ in range(30):
        _, motivo = emitir_e_validar(disp, relogio, validador)
        assert motivo is None
    assert validador.ultimo_nonce[disp.conta.address] == 30
    assert linhas_log(caminho_log) == []


def test_excursao_e_lacre_reais_passam(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    # Excursão e lacre são eventos de SLA, não fraude: julgá-los é do contrato.
    t0 = relogio.agora()
    disp.excursao_temperatura(120, duracao=30, inicio=t0 + 20)
    disp.abrir_lacre(duracao=20, inicio=t0 + 60)
    motivos = [emitir_e_validar(disp, relogio, validador)[1] for _ in range(12)]
    assert motivos == [None] * 12


def test_cadastro_em_minusculas_e_aceito(
    contas: dict[str, LocalAccount], disp: Dispositivo, relogio: RelogioVirtual
) -> None:
    validador = Validador({SERVICO: contas["dispositivo"].address.lower()}, relogio)
    assert emitir_e_validar(disp, relogio, validador)[1] is None


def test_sem_log_tambem_funciona(
    contas: dict[str, LocalAccount], disp: Dispositivo, relogio: RelogioVirtual
) -> None:
    validador = Validador({SERVICO: contas["dispositivo"].address}, relogio)
    disp.valor_impossivel("temperatura")
    assert emitir_e_validar(disp, relogio, validador)[1] is Motivo.VALOR_IMPOSSIVEL


# --- um teste por motivo ---------------------------------------------------


def test_servico_sem_dispositivo_cadastrado(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    la = leitura_manual(contas, 1, relogio.agora(), servico_id=99)
    assert validador.validar(la) is Motivo.DISPOSITIVO_NAO_CADASTRADO


@pytest.mark.parametrize("modo", ["outra_chave", "adulterada"])
def test_assinatura_invalida(
    modo: str, disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    disp.assinatura_invalida(modo)
    assert emitir_e_validar(disp, relogio, validador)[1] is Motivo.ASSINATURA_INVALIDA


def test_assinatura_malformada(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    la = leitura_manual(contas, 1, relogio.agora())
    truncada = LeituraAssinada(la.leitura, la.assinatura[:40])
    assert validador.validar(truncada) is Motivo.ASSINATURA_INVALIDA


def test_leitura_que_nao_codifica_e_assinatura_invalida(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    la = leitura_manual(contas, 1, relogio.agora())
    fora_do_uint8 = LeituraAssinada(replace(la.leitura, umidade=300), la.assinatura)
    assert validador.validar(fora_do_uint8) is Motivo.ASSINATURA_INVALIDA


def test_dispositivo_legitimo_assinando_em_nome_de_outro(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    leitura = Leitura(
        contas["oraculo"].address, SERVICO, 1, relogio.agora(), 50, 60, False
    )
    la = LeituraAssinada(leitura, assinar(leitura, contas["dispositivo"]))
    assert validador.validar(la) is Motivo.ASSINATURA_INVALIDA


def test_nonce_repetido(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    assert emitir_e_validar(disp, relogio, validador)[1] is None
    disp.nonce_repetido()
    assert emitir_e_validar(disp, relogio, validador)[1] is Motivo.NONCE_REPETIDO


def test_nonce_menor_que_o_ultimo_e_repetido(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    agora = relogio.agora()
    assert validador.validar(leitura_manual(contas, 5, agora)) is None
    antigo = leitura_manual(contas, 3, agora)
    assert validador.validar(antigo) is Motivo.NONCE_REPETIDO


def test_timestamp_desatualizado(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    la = disp.emitir()
    assert la is not None
    relogio.avancar(Limites().tolerancia_atraso_seg + 1)
    assert validador.validar(la) is Motivo.TIMESTAMP_DESATUALIZADO


def test_atraso_no_limite_da_tolerancia_passa(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    la = disp.emitir()
    assert la is not None
    relogio.avancar(Limites().tolerancia_atraso_seg)
    assert validador.validar(la) is None


def test_timestamp_futuro(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    adiantada = relogio.agora() + Limites().tolerancia_futuro_seg + 1
    la = leitura_manual(contas, 1, adiantada)
    assert validador.validar(la) is Motivo.TIMESTAMP_FUTURO


def test_timestamp_regressivo(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    agora = relogio.agora()
    assert validador.validar(leitura_manual(contas, 1, agora)) is None
    # Dentro da tolerância de atraso, nonce novo: só a ordem temporal acusa.
    anterior = leitura_manual(contas, 2, agora - 5)
    assert validador.validar(anterior) is Motivo.TIMESTAMP_REGRESSIVO


@pytest.mark.parametrize("modo", ["temperatura", "umidade"])
def test_valor_impossivel(
    modo: str, disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    disp.valor_impossivel(modo)
    assert emitir_e_validar(disp, relogio, validador)[1] is Motivo.VALOR_IMPOSSIVEL


@pytest.mark.parametrize("temperatura", [-401, 851])
def test_valor_impossivel_nos_dois_extremos(
    temperatura: int,
    contas: dict[str, LocalAccount],
    relogio: RelogioVirtual,
    validador: Validador,
) -> None:
    la = leitura_manual(contas, 1, relogio.agora(), temperatura=temperatura)
    assert validador.validar(la) is Motivo.VALOR_IMPOSSIVEL


def test_variacao_impossivel(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    assert emitir_e_validar(disp, relogio, validador)[1] is None
    disp.valor_impossivel("salto")
    la, motivo = emitir_e_validar(disp, relogio, validador)
    assert motivo is Motivo.VARIACAO_IMPOSSIVEL
    limites = Limites()
    assert limites.temperatura_min <= la.leitura.temperatura <= limites.temperatura_max


def test_volta_ao_normal_depois_do_salto_nao_e_variacao(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    emitir_e_validar(disp, relogio, validador)
    disp.valor_impossivel("salto")
    emitir_e_validar(disp, relogio, validador)
    assert emitir_e_validar(disp, relogio, validador)[1] is None


def test_deriva_lenta_depois_de_silencio_longo_passa(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, validador: Validador
) -> None:
    agora = relogio.agora()
    assert validador.validar(leitura_manual(contas, 1, agora)) is None
    relogio.avancar(600)
    # 30 °C em 10 min: degrau impossível, deriva aceitável.
    la = leitura_manual(contas, 2, relogio.agora(), temperatura=350)
    assert validador.validar(la) is None


# --- ordem e consumo do nonce ----------------------------------------------


def test_assinatura_e_checada_antes_do_nonce(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    la, _ = emitir_e_validar(disp, relogio, validador)
    # Nonce já visto e assinatura que não confere: a assinatura vence.
    adulterada = LeituraAssinada(
        replace(la.leitura, temperatura=la.leitura.temperatura + 1), la.assinatura
    )
    assert validador.validar(adulterada) is Motivo.ASSINATURA_INVALIDA


def test_forjada_nao_consome_o_nonce_do_dispositivo(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    disp.assinatura_invalida("outra_chave")
    forjada, motivo = emitir_e_validar(disp, relogio, validador)
    assert motivo is Motivo.ASSINATURA_INVALIDA
    legitima, motivo = emitir_e_validar(disp, relogio, validador)
    assert legitima.leitura.nonce == forjada.leitura.nonce
    assert motivo is None


def test_implausivel_consome_o_nonce(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    disp.valor_impossivel("temperatura")
    la, motivo = emitir_e_validar(disp, relogio, validador)
    assert motivo is Motivo.VALOR_IMPOSSIVEL
    assert validador.validar(la) is Motivo.NONCE_REPETIDO


def test_desatualizada_consome_o_nonce(
    disp: Dispositivo, relogio: RelogioVirtual, validador: Validador
) -> None:
    la = disp.emitir()
    assert la is not None
    relogio.avancar(Limites().tolerancia_atraso_seg + 1)
    assert validador.validar(la) is Motivo.TIMESTAMP_DESATUALIZADO
    assert validador.validar(la) is Motivo.NONCE_REPETIDO


# --- log -------------------------------------------------------------------


def test_cada_rejeicao_gera_uma_linha(
    disp: Dispositivo,
    relogio: RelogioVirtual,
    validador: Validador,
    caminho_log: Path,
) -> None:
    t0 = relogio.agora()
    disp.assinatura_invalida("outra_chave", inicio=t0 + 10)
    disp.nonce_repetido(inicio=t0 + 30)
    disp.valor_impossivel("umidade", inicio=t0 + 50)
    rejeitadas = []
    for _ in range(8):
        recebimento = relogio.agora()
        la, motivo = emitir_e_validar(disp, relogio, validador)
        if motivo is not None:
            rejeitadas.append((la, motivo, recebimento))
    assert [m for _, m, _ in rejeitadas] == [
        Motivo.ASSINATURA_INVALIDA,
        Motivo.NONCE_REPETIDO,
        Motivo.VALOR_IMPOSSIVEL,
    ]

    linhas = linhas_log(caminho_log)
    assert len(linhas) == len(rejeitadas)
    for linha, (la, motivo, recebimento) in zip(linhas, rejeitadas):
        assert linha == {
            "experimento": "teste",
            "repeticao": "3",
            "servico_id": str(SERVICO),
            "dispositivo": la.leitura.dispositivo,
            "nonce": str(la.leitura.nonce),
            "timestamp_leitura": str(la.leitura.timestamp),
            "instante_recebimento": str(recebimento),
            "motivo": motivo.value,
        }


def test_repeticoes_acrescentam_ao_mesmo_arquivo(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, caminho_log: Path
) -> None:
    for repeticao in (1, 2):
        log = LogRejeicoes(caminho_log, "teste", repeticao)
        cadastro = {SERVICO: contas["dispositivo"].address}
        validador = Validador(cadastro, relogio, log=log)
        validador.validar(leitura_manual(contas, 1, relogio.agora(), temperatura=2000))

    with caminho_log.open(encoding="utf-8") as arquivo:
        texto = arquivo.read().splitlines()
    assert texto[0] == ",".join(CABECALHO)
    assert texto.count(",".join(CABECALHO)) == 1
    assert [l["repeticao"] for l in linhas_log(caminho_log)] == ["1", "2"]


def test_limites_incoerentes_sao_recusados() -> None:
    with pytest.raises(ValueError):
        Limites(temperatura_min=100, temperatura_max=0)
    with pytest.raises(ValueError):
        Limites(tolerancia_atraso_seg=-1)
