import pytest

from oraculo.relogio import INICIO_PADRAO, RelogioVirtual, sincronizar_no


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


class _NoFalso:
    def __init__(self, ultimo_timestamp: int) -> None:
        self.ultimo_timestamp = ultimo_timestamp
        self.chamadas: list[tuple[str, list]] = []
        self.eth = self
        self.provider = self

    def get_block(self, _: str) -> dict:
        return {"timestamp": self.ultimo_timestamp}

    def make_request(self, metodo: str, parametros: list) -> None:
        self.chamadas.append((metodo, parametros))


def test_sincronizar_no_adianta_o_proximo_bloco(relogio: RelogioVirtual) -> None:
    no = _NoFalso(relogio.agora() - 1)
    assert sincronizar_no(no, relogio) is True
    assert no.chamadas == [("evm_setNextBlockTimestamp", [relogio.agora()])]


def test_sincronizar_no_nunca_atrasa(relogio: RelogioVirtual) -> None:
    no = _NoFalso(relogio.agora())
    assert sincronizar_no(no, relogio) is False
    assert no.chamadas == []
