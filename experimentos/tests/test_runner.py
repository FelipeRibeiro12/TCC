import csv

from oraculo.cenario import carregar_todos
from experimentos.runner import CSVAppender, _nome_experimento


def test_volume_scenario_expands_to_ten_unique_experiments():
    cenario = next(
        cenario for cenario in carregar_todos() if cenario.nome == "06_volume_telemetria"
    )

    nomes = [_nome_experimento(variante) for variante in cenario.variantes()]

    assert len(nomes) == 10
    assert len(set(nomes)) == 10
    assert any("periodo_amostra_seg=1_modo_agregacao=bruto" in nome for nome in nomes)
    assert any("periodo_amostra_seg=30_modo_agregacao=agregado" in nome for nome in nomes)


def test_csv_appender_writes_one_header_and_appends_rows(tmp_path):
    caminho = tmp_path / "resultados" / "transacoes.csv"
    cabecalho = ("experimento", "status")
    primeiro = CSVAppender(caminho, cabecalho)
    primeiro.escrever(("01_conforme", "ok"))
    primeiro.fechar()

    segundo = CSVAppender(caminho, cabecalho)
    segundo.escrever(("04_lacre_fora_janela", "revert"))
    segundo.fechar()

    with caminho.open(newline="", encoding="utf-8") as arquivo:
        linhas = list(csv.reader(arquivo))

    assert linhas == [
        ["experimento", "status"],
        ["01_conforme", "ok"],
        ["04_lacre_fora_janela", "revert"],
    ]
