import pytest
from eth_account.signers.local import LocalAccount

from oraculo.contas import PAPEIS, conta_do_papel
from oraculo.relogio import RelogioVirtual


@pytest.fixture
def contas() -> dict[str, LocalAccount]:
    return {papel: conta_do_papel(papel) for papel in PAPEIS}


# Relógio novo a cada teste, para um teste não herdar o tempo avançado por
# outro — o mesmo isolamento que a divisão 3 exige entre repetições.
@pytest.fixture
def relogio() -> RelogioVirtual:
    return RelogioVirtual()
