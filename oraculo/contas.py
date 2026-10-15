"""Contas de teste do Hardhat, por índice e por papel.

As chaves vêm só do mnemônico público do Hardhat, que é determinístico: o
mesmo índice dá o mesmo endereço em qualquer máquina, e nenhuma chave real
precisa existir no repositório, em argumento de CLI ou em `.env`.
"""

from functools import cache

from eth_account import Account
from eth_account.signers.local import LocalAccount

MNEMONICO_HARDHAT = "test test test test test test test test test test test junk"
CAMINHO_DERIVACAO = "m/44'/60'/0'/0/{indice}"
CHAIN_ID_LOCAL = 31337

# O índice define o papel, igual ao deploy da divisão 1. O dispositivo só
# assina leituras; se ele aparecer como remetente de transação, é bug.
PAPEIS: dict[str, int] = {
    "dono": 0,
    "contratante": 1,
    "transportador": 2,
    "recebedor": 3,
    "oraculo": 4,
    "dispositivo": 5,
}

Account.enable_unaudited_hdwallet_features()


@cache
def conta(indice: int) -> LocalAccount:
    if indice < 0:
        raise ValueError(f"índice negativo: {indice}")
    return Account.from_mnemonic(
        MNEMONICO_HARDHAT, account_path=CAMINHO_DERIVACAO.format(indice=indice)
    )


def conta_do_papel(papel: str) -> LocalAccount:
    try:
        return conta(PAPEIS[papel])
    except KeyError:
        raise ValueError(
            f"papel desconhecido: {papel!r}; esperados: {', '.join(PAPEIS)}"
        ) from None
