import copy
import json
from pathlib import Path

import pytest
from eth_account.signers.local import LocalAccount

from oraculo.agregacao import Agregador, Modo
from oraculo.cenario import (
    DIRETORIO_CENARIOS,
    Cenario,
    ErroCenario,
    Esperado,
    Janela,
    carregar,
    de_dict,
)
from oraculo.executor import Emissao, Executor
from oraculo.log_rejeicoes import LogRejeicoes
from oraculo.oraculo import Motivo, Validador
from oraculo.relogio import RelogioVirtual

SERVICO = 1
ARQUIVOS = sorted(DIRETORIO_CENARIOS.glob("*.json"))

MINIMO = {
    "nome": "minimo",
    "duracao_seg": 600,
    "periodo_amostra_seg": 10,
    "servico": {"tempMin": 20, "tempMax": 80, "periodicidadeSeg": 60},
    "base": {"temperatura": 50, "umidade": 60},
    "semente": 42,
    "eventos": [],
}


def com_eventos(*eventos: dict, **campos) -> dict:
    dados = copy.deepcopy(MINIMO)
    dados["eventos"] = list(eventos)
    dados.update(campos)
    return dados


def cenario(nome: str) -> Cenario:
    return carregar(DIRETORIO_CENARIOS / f"{nome}.json")


def executar(
    c: Cenario, contas: dict[str, LocalAccount], relogio: RelogioVirtual, **kw
) -> list[Emissao]:
    return list(Executor(c, contas["dispositivo"], SERVICO, relogio, **kw))


# Valida a cada emissão, com o relógio parado nela, como o runner vai fazer;
# validar depois de percorrer tudo daria timestamp desatualizado.
def rejeicoes(
    c: Cenario, contas: dict[str, LocalAccount], relogio: RelogioVirtual,
    log: LogRejeicoes | None = None, agregador: Agregador | None = None,
) -> list[tuple[tuple[int, ...], Motivo]]:
    validador = Validador({SERVICO: contas["dispositivo"].address}, relogio, log=log)
    rejeitadas = []
    for e in Executor(c, contas["dispositivo"], SERVICO, relogio):
        motivo = validador.validar(e.leitura)
        if motivo is not None:
            rejeitadas.append((e.eventos, motivo))
        elif agregador is not None:
            agregador.adicionar(e.leitura)
    return rejeitadas


# --- arquivos entregues -----------------------------------------------------


def test_ha_os_oito_cenarios() -> None:
    assert [p.stem[:2] for p in ARQUIVOS] == [f"{i:02}" for i in range(1, 9)]


@pytest.mark.parametrize("caminho", ARQUIVOS, ids=lambda p: p.stem)
def test_todo_cenario_carrega_e_valida(caminho: Path) -> None:
    c = carregar(caminho)
    assert c.nome == caminho.stem
    assert list(c.variantes())


@pytest.mark.parametrize("caminho", ARQUIVOS, ids=lambda p: p.stem)
def test_todo_cenario_executa_e_passa_no_oraculo_salvo_os_maliciosos(
    caminho: Path, contas: dict[str, LocalAccount]
) -> None:
    for variante in carregar(caminho).variantes():
        rejeitadas = rejeicoes(variante, contas, RelogioVirtual())
        esperadas = [((i,), m) for i, m in variante.rejeicoes_esperadas()]
        assert rejeitadas == esperadas


def test_conforme_gera_duracao_sobre_periodo_leituras(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    c = cenario("01_conforme")
    t0 = relogio.agora()
    emissoes = executar(c, contas, relogio)
    assert len(emissoes) == c.duracao_seg // c.periodo_amostra_seg
    assert [e.leitura.leitura.nonce for e in emissoes] == list(range(1, len(emissoes) + 1))
    assert [e.segundo for e in emissoes] == list(range(0, c.duracao_seg, c.periodo_amostra_seg))
    assert all(e.instante == t0 + e.segundo == e.leitura.leitura.timestamp for e in emissoes)
    assert all(e.eventos == () for e in emissoes)
    assert relogio.agora() == t0 + c.duracao_seg


def test_rejeicoes_gera_exatamente_os_motivos_esperados(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual, tmp_path: Path
) -> None:
    c = cenario("07_rejeicoes")
    caminho_log = tmp_path / "rejeicoes.csv"
    log = LogRejeicoes(caminho_log, c.nome, 1)
    motivos = [motivo for _, motivo in rejeicoes(c, contas, relogio, log)]
    assert motivos == [
        Motivo.ASSINATURA_INVALIDA,
        Motivo.ASSINATURA_INVALIDA,
        Motivo.NONCE_REPETIDO,
        Motivo.NONCE_REPETIDO,
        Motivo.VALOR_IMPOSSIVEL,
        Motivo.VALOR_IMPOSSIVEL,
        Motivo.VARIACAO_IMPOSSIVEL,
    ]
    linhas = caminho_log.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1 + len(motivos)


# A agregação real decide os rótulos de silêncio e conformidade; estes testes
# conferem que o roteiro produz, nos checkpoints, a lacuna que o rótulo promete.
def lacunas(
    c: Cenario, contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> list[tuple[int, int]]:
    t0 = relogio.agora()
    agregador = Agregador(
        c.servico.temp_min, c.servico.temp_max, c.servico.periodicidade_seg, c.modo_agregacao
    )
    rejeicoes(c, contas, relogio, agregador=agregador)
    instantes = [cp.timestamp_leitura - t0 for cp in agregador.checkpoints]
    return [
        (a, b)
        for a, b in zip(instantes, instantes[1:])
        if b - a > c.servico.periodicidade_seg
    ]


@pytest.mark.parametrize(
    "nome", ["01_conforme", "02_excursao_crescente", "03_lacre_dentro_janela", "07_rejeicoes"]
)
def test_cenario_sem_silencio_nunca_estoura_a_periodicidade(
    nome: str, contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    assert lacunas(cenario(nome), contas, relogio) == []


def test_cada_silencio_vira_exatamente_uma_lacuna(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    c = cenario("05_indisponibilidade_crescente")
    encontradas = lacunas(c, contas, relogio)
    assert len(encontradas) == len(c.eventos)
    for (antes, depois), silencio in zip(encontradas, c.eventos):
        assert antes < silencio.inicio and silencio.inicio + silencio.duracao <= depois


def test_eventos_marcam_as_leituras_que_produziram(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    c = cenario("08_combinado")
    emissoes = executar(c, contas, relogio)
    excursao, lacre = c.eventos
    assert [e.segundo for e in emissoes if e.eventos == (0,)] == list(
        range(excursao.inicio, excursao.inicio + excursao.duracao, c.periodo_amostra_seg)
    )
    marcadas_lacre = [e for e in emissoes if e.eventos == (1,)]
    assert marcadas_lacre and all(e.leitura.leitura.lacre_aberto for e in marcadas_lacre)
    assert all(e.leitura.leitura.temperatura > c.servico.temp_max for e in emissoes if e.eventos == (0,))


def test_janela_do_executor_em_instante_absoluto(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    relogio.avancar(1000)
    t0 = relogio.agora()
    executor = Executor(cenario("03_lacre_dentro_janela"), contas["dispositivo"], SERVICO, relogio)
    assert executor.janela_lacre() == Janela(t0 + 280, t0 + 360)
    sem = Executor(cenario("01_conforme"), contas["dispositivo"], SERVICO, relogio)
    assert sem.janela_lacre() is None


def test_variantes_do_volume_cobrem_periodos_e_modos() -> None:
    variantes = list(cenario("06_volume_telemetria").variantes())
    combinacoes = {(v.periodo_amostra_seg, v.modo_agregacao) for v in variantes}
    assert combinacoes == {(p, m) for p in (1, 2, 5, 10, 30) for m in Modo}
    assert all(v.variante and not v.varrer for v in variantes)


def test_executor_recusa_cenario_com_varredura(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    with pytest.raises(ValueError, match="variantes"):
        Executor(cenario("06_volume_telemetria"), contas["dispositivo"], SERVICO, relogio)


def test_bruto_e_agregado_veem_as_mesmas_leituras(contas: dict[str, LocalAccount]) -> None:
    variantes = [
        v for v in cenario("06_volume_telemetria").variantes() if v.periodo_amostra_seg == 5
    ]
    leituras = [[e.leitura for e in executar(v, contas, RelogioVirtual())] for v in variantes]
    assert leituras[0] == leituras[1]


def test_semente_reproduz_e_semente_diferente_muda(contas: dict[str, LocalAccount]) -> None:
    c = cenario("02_excursao_crescente")

    def leituras(**kw):
        return [e.leitura for e in executar(c, contas, RelogioVirtual(), **kw)]

    assert leituras() == leituras()
    assert leituras(semente=c.semente + 1) != leituras()


def test_executor_nao_percorre_duas_vezes(
    contas: dict[str, LocalAccount], relogio: RelogioVirtual
) -> None:
    executor = Executor(cenario("01_conforme"), contas["dispositivo"], SERVICO, relogio)
    list(executor)
    with pytest.raises(RuntimeError):
        list(executor)


# --- validação ---------------------------------------------------------------

EXCURSAO = {"inicio": 60, "tipo": "excursao_temperatura", "duracao": 30, "temperatura": 120, "esperado": "desvio_temperatura"}
LACRE = {"inicio": 300, "tipo": "abrir_lacre", "duracao": 30, "esperado": "violacao_lacre"}
SILENCIO = {"inicio": 100, "tipo": "silencio", "duracao": 60, "esperado": "nao_observado"}
NONCE = {"inicio": 200, "tipo": "nonce_repetido", "esperado": "rejeitado_oraculo", "motivo_esperado": "nonce_repetido"}


def test_minimo_valida() -> None:
    assert de_dict(MINIMO).eventos == ()


@pytest.mark.parametrize(
    "dados, mensagem",
    [
        (com_eventos({**EXCURSAO, "tipo": "terremoto"}), r"eventos\[0\]\.tipo: 'terremoto' desconhecido"),
        (com_eventos({**EXCURSAO, "duracao": 600}), r"eventos\[0\]: termina em 660 s, depois do fim"),
        (com_eventos({**NONCE, "inicio": 600}), r"não cabem antes do fim"),
        (com_eventos({**EXCURSAO, "temperaturaa": 1}), r"campos desconhecidos: temperaturaa"),
        (com_eventos({k: v for k, v in EXCURSAO.items() if k != "temperatura"}), r"ausentes: temperatura"),
        (com_eventos({**EXCURSAO, "duracao": True}), r"duracao: esperado inteiro"),
        (com_eventos({**EXCURSAO, "duracao": 0}), r"duracao: deve ser ≥ 1"),
        (com_eventos(EXCURSAO, {**EXCURSAO, "inicio": 80}), r"eventos\[1\]: excursao_temperatura em \[80, 110\) sobrepõe"),
        (com_eventos(EXCURSAO, {**SILENCIO, "inicio": 70}), r"silencio em \[70, 130\) sobrepõe"),
        (com_eventos({**SILENCIO, "inicio": 190}, NONCE), r"nonce_repetido em \[200, 210\) sobrepõe silencio"),
        (com_eventos({**EXCURSAO, "esperado": "conforme"}), r"produz desvio_temperatura, não conforme"),
        (com_eventos({**EXCURSAO, "temperatura": 70, "esperado": "desvio_temperatura"}), r"produz conforme"),
        (com_eventos({**EXCURSAO, "temperatura": 81}), r"cruza o limite da faixa"),
        (com_eventos({**EXCURSAO, "temperatura": 900}), r"fora da faixa física"),
        (com_eventos({**EXCURSAO, "temperatura": 300}), r"excede a variação admitida"),
        (com_eventos({**EXCURSAO, "inicio": 61, "duracao": 5}), r"não cobre nenhuma amostra"),
        (com_eventos({**LACRE, "janela": {"inicio": 290, "fim": 310}}), r"só em parte dentro da janela"),
        (com_eventos({**LACRE, "janela": {"inicio": 280, "fim": 340}}), r"produz conforme, não violacao_lacre"),
        (com_eventos({**LACRE, "janela": {"inicio": 340, "fim": 340}}), r"inicio deve ser menor que fim"),
        (
            com_eventos(
                {**LACRE, "janela": {"inicio": 280, "fim": 340}, "esperado": "conforme"},
                {**LACRE, "inicio": 400, "janela": {"inicio": 390, "fim": 440}, "esperado": "conforme"},
            ),
            r"uma janela de lacre por serviço",
        ),
        (com_eventos(LACRE, {**EXCURSAO, "inicio": 400}), r"começa depois da violação de lacre"),
        (com_eventos({**SILENCIO, "esperado": "conforme"}), r"produz nao_observado, não conforme"),
        (com_eventos({**SILENCIO, "duracao": 20, "esperado": "desvio_temperatura"}), r"só pode ser conforme ou nao_observado"),
        (com_eventos({**NONCE, "inicio": 0}), r"precisa de uma leitura legítima antes"),
        (com_eventos({**NONCE, "motivo_esperado": "assinatura_invalida"}), r"rejeita por nonce_repetido"),
        (com_eventos({**NONCE, "tipo": "valor_impossivel", "modo": "salto", "motivo_esperado": "valor_impossivel"}), r"rejeita por variacao_impossivel"),
        (com_eventos({**NONCE, "tipo": "valor_impossivel", "modo": "salto", "motivo_esperado": "variacao_impossivel"}, periodo_amostra_seg=150, servico={"tempMin": 20, "tempMax": 80, "periodicidadeSeg": 300}), r"cabe na variação admitida"),
        (com_eventos({**NONCE, "tipo": "assinatura_invalida", "modo": "carimbo"}), r"modo: 'carimbo' desconhecido"),
        (com_eventos(periodo_amostra_seg=90), r"maior que a periodicidade"),
        (com_eventos(base={"temperatura": 79, "umidade": 60}), r"base com ruído precisa ficar dentro"),
        (com_eventos(base={"temperatura": 50, "umidade": 101}), r"base\.umidade: fora de 0–100"),
        (com_eventos(servico={"tempMin": 80, "tempMax": 20, "periodicidadeSeg": 60}), r"tempMin maior que tempMax"),
        (com_eventos(varrer={"semente": [1, 2]}), r"varrer: campos desconhecidos: semente"),
        (com_eventos(varrer={"periodo_amostra_seg": [10, 10]}), r"valores repetidos"),
        (com_eventos(varrer={"periodo_amostra_seg": [10, 90]}), r"\{'periodo_amostra_seg': 90\}: periodo_amostra_seg"),
    ],
)
def test_roteiro_invalido_rejeitado_com_mensagem_clara(dados: dict, mensagem: str) -> None:
    with pytest.raises(ErroCenario, match=mensagem):
        de_dict(dados, origem="teste.json")


def test_mensagem_traz_o_nome_do_arquivo(tmp_path: Path) -> None:
    caminho = tmp_path / "quebrado.json"
    caminho.write_text(json.dumps(com_eventos({**EXCURSAO, "tipo": "x"})), encoding="utf-8")
    with pytest.raises(ErroCenario, match=r"^quebrado\.json: eventos\[0\]\.tipo"):
        carregar(caminho)
    caminho.write_text("{ nao e json", encoding="utf-8")
    with pytest.raises(ErroCenario, match=r"quebrado\.json: JSON inválido"):
        carregar(caminho)


def test_excursao_com_lacre_aberto_pode_coincidir() -> None:
    c = de_dict(com_eventos(EXCURSAO, {**LACRE, "inicio": 60}))
    assert [e.esperado for e in c.eventos] == [Esperado.DESVIO_TEMPERATURA, Esperado.VIOLACAO_LACRE]


def test_lacre_sem_janela_e_lacre_longe_da_janela_sao_violacao() -> None:
    assert de_dict(com_eventos(LACRE)).janela_lacre is None
    c = de_dict(com_eventos({**LACRE, "janela": {"inicio": 100, "fim": 160}}))
    assert c.janela_lacre == Janela(100, 160)


def test_silencio_curto_aceita_os_dois_rotulos() -> None:
    for rotulo in ("conforme", "nao_observado"):
        de_dict(com_eventos({**SILENCIO, "duracao": 20, "esperado": rotulo}))
