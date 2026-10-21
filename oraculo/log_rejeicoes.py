"""Log das leituras que o oráculo recusou, em CSV.

O log é dado de resultado do artigo: mostra que a camada de oráculo filtrou o
que devia. Por isso o par experimento/repetição vai em cada linha, e as 20
repetições de um cenário podem acrescentar ao mesmo arquivo.
"""

import csv
from pathlib import Path

from oraculo.assinatura import Leitura

CABECALHO = (
    "experimento",
    "repeticao",
    "servico_id",
    "dispositivo",
    "nonce",
    "timestamp_leitura",
    "instante_recebimento",
    "motivo",
)


class LogRejeicoes:
    def __init__(self, caminho: str | Path, experimento: str, repeticao: int) -> None:
        self.caminho = Path(caminho)
        self.experimento = experimento
        self.repeticao = repeticao

    def registrar(
        self, leitura: Leitura, instante_recebimento: int, motivo: str
    ) -> None:
        # Abre e fecha a cada linha: se o experimento cair no meio, o que já
        # foi rejeitado está no disco, e várias instâncias (uma por repetição)
        # podem acrescentar ao mesmo arquivo sem disputar um handle aberto.
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        novo = not self.caminho.exists() or self.caminho.stat().st_size == 0
        with self.caminho.open("a", newline="", encoding="utf-8") as arquivo:
            escritor = csv.writer(arquivo)
            if novo:
                escritor.writerow(CABECALHO)
            # `dispositivo` é o endereço que a leitura declara, não o
            # recuperado da assinatura: numa forjada, o recuperado é o do
            # atacante, e o que interessa é quem ele tentou se passar.
            escritor.writerow(
                (
                    self.experimento,
                    self.repeticao,
                    leitura.servico_id,
                    leitura.dispositivo,
                    leitura.nonce,
                    leitura.timestamp,
                    instante_recebimento,
                    motivo,
                )
            )
