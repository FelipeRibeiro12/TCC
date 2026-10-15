"""Relógio virtual compartilhado pelo simulador, pelo oráculo e pelo runner.

Uma rodada de 10 min precisa rodar em segundos e dar o mesmo resultado nas 20
repetições; por isso nenhum módulo do pacote consulta o relógio do sistema.
Quem conduz o experimento avança o tempo explicitamente.
"""

# Instante arbitrário e fixo (nov/2027), longe o bastante do relógio real para
# que o nó Hardhat aceite os timestamps sem conflito com blocos já minerados.
INICIO_PADRAO = 1_800_000_000


class RelogioVirtual:
    def __init__(self, inicio: int = INICIO_PADRAO) -> None:
        if inicio < 0:
            raise ValueError(f"inicio negativo: {inicio}")
        # Segundos inteiros porque o timestamp vai para um uint64 na leitura
        # assinada e no contrato; fração de segundo não sobreviveria.
        self._agora = int(inicio)

    def agora(self) -> int:
        return self._agora

    def avancar(self, seg: int) -> int:
        # Tempo voltando quebraria a checagem de atualidade e de timestamp
        # regressivo do oráculo, então o relógio só anda para frente.
        if seg < 0:
            raise ValueError(f"o relógio não volta: avancar({seg})")
        self._agora += int(seg)
        return self._agora
