import pytest
from eth_account.signers.local import LocalAccount

from oraculo.assinatura import LeituraAssinada, signatario
from oraculo.relogio import RelogioVirtual
from oraculo.simulador import SALTO_IMPOSSIVEL, Base, Dispositivo

# Faixa física que o oráculo vai aplicar (T4), em décimos de grau e %.
TEMP_FISICA = (-400, 850)
UMIDADE_FISICA = (0, 100)

BASE = Base(temperatura=50, umidade=60)


@pytest.fixture
def t0(relogio: RelogioVirtual) -> int:
    return relogio.agora()


def novo_dispositivo(
    contas: dict[str, LocalAccount],
    relogio: RelogioVirtual,
    periodo: int = 1,
    semente: int = 42,
) -> Dispositivo:
    return Dispositivo(contas["dispositivo"], 1, relogio, periodo, BASE, semente)


@pytest.fixture
def disp(contas: dict[str, LocalAccount], relogio: RelogioVirtual) -> Dispositivo:
    return novo_dispositivo(contas, relogio)


# Conduz como a divisão 3 vai conduzir: um passo de 1 s, chamando `emitir` a
# cada passo. Cobre [agora, agora + segundos).
def rodar(
    disp: Dispositivo, relogio: RelogioVirtual, segundos: int
) -> list[LeituraAssinada]:
    leituras = []
    for _ in range(segundos):
        if (la := disp.emitir()) is not None:
            leituras.append(la)
        relogio.avancar(1)
    return leituras


def assinada_pelo_dispositivo(la: LeituraAssinada, disp: Dispositivo) -> bool:
    return signatario(la.leitura, la.assinatura) == disp.conta.address


def test_leituras_normais_assinadas_e_com_nonce_de_1_em_1(
    disp: Dispositivo, relogio: RelogioVirtual
) -> None:
    leituras = rodar(disp, relogio, 20)
    assert [la.leitura.nonce for la in leituras] == list(range(1, 21))
    assert all(assinada_pelo_dispositivo(la, disp) for la in leituras)
    assert all(la.leitura.servico_id == 1 for la in leituras)


def test_leitura_normal_fica_perto_da_base(
    disp: Dispositivo, relogio: RelogioVirtual
) -> None:
    for la in rodar(disp, relogio, 50):
        assert abs(la.leitura.temperatura - BASE.temperatura) <= BASE.ruido_temperatura
        assert abs(la.leitura.umidade - BASE.umidade) <= BASE.ruido_umidade
        assert la.leitura.lacre_aberto is False


def test_respeita_o_periodo_de_amostra_mesmo_com_passo_menor(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, t0: int
) -> None:
    disp = novo_dispositivo(contas, relogio, periodo=10)
    leituras = rodar(disp, relogio, 100)
    assert [la.leitura.timestamp for la in leituras] == list(range(t0, t0 + 100, 10))


def test_timestamp_vem_do_relogio_virtual(
    disp: Dispositivo, relogio: RelogioVirtual
) -> None:
    relogio.avancar(1234)
    la = disp.emitir()
    assert la is not None and la.leitura.timestamp == relogio.agora()


def test_mesma_semente_reproduz_leituras_e_assinaturas(
    contas: dict[str, LocalAccount],
) -> None:
    def sequencia(semente: int) -> list[LeituraAssinada]:
        relogio = RelogioVirtual()
        disp = novo_dispositivo(contas, relogio, semente=semente)
        disp.excursao_temperatura(300, duracao=5, inicio=relogio.agora() + 10)
        return rodar(disp, relogio, 30)

    assert sequencia(7) == sequencia(7)
    assert sequencia(7) != sequencia(8)


def test_excursao_dura_exatamente_o_tempo_pedido(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.excursao_temperatura(300, duracao=30, inicio=t0 + 10)
    leituras = rodar(disp, relogio, 60)
    em_excursao = [
        la.leitura.timestamp
        for la in leituras
        if abs(la.leitura.temperatura - 300) <= BASE.ruido_temperatura
    ]
    assert em_excursao == list(range(t0 + 10, t0 + 40))
    # Fora da excursão, de volta à base.
    assert all(
        abs(la.leitura.temperatura - BASE.temperatura) <= BASE.ruido_temperatura
        for la in leituras
        if la.leitura.timestamp not in em_excursao
    )


def test_excursao_sem_inicio_comeca_agora(
    disp: Dispositivo, relogio: RelogioVirtual
) -> None:
    disp.excursao_temperatura(-250, duracao=3)
    temperaturas = [la.leitura.temperatura for la in rodar(disp, relogio, 4)]
    assert all(abs(t + 250) <= BASE.ruido_temperatura for t in temperaturas[:3])
    assert abs(temperaturas[3] - BASE.temperatura) <= BASE.ruido_temperatura


def test_lacre_aberto_dura_exatamente_o_tempo_pedido(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.abrir_lacre(duracao=20, inicio=t0 + 5)
    leituras = rodar(disp, relogio, 40)
    abertos = [la.leitura.timestamp for la in leituras if la.leitura.lacre_aberto]
    assert abertos == list(range(t0 + 5, t0 + 25))


def test_excursao_e_lacre_podem_coincidir(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.excursao_temperatura(300, duracao=10, inicio=t0)
    disp.abrir_lacre(duracao=10, inicio=t0)
    for la in rodar(disp, relogio, 10):
        assert la.leitura.lacre_aberto
        assert abs(la.leitura.temperatura - 300) <= BASE.ruido_temperatura


def test_silencio_nao_emite_nada_no_intervalo(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.silenciar(duracao=20, inicio=t0 + 5)
    leituras = rodar(disp, relogio, 40)
    timestamps = [la.leitura.timestamp for la in leituras]
    assert not any(t0 + 5 <= t < t0 + 25 for t in timestamps)
    assert timestamps == list(range(t0, t0 + 5)) + list(range(t0 + 25, t0 + 40))
    # Aparelho desligado não gasta nonce: a sequência continua sem buraco.
    assert [la.leitura.nonce for la in leituras] == list(range(1, 21))


@pytest.mark.parametrize(
    "agendar",
    [
        lambda d, t: d.excursao_temperatura(300, duracao=10, inicio=t + 5),
        lambda d, t: d.abrir_lacre(duracao=10, inicio=t + 5),
        lambda d, t: d.silenciar(duracao=10, inicio=t + 5),
    ],
)
def test_intervalos_do_mesmo_tipo_nao_se_sobrepoem(
    disp: Dispositivo, t0: int, agendar
) -> None:
    agendar(disp, t0)
    with pytest.raises(ValueError, match="sobrepõe"):
        agendar(disp, t0 + 9)


def test_intervalos_encostados_sao_aceitos(disp: Dispositivo, t0: int) -> None:
    disp.excursao_temperatura(300, duracao=10, inicio=t0)
    disp.excursao_temperatura(400, duracao=10, inicio=t0 + 10)


@pytest.mark.parametrize("duracao", [0, -5])
def test_duracao_nao_positiva_rejeitada(disp: Dispositivo, duracao: int) -> None:
    with pytest.raises(ValueError, match="duração"):
        disp.silenciar(duracao=duracao)


def test_periodo_de_amostra_invalido(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    with pytest.raises(ValueError):
        novo_dispositivo(contas, relogio, periodo=0)


# --- maliciosos -------------------------------------------------------------


def test_assinatura_de_outra_chave_nao_recupera_o_dispositivo(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.assinatura_invalida("outra_chave", inicio=t0 + 3)
    leituras = rodar(disp, relogio, 6)
    forjada = leituras[3]
    assert not assinada_pelo_dispositivo(forjada, disp)
    # A forjada carrega o endereço do dispositivo, senão nem seria ataque.
    assert forjada.leitura.dispositivo == disp.conta.address
    # O dispositivo não emitiu a forjada, então o nonce dela volta a ser usado
    # pela próxima leitura legítima.
    assert [la.leitura.nonce for la in leituras] == [1, 2, 3, 4, 4, 5]
    legitimas = leituras[:3] + leituras[4:]
    assert all(assinada_pelo_dispositivo(la, disp) for la in legitimas)


def test_assinatura_adulterada_em_transito_nao_recupera_o_dispositivo(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.assinatura_invalida("adulterada", inicio=t0 + 2)
    leituras = rodar(disp, relogio, 4)
    adulterada = leituras[2]
    assert not assinada_pelo_dispositivo(adulterada, disp)
    # Sem abertura agendada, a adulteração forja um lacre aberto.
    assert adulterada.leitura.lacre_aberto is True
    assert [la.leitura.nonce for la in leituras] == [1, 2, 3, 4]


def test_nonce_repetido_reenvia_byte_a_byte(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.nonce_repetido(inicio=t0 + 3)
    leituras = rodar(disp, relogio, 5)
    assert leituras[3] == leituras[2]
    # A assinatura continua válida: só o nonce denuncia o replay.
    assert assinada_pelo_dispositivo(leituras[3], disp)
    assert leituras[4].leitura.nonce == leituras[2].leitura.nonce + 1


def test_nonce_repetido_sem_leitura_anterior(disp: Dispositivo) -> None:
    disp.nonce_repetido()
    with pytest.raises(RuntimeError):
        disp.emitir()


@pytest.mark.parametrize("modo", ["temperatura", "umidade"])
def test_valor_impossivel_fora_da_faixa_fisica_mas_bem_assinado(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int, modo: str
) -> None:
    disp.valor_impossivel(modo, inicio=t0 + 1)
    impossivel = rodar(disp, relogio, 2)[1]
    assert assinada_pelo_dispositivo(impossivel, disp)
    leitura = impossivel.leitura
    fora_temp = not TEMP_FISICA[0] <= leitura.temperatura <= TEMP_FISICA[1]
    fora_umidade = not UMIDADE_FISICA[0] <= leitura.umidade <= UMIDADE_FISICA[1]
    assert fora_temp if modo == "temperatura" else fora_umidade


@pytest.mark.parametrize("base_temp", [-400, -180, 0, 1, 50, 850])
def test_salto_impossivel_cabe_na_faixa_mas_nao_na_variacao(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, base_temp: int
) -> None:
    base = Base(temperatura=base_temp, umidade=60, ruido_temperatura=0)
    disp = Dispositivo(contas["dispositivo"], 1, relogio, 1, base)
    disp.valor_impossivel("salto", inicio=relogio.agora() + 1)
    anterior, salto = rodar(disp, relogio, 2)
    assert assinada_pelo_dispositivo(salto, disp)
    assert TEMP_FISICA[0] <= salto.leitura.temperatura <= TEMP_FISICA[1]
    variacao = abs(salto.leitura.temperatura - anterior.leitura.temperatura)
    assert variacao == SALTO_IMPOSSIVEL


def test_quantidade_conta_amostras_maliciosas(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.valor_impossivel("temperatura", inicio=t0 + 2, quantidade=3)
    temperaturas = [la.leitura.temperatura for la in rodar(disp, relogio, 8)]
    impossiveis = [i for i, t in enumerate(temperaturas) if t > TEMP_FISICA[1]]
    assert impossiveis == [2, 3, 4]


def test_injecao_durante_silencio_espera_o_aparelho_voltar(
    disp: Dispositivo, relogio: RelogioVirtual, t0: int
) -> None:
    disp.silenciar(duracao=10, inicio=t0)
    disp.valor_impossivel("temperatura", inicio=t0 + 2)
    leituras = rodar(disp, relogio, 12)
    assert [la.leitura.timestamp for la in leituras] == [t0 + 10, t0 + 11]
    assert leituras[0].leitura.temperatura > TEMP_FISICA[1]


@pytest.mark.parametrize(
    "comando",
    [
        lambda d: d.assinatura_invalida("chave_errada"),
        lambda d: d.valor_impossivel("pressao"),
        lambda d: d.nonce_repetido(quantidade=0),
    ],
)
def test_comando_malicioso_invalido(disp: Dispositivo, comando) -> None:
    with pytest.raises(ValueError):
        comando(disp)
