from pathlib import Path

import pytest

from oraculo.oraculo import _resumo_revert, carregar_abi


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


def test_resumo_revert_sem_motivo_mantem_a_mensagem() -> None:
    assert _resumo_revert("nonce too low") == "nonce too low"
