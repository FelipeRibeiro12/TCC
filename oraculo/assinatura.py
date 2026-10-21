"""Leitura do dispositivo e sua assinatura na origem.

O hash precisa ser exatamente o `keccak256(abi.encode(...))` que o contrato
calcularia, porque é ele que vai para `registrarCheckpoint` como
`hashLeitura`. Por isso a mensagem é ABI-encoded com tipos fixos, e não JSON.
"""

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from eth_abi import encode
from eth_abi.exceptions import EncodingError
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from eth_keys.exceptions import BadSignature
from eth_utils import keccak
from eth_utils.exceptions import ValidationError

# A ordem e os tipos são o contrato entre dispositivo e oráculo; mudar qualquer
# um muda todos os hashes. `dispositivo` e `servicoId` entram na mensagem para
# que uma leitura de um serviço não possa ser reapresentada em outro.
TIPOS_LEITURA = (
    "address",  # dispositivo
    "uint256",  # servicoId
    "uint64",  # nonce
    "uint64",  # timestamp
    "int16",  # temperatura, décimos de grau
    "uint8",  # umidade, %
    "bool",  # lacreAberto
)

INT16_MIN = -(2**15)
INT16_MAX = 2**15 - 1


# Sem rótulo de evento: o dispositivo só mede. Se a leitura é desvio ou
# violação, quem decide é o contrato, a partir da faixa do serviço.
@dataclass(frozen=True)
class Leitura:
    dispositivo: str
    servico_id: int
    nonce: int
    timestamp: int
    temperatura: int
    umidade: int
    lacre_aberto: bool

    def valores(self) -> tuple:
        return (
            self.dispositivo,
            self.servico_id,
            self.nonce,
            self.timestamp,
            self.temperatura,
            self.umidade,
            self.lacre_aberto,
        )


@dataclass(frozen=True)
class LeituraAssinada:
    leitura: Leitura
    assinatura: bytes


def codificar(leitura: Leitura) -> bytes:
    # O eth_abi já recusa float, bool no lugar de int e valor fora do tipo;
    # só traduzimos para ValueError para quem chama tratar um tipo só.
    try:
        return encode(TIPOS_LEITURA, leitura.valores())
    except EncodingError as erro:
        raise ValueError(f"leitura não codificável: {erro}") from erro


def hash_leitura(leitura: Leitura) -> bytes:
    return keccak(codificar(leitura))


def _mensagem(leitura: Leitura):
    # EIP-191 sobre o hash, e não sobre os bytes crus: é o formato que
    # `ECDSA.toEthSignedMessageHash` reproduz em Solidity, se um dia a
    # verificação também for feita em cadeia.
    return encode_defunct(primitive=hash_leitura(leitura))


# Recebe a conta, não a chave crua, para que a chave do dispositivo nunca
# precise circular como valor solto pelo pacote.
def assinar(leitura: Leitura, chave: LocalAccount) -> bytes:
    return bytes(chave.sign_message(_mensagem(leitura)).signature)


def signatario(leitura: Leitura, assinatura: bytes) -> str:
    # Assinatura malformada (tamanho errado, r/s fora da curva) vira ValueError
    # para o oráculo classificá-la como inválida, igual a uma de outra chave.
    try:
        return Account.recover_message(_mensagem(leitura), signature=assinatura)
    except (BadSignature, ValidationError) as erro:
        raise ValueError(f"assinatura malformada: {erro}") from erro


def celsius_para_decimos(celsius: float) -> int:
    if not math.isfinite(celsius):
        raise ValueError(f"temperatura não finita: {celsius}")
    # Decimal a partir do repr evita que 2.25 vire 2.2499999... em binário e
    # arredonde para baixo; meio arredonda para longe do zero, como na escola.
    decimos = int(
        (Decimal(repr(celsius)) * 10).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )
    if not INT16_MIN <= decimos <= INT16_MAX:
        raise ValueError(f"{celsius} °C não cabe em int16 de décimos de grau")
    return decimos
