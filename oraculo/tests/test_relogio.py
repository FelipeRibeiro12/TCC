import pytest

from oraculo.relogio import INICIO_PADRAO, RelogioVirtual


def test_comeca_no_inicio_padrao(relogio: RelogioVirtual) -> None:
    assert relogio.agora() == INICIO_PADRAO


def test_avancar_soma_e_devolve_o_novo_instante(relogio: RelogioVirtual) -> None:
    assert relogio.avancar(10) == INICIO_PADRAO + 10
    relogio.avancar(0)
    assert relogio.agora() == INICIO_PADRAO + 10


def test_nao_volta_no_tempo(relogio: RelogioVirtual) -> None:
    with pytest.raises(ValueError):
        relogio.avancar(-1)
    assert relogio.agora() == INICIO_PADRAO


def test_inicio_personalizado() -> None:
    assert RelogioVirtual(inicio=1000).agora() == 1000


def test_inicio_negativo_rejeitado() -> None:
    with pytest.raises(ValueError):
        RelogioVirtual(inicio=-1)
