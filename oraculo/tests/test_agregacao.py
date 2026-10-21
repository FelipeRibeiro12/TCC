import pytest
from eth_account.signers.local import LocalAccount
from eth_utils import keccak

from oraculo.agregacao import Agregador, Checkpoint, Disparo, Modo, hash_lote
from oraculo.assinatura import Leitura, LeituraAssinada, assinar, hash_leitura
from oraculo.cenario import DIRETORIO_CENARIOS, carregar
from oraculo.executor import Executor
from oraculo.oraculo import Validador
from oraculo.relogio import RelogioVirtual
from oraculo.simulador import Base, Dispositivo

SERVICO = 1
# 2,0 °C a 8,0 °C, faixa típica de carga refrigerada, em décimos de grau.
TEMP_MIN = 20
TEMP_MAX = 80
PERIODICIDADE = 60


@pytest.fixture
def agregador() -> Agregador:
    return Agregador(TEMP_MIN, TEMP_MAX, PERIODICIDADE)


@pytest.fixture
def faixa_larga() -> Agregador:
    # Faixa que contém 21,0 °C, para o teste da excursão a 42,0 °C.
    return Agregador(150, 300, PERIODICIDADE)


def leitura(
    contas: dict[str, LocalAccount],
    nonce: int,
    timestamp: int,
    temperatura: int = 50,
    umidade: int = 60,
    lacre_aberto: bool = False,
) -> LeituraAssinada:
    crua = Leitura(
        dispositivo=contas["dispositivo"].address,
        servico_id=SERVICO,
        nonce=nonce,
        timestamp=timestamp,
        temperatura=temperatura,
        umidade=umidade,
        lacre_aberto=lacre_aberto,
    )
    return LeituraAssinada(crua, assinar(crua, contas["dispositivo"]))


# Uma leitura a cada `periodo` s a partir de t=0, com os valores dados.
def serie(
    contas: dict[str, LocalAccount], valores: list[dict], periodo: int = 10
) -> list[LeituraAssinada]:
    return [
        leitura(contas, nonce=i + 1, timestamp=i * periodo, **campos)
        for i, campos in enumerate(valores)
    ]


def alimentar(
    agregador: Agregador, leituras: list[LeituraAssinada]
) -> list[Checkpoint]:
    saida = [agregador.adicionar(item) for item in leituras]
    return [cp for cp in saida if cp is not None]


# --- eventos ----------------------------------------------------------------


def test_primeira_leitura_dispara(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    cp = agregador.adicionar(leitura(contas, 1, 0))
    assert cp is not None
    assert cp.disparo is Disparo.PRIMEIRA


def test_excursao_envia_pior_valor_e_nao_a_media(
    contas: dict[str, LocalAccount], faixa_larga: Agregador
) -> None:
    temperaturas = [210, 210, 420, 420, 420, 210]
    leituras = serie(contas, [{"temperatura": t} for t in temperaturas])
    cps = alimentar(faixa_larga, leituras)

    assert [cp.disparo for cp in cps] == [
        Disparo.PRIMEIRA,
        Disparo.FAIXA,
        Disparo.FAIXA,
    ]
    saida, retorno = cps[1], cps[2]
    assert saida.temperatura == 420
    assert saida.timestamp_leitura == leituras[2].leitura.timestamp
    # O retorno leva o valor de dentro da faixa: se levasse 42,0 °C, o
    # contrato veria a carga fora da faixa no instante em que ela voltou.
    assert retorno.temperatura == 210
    assert retorno.timestamp_leitura == leituras[5].leitura.timestamp


def test_pior_da_janela_fora_da_faixa(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    # Fica acima da faixa, com pico no meio; o batimento leva o pico.
    temperaturas = [50, 100, 150, 120, 110, 105, 100, 100]
    leituras = serie(contas, [{"temperatura": t} for t in temperaturas])
    cps = alimentar(agregador, leituras)
    assert [cp.disparo for cp in cps] == [
        Disparo.PRIMEIRA,
        Disparo.FAIXA,
        Disparo.BATIMENTO,
    ]
    assert cps[2].temperatura == 150


def test_pior_da_janela_dentro_da_faixa_e_o_mais_perto_do_limite(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    temperaturas = [50, 50, 50, 22, 50, 50, 50]
    leituras = serie(contas, [{"temperatura": t} for t in temperaturas])
    cps = alimentar(agregador, leituras)
    assert cps[-1].disparo is Disparo.BATIMENTO
    assert cps[-1].temperatura == 22


def test_umidade_pior_e_a_maior(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    umidades = [60, 60, 90, 60, 60, 60, 60]
    cps = alimentar(agregador, serie(contas, [{"umidade": u} for u in umidades]))
    assert cps[-1].disparo is Disparo.BATIMENTO
    assert cps[-1].umidade == 90


def test_cruzar_de_abaixo_para_acima_dispara(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    temperaturas = [50, 0, 120]
    leituras = serie(contas, [{"temperatura": t} for t in temperaturas])
    cps = alimentar(agregador, leituras)
    assert [cp.disparo for cp in cps] == [
        Disparo.PRIMEIRA,
        Disparo.FAIXA,
        Disparo.FAIXA,
    ]


def test_lacre_abrindo_dispara_na_hora(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    lacres = [False, False, True, True, False]
    leituras = serie(contas, [{"lacre_aberto": aberto} for aberto in lacres])
    cps = alimentar(agregador, leituras)

    assert [cp.disparo for cp in cps] == [
        Disparo.PRIMEIRA,
        Disparo.LACRE,
        Disparo.LACRE,
    ]
    assert cps[1].lacre_aberto is True
    assert cps[1].timestamp_leitura == leituras[2].leitura.timestamp
    assert cps[2].lacre_aberto is False


# --- batimento --------------------------------------------------------------


def test_leituras_normais_um_checkpoint_por_periodicidade(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    # 10 min com amostra a cada 10 s, como uma rodada do experimento.
    cps = alimentar(agregador, serie(contas, [{}] * 61))
    assert len(cps) == 1 + 600 // PERIODICIDADE
    instantes = [cp.timestamp_leitura for cp in cps]
    assert all(b - a == PERIODICIDADE for a, b in zip(instantes, instantes[1:]))


@pytest.mark.parametrize("periodo", [1, 7, 13, 25, 60])
def test_batimento_nunca_estoura_a_periodicidade(
    contas: dict[str, LocalAccount], agregador: Agregador, periodo: int
) -> None:
    cps = alimentar(agregador, serie(contas, [{}] * (600 // periodo + 1), periodo))
    instantes = [cp.timestamp_leitura for cp in cps]
    assert all(b - a <= PERIODICIDADE for a, b in zip(instantes, instantes[1:]))


def test_margem_antecipa_o_batimento(contas: dict[str, LocalAccount]) -> None:
    agregador = Agregador(TEMP_MIN, TEMP_MAX, PERIODICIDADE, margem_seg=10)
    cps = alimentar(agregador, serie(contas, [{}] * 13))
    assert [cp.timestamp_leitura for cp in cps] == [0, 50, 100]


def test_silencio_nao_gera_checkpoint_no_intervalo(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, agregador: Agregador
) -> None:
    disp = Dispositivo(
        contas["dispositivo"], SERVICO, relogio, 10, Base(temperatura=50, umidade=60)
    )
    validador = Validador({SERVICO: contas["dispositivo"].address}, relogio)
    inicio = relogio.agora()
    silencio_ini, silencio_fim = inicio + 120, inicio + 120 + 3 * PERIODICIDADE
    disp.silenciar(silencio_fim - silencio_ini, inicio=silencio_ini)

    cps = []
    for _ in range(40):
        emitida = disp.emitir()
        if emitida is not None and validador.validar(emitida) is None:
            cp = agregador.adicionar(emitida)
            if cp is not None:
                cps.append(cp)
        relogio.avancar(10)

    instantes = [cp.timestamp_leitura for cp in cps]
    assert not any(silencio_ini <= t < silencio_fim for t in instantes)
    # A lacuna chega ao contrato inteira, entre o último checkpoint antes do
    # silêncio e o primeiro depois dele.
    antes = max(t for t in instantes if t < silencio_ini)
    depois = min(t for t in instantes if t >= silencio_fim)
    assert depois == silencio_fim
    assert depois - antes > 3 * PERIODICIDADE
    assert cps[instantes.index(depois)].disparo is Disparo.BATIMENTO


# --- hash e auditoria -------------------------------------------------------


def test_hash_reproduzivel_a_partir_do_lote(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    leituras = serie(contas, [{}] * 13)
    cps = alimentar(agregador, leituras)

    # O lote é tudo o que chegou desde o checkpoint anterior, em ordem, e os
    # lotes juntos cobrem todas as leituras exatamente uma vez.
    assert [item for cp in cps for item in cp.lote] == leituras
    for cp in cps:
        esperado = keccak(b"".join(hash_leitura(item.leitura) for item in cp.lote))
        assert cp.hash_leitura == esperado == hash_lote(cp.lote)


def test_hash_muda_se_o_lote_muda(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    leituras = serie(contas, [{}] * 3)
    assert hash_lote(leituras) != hash_lote(leituras[::-1])
    assert hash_lote(leituras) != hash_lote(leituras[:2])


def test_lote_da_troca_de_faixa_inclui_leituras_antigas(
    contas: dict[str, LocalAccount], faixa_larga: Agregador
) -> None:
    leituras = serie(contas, [{"temperatura": t} for t in [210, 210, 420]])
    cps = alimentar(faixa_larga, leituras)
    assert cps[1].lote == tuple(leituras[1:])


def test_argumentos_na_ordem_de_registrar_checkpoint(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    cp = agregador.adicionar(leitura(contas, 1, 0, temperatura=33, umidade=70))
    assert cp is not None
    assert cp.argumentos() == (SERVICO, cp.hash_leitura, 33, 70, False, 0)


# --- fim da rodada ----------------------------------------------------------


def test_descarregar_ancora_o_fim_do_cenario_conforme(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    c = carregar(DIRETORIO_CENARIOS / "01_conforme.json")
    servico = c.servico
    agregador = Agregador(
        servico.temp_min, servico.temp_max, servico.periodicidade_seg
    )
    t0 = relogio.agora()
    for emissao in Executor(c, contas["dispositivo"], SERVICO, relogio):
        agregador.adicionar(emissao.leitura)
    assert agregador.checkpoints[-1].timestamp_leitura == t0 + 540

    final = agregador.descarregar()
    assert final is not None
    assert final.disparo is Disparo.FINAL
    assert final.timestamp_leitura == t0 + 590
    assert [item.leitura.timestamp - t0 for item in final.lote] == [
        550, 560, 570, 580, 590
    ]
    assert agregador.checkpoints[-1] is final


def test_descarregar_duas_vezes_a_segunda_e_none(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    alimentar(agregador, serie(contas, [{}] * 3))
    assert agregador.descarregar() is not None
    assert agregador.descarregar() is None


def test_descarregar_sem_leitura_e_none(agregador: Agregador) -> None:
    assert agregador.descarregar() is None


def test_hash_do_checkpoint_final_reproduzivel(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    leituras = serie(contas, [{}] * 9)
    cps = alimentar(agregador, leituras)
    final = agregador.descarregar()
    assert final is not None
    # Com o final, os lotes cobrem todas as leituras, sem sobra.
    assert [item for cp in [*cps, final] for item in cp.lote] == leituras
    assert final.hash_leitura == hash_lote(final.lote)


def test_checkpoint_final_leva_o_pior_da_janela(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    alimentar(agregador, serie(contas, [{"temperatura": t} for t in [50, 50, 30, 75, 50]]))
    final = agregador.descarregar()
    assert final is not None
    assert final.temperatura == 75


def test_modo_bruto_nao_tem_o_que_descarregar(contas: dict[str, LocalAccount]) -> None:
    agregador = Agregador(TEMP_MIN, TEMP_MAX, PERIODICIDADE, modo="bruto")
    alimentar(agregador, serie(contas, [{}] * 5))
    assert agregador.descarregar() is None


# --- modo bruto -------------------------------------------------------------


def test_modo_bruto_uma_leitura_um_checkpoint(contas: dict[str, LocalAccount]) -> None:
    agregador = Agregador(TEMP_MIN, TEMP_MAX, PERIODICIDADE, modo="bruto")
    assert agregador.modo is Modo.BRUTO
    leituras = serie(contas, [{"temperatura": t} for t in [50, 120, 50, 50, 50]])
    cps = alimentar(agregador, leituras)

    assert len(cps) == len(leituras)
    for cp, item in zip(cps, leituras):
        assert cp.disparo is Disparo.BRUTO
        assert cp.hash_leitura == hash_leitura(item.leitura)
        assert cp.temperatura == item.leitura.temperatura
        assert cp.lote == (item,)


# --- entradas inválidas -----------------------------------------------------


@pytest.mark.parametrize(
    "argumentos",
    [
        {"temp_min": 80, "temp_max": 20, "periodicidade_seg": 60},
        {"temp_min": 20, "temp_max": 80, "periodicidade_seg": 0},
        {"temp_min": 20, "temp_max": 80, "periodicidade_seg": 60, "margem_seg": -1},
        {"temp_min": 20, "temp_max": 80, "periodicidade_seg": 60, "modo": "media"},
    ],
)
def test_parametros_invalidos(argumentos: dict) -> None:
    with pytest.raises(ValueError):
        Agregador(**argumentos)


def test_rejeita_leitura_fora_de_ordem(
    contas: dict[str, LocalAccount], agregador: Agregador
) -> None:
    agregador.adicionar(leitura(contas, 1, 20))
    with pytest.raises(ValueError, match="fora de ordem"):
        agregador.adicionar(leitura(contas, 2, 10))


def test_hash_de_lote_vazio_falha() -> None:
    with pytest.raises(ValueError):
        hash_lote([])
