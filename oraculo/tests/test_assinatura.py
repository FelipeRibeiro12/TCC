from dataclasses import replace

import pytest
from eth_account.signers.local import LocalAccount
from eth_utils import keccak

from oraculo.assinatura import (
    Leitura,
    assinar,
    celsius_para_decimos,
    codificar,
    hash_leitura,
    signatario,
)


@pytest.fixture
def dispositivo(contas: dict[str, LocalAccount]) -> LocalAccount:
    return contas["dispositivo"]


@pytest.fixture
def leitura(dispositivo: LocalAccount) -> Leitura:
    return Leitura(
        dispositivo=dispositivo.address,
        servico_id=1,
        nonce=7,
        timestamp=1_800_000_000,
        temperatura=-180,
        umidade=60,
        lacre_aberto=False,
    )


def test_assinar_e_recuperar_devolve_o_dispositivo(
    leitura: Leitura, dispositivo: LocalAccount
) -> None:
    assert signatario(leitura, assinar(leitura, dispositivo)) == dispositivo.address


# Um valor diferente por campo, cada um ainda codificável, para isolar que a
# adulteração de qualquer campo é percebida.
ADULTERACOES = {
    "dispositivo": "0x0000000000000000000000000000000000000001",
    "servico_id": 2,
    "nonce": 8,
    "timestamp": 1_800_000_001,
    "temperatura": -179,
    "umidade": 61,
    "lacre_aberto": True,
}


@pytest.mark.parametrize("campo, valor", ADULTERACOES.items())
def test_alterar_campo_depois_de_assinar_muda_o_signatario(
    leitura: Leitura, dispositivo: LocalAccount, campo: str, valor: object
) -> None:
    assinatura = assinar(leitura, dispositivo)
    adulterada = replace(leitura, **{campo: valor})
    assert signatario(adulterada, assinatura) != dispositivo.address


def test_assinatura_de_outra_chave_nao_recupera_o_dispositivo(
    leitura: Leitura, dispositivo: LocalAccount, contas: dict[str, LocalAccount]
) -> None:
    assinatura = assinar(leitura, contas["oraculo"])
    assert signatario(leitura, assinatura) != dispositivo.address


def test_assinatura_malformada_vira_value_error(leitura: Leitura) -> None:
    with pytest.raises(ValueError, match="malformada"):
        signatario(leitura, b"\x01" * 10)


def test_servico_diferente_gera_hash_diferente(leitura: Leitura) -> None:
    assert hash_leitura(leitura) != hash_leitura(replace(leitura, servico_id=2))


def _palavra(valor: int) -> bytes:
    # Uma palavra ABI: 32 bytes big-endian, inteiros com sinal em complemento
    # de dois estendido para 256 bits.
    return (valor % 2**256).to_bytes(32, "big")


def test_hash_bate_com_abi_encode_montado_a_mao(leitura: Leitura) -> None:
    # Monta o `abi.encode` do Solidity palavra por palavra, sem eth_abi, para
    # que o teste não confie na mesma biblioteca que está testando. A
    # temperatura negativa exercita a extensão de sinal do int16.
    esperado = b"".join(
        [
            _palavra(int(leitura.dispositivo, 16)),
            _palavra(leitura.servico_id),
            _palavra(leitura.nonce),
            _palavra(leitura.timestamp),
            _palavra(leitura.temperatura),
            _palavra(leitura.umidade),
            _palavra(int(leitura.lacre_aberto)),
        ]
    )
    assert codificar(leitura) == esperado
    assert hash_leitura(leitura) == keccak(esperado)


@pytest.mark.parametrize(
    "campo, valor",
    [
        ("temperatura", 21.5),  # float nunca entra na mensagem assinada
        ("temperatura", 2**15),  # fora do int16
        ("umidade", 256),  # fora do uint8
        ("umidade", -1),
        ("nonce", -1),
        ("lacre_aberto", 1),  # int no lugar de bool
    ],
)
def test_valor_fora_do_tipo_nao_codifica(
    leitura: Leitura, campo: str, valor: object
) -> None:
    with pytest.raises(ValueError, match="não codificável"):
        codificar(replace(leitura, **{campo: valor}))


@pytest.mark.parametrize(
    "celsius, decimos",
    [
        (21.0, 210),
        (-18.0, -180),
        (2.25, 23),  # meio arredonda para longe do zero
        (-2.25, -23),
        (2.24, 22),
        (0.05, 1),
        (3276.7, 32767),
        (-3276.8, -32768),
    ],
)
def test_celsius_para_decimos(celsius: float, decimos: int) -> None:
    assert celsius_para_decimos(celsius) == decimos


@pytest.mark.parametrize("celsius", [3276.8, -3276.9, float("nan"), float("inf")])
def test_celsius_fora_do_int16_ou_nao_finito(celsius: float) -> None:
    with pytest.raises(ValueError):
        celsius_para_decimos(celsius)
