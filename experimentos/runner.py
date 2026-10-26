"""Executa roteiros declarativos no Hardhat e grava os dados do experimento."""

import argparse
import csv
import json
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError

from oraculo.cenario import (
    DIRETORIO_CENARIOS,
    Esperado,
    Evento,
    Tipo,
    carregar_todos,
)
from oraculo.contas import CHAIN_ID_LOCAL, conta_do_papel
from oraculo.executor import Emissao, Executor
from oraculo.log_rejeicoes import CABECALHO as CABECALHO_REJEICOES
from oraculo.log_rejeicoes import LogRejeicoes
from oraculo.oraculo import (
    Oraculo,
    ResultadoTransacao,
    _resumo_revert,
)
from oraculo.relogio import INICIO_PADRAO, RelogioVirtual, sincronizar_no

RAIZ = Path(__file__).resolve().parent.parent
DIRETORIO_CONTRATOS = RAIZ / "contracts"
DIRETORIO_RESULTADOS = Path(__file__).resolve().parent / "resultados"
VALOR_SERVICO_WEI = Web3.to_wei(1, "ether")
LIMITE_GAS = 800_000
REDE = "hardhat-local"

CABECALHO_TRANSACOES = (
    "experimento",
    "repeticao",
    "funcao",
    "chamador",
    "gas_usado",
    "bloco",
    "latencia_ms",
    "status",
    "erro",
    "timestamp_envio",
    "rede",
)
CABECALHO_CLASSIFICACAO = (
    "experimento",
    "repeticao",
    "evento_injetado",
    "evento_classificado",
    "penalidade_esperada_wei",
    "penalidade_aplicada_wei",
)


class CSVAppender:
    def __init__(self, caminho: Path, cabecalho: Iterable[str]) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        vazio = not caminho.exists() or caminho.stat().st_size == 0
        self.arquivo = caminho.open("a", newline="", encoding="utf-8")
        self.escritor = csv.writer(self.arquivo)
        if vazio:
            self.escritor.writerow(cabecalho)

    def escrever(self, linha: Iterable[Any]) -> None:
        self.escritor.writerow(linha)
        self.arquivo.flush()

    def fechar(self) -> None:
        self.arquivo.close()


def _carregar_artifact(nome: str) -> dict:
    caminho = (
        DIRETORIO_CONTRATOS
        / "artifacts"
        / "contracts"
        / f"{nome}.sol"
        / f"{nome}.json"
    )
    if not caminho.is_file():
        raise FileNotFoundError(
            f"Artifact Hardhat ausente: {caminho}. "
            "Instale as dependências em contracts/ e execute `npm run compile`."
        )
    return json.loads(caminho.read_text(encoding="utf-8"))


def _registrar_transacao(
    w3: Web3,
    conta,
    chamada,
    *,
    funcao: str,
    papel: str,
    experimento: str,
    repeticao: int,
    saida: CSVAppender,
    relogio: RelogioVirtual | None = None,
    value: int = 0,
    limite_gas: int = LIMITE_GAS,
) -> tuple[Any, str]:
    inicio = time.perf_counter_ns()
    timestamp_envio = datetime.now(timezone.utc).isoformat()
    if relogio is not None:
        sincronizar_no(w3, relogio)

    transacao = chamada.build_transaction(
        {
            "from": conta.address,
            "nonce": w3.eth.get_transaction_count(conta.address, "pending"),
            "gas": limite_gas,
            "chainId": w3.eth.chain_id,
            "value": value,
        }
    )
    assinada = conta.sign_transaction(transacao)
    erro = ""
    hash_tx: str | None = None
    recibo = None
    try:
        hash_tx = w3.eth.send_raw_transaction(assinada.raw_transaction).to_0x_hex()
        recibo = w3.eth.wait_for_transaction_receipt(hash_tx)
    except (Web3RPCError, ContractLogicError) as excecao:
        erro = _resumo_revert(str(getattr(excecao, "message", excecao)))
        resposta = getattr(excecao, "rpc_response", None) or {}
        dados = (resposta.get("error") or {}).get("data")
        if isinstance(dados, dict) and dados.get("txHash"):
            hash_tx = dados["txHash"]
            recibo = w3.eth.wait_for_transaction_receipt(hash_tx)
        elif hash_tx is None:
            raise

    if recibo is None:
        raise RuntimeError(f"{funcao}: transação enviada sem recibo ({hash_tx})")

    status = "ok" if recibo["status"] == 1 else "revert"
    if status == "revert" and not erro:
        erro = "revert sem motivo"
    latencia_ms = (time.perf_counter_ns() - inicio) / 1e6
    saida.escrever(
        (
            experimento,
            repeticao,
            funcao,
            papel,
            recibo["gasUsed"],
            recibo["blockNumber"],
            f"{latencia_ms:.3f}",
            status,
            erro,
            timestamp_envio,
            REDE,
        )
    )
    return recibo, status


def _implantar(
    w3: Web3,
    artifact: dict,
    nome: str,
    argumentos: tuple,
    *,
    conta,
    experimento: str,
    repeticao: int,
    saida: CSVAppender,
):
    fabrica = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    recibo, status = _registrar_transacao(
        w3,
        conta,
        fabrica.constructor(*argumentos),
        funcao=f"deploy:{nome}",
        papel="dono",
        experimento=experimento,
        repeticao=repeticao,
        saida=saida,
        limite_gas=5_000_000,
    )
    if status != "ok" or not recibo.get("contractAddress"):
        raise RuntimeError(f"deploy de {nome} falhou: {recibo}")
    return w3.eth.contract(
        address=Web3.to_checksum_address(recibo["contractAddress"]),
        abi=artifact["abi"],
    )


def _publicar_enderecos(w3: Web3, twin, escrow, twin_id: int) -> None:
    caminho = RAIZ / "abi" / "enderecos.json"
    dados = {
        "rede": REDE,
        "chainId": w3.eth.chain_id,
        "contratos": {
            "AssetTwin": twin.address,
            "ShipmentEscrow": escrow.address,
        },
        "twinInicial": twin_id,
        "contas": {
            papel: conta_do_papel(papel).address
            for papel in ("dono", "contratante", "transportador", "recebedor", "oraculo")
        },
    }
    caminho.write_text(json.dumps(dados, indent=2) + "\n", encoding="utf-8")


def _linhas_eventos(escrow, servico_id: int, bloco_inicial: int) -> list[tuple[int, str, dict]]:
    nomes = {
        "ViolacaoAmbiental": Esperado.DESVIO_TEMPERATURA.value,
        "TempoNaoObservado": Esperado.NAO_OBSERVADO.value,
        "ViolacaoLacre": Esperado.VIOLACAO_LACRE.value,
        "ServicoConcluido": Esperado.CONFORME.value,
        "ServicoRescindido": Esperado.VIOLACAO_LACRE.value,
    }
    eventos = []
    bloco_final = escrow.w3.eth.block_number
    for nome, rotulo in nomes.items():
        for log in getattr(escrow.events, nome)().get_logs(
            from_block=bloco_inicial, to_block=bloco_final
        ):
            argumentos = dict(log["args"])
            if argumentos.get("servicoId") != servico_id:
                continue
            timestamp = escrow.w3.eth.get_block(log["blockNumber"])["timestamp"]
            eventos.append((timestamp, rotulo, argumentos))
    return sorted(eventos, key=lambda evento: evento[0])


def _rotulo_observado(
    eventos_cadeia: list[tuple[int, str, dict]],
    *,
    inicio: int,
    fim: int,
    concluido: bool,
) -> tuple[str, int]:
    dentro = [evento for evento in eventos_cadeia if inicio <= evento[0] <= fim]
    prioridade = (
        Esperado.VIOLACAO_LACRE.value,
        Esperado.DESVIO_TEMPERATURA.value,
        Esperado.NAO_OBSERVADO.value,
    )
    for rotulo in prioridade:
        correspondentes = [evento for evento in dentro if evento[1] == rotulo]
        if correspondentes:
            args = correspondentes[-1][2]
            penalidade = int(args.get("penalidadeAcumulada", 0))
            if rotulo == Esperado.VIOLACAO_LACRE.value:
                penalidade = int(args.get("valorDevolvido", penalidade))
            return rotulo, penalidade
    if concluido:
        return Esperado.CONFORME.value, 0
    raise RuntimeError(
        f"nenhum evento de classificação observado no intervalo {inicio}–{fim}"
    )


def _penalidade_esperada(escrow, evento: Evento, valor: int, periodicidade: int) -> int:
    if evento.esperado is Esperado.CONFORME:
        return 0
    if evento.esperado is Esperado.VIOLACAO_LACRE:
        return valor
    if evento.tipo is Tipo.SILENCIO:
        segundos = max(0, (evento.duracao or 0) - periodicidade)
    else:
        segundos = evento.duracao or 0
    return int(escrow.functions.multaDe(valor, segundos).call())


def _gravar_classificacao(
    cenario,
    *,
    experimento: str,
    repeticao: int,
    executor: Executor,
    escrow,
    servico_id: int,
    bloco_inicial: int,
    saida: CSVAppender,
) -> None:
    eventos_cadeia = _linhas_eventos(escrow, servico_id, bloco_inicial)
    concluiu = any(
        rotulo == Esperado.CONFORME.value for _, rotulo, _ in eventos_cadeia
    )
    eventos_validos = [
        evento
        for evento in cenario.eventos
        if evento.esperado is not Esperado.REJEITADO_ORACULO
    ]
    if not eventos_validos:
        eventos_validos = [
            Evento(
                inicio=0,
                tipo=Tipo.EXCURSAO_TEMPERATURA,
                esperado=Esperado.CONFORME,
                duracao=cenario.duracao_seg,
            )
        ]

    for evento in eventos_validos:
        inicio = executor.t0 + evento.inicio
        fim = inicio + (evento.duracao or 0) + cenario.servico.periodicidade_seg
        observado, aplicada = _rotulo_observado(
            eventos_cadeia, inicio=inicio, fim=fim, concluido=concluiu
        )
        esperada = _penalidade_esperada(
            escrow, evento, VALOR_SERVICO_WEI, cenario.servico.periodicidade_seg
        )
        saida.escrever(
            (
                experimento,
                repeticao,
                evento.esperado.value,
                observado,
                esperada,
                aplicada,
            )
        )


def _transacoes_oraculo(
    oraculo: Oraculo,
    inicio: int,
    *,
    experimento: str,
    repeticao: int,
    saida: CSVAppender,
) -> int:
    for transacao in oraculo.transacoes[inicio:]:
        _gravar_resultado_oraculo(
            transacao, experimento=experimento, repeticao=repeticao, saida=saida
        )
    return len(oraculo.transacoes)


def _gravar_resultado_oraculo(
    transacao: ResultadoTransacao,
    *,
    experimento: str,
    repeticao: int,
    saida: CSVAppender,
) -> None:
    saida.escrever(
        (
            experimento,
            repeticao,
            transacao.funcao,
            "oraculo",
            transacao.gas_usado,
            transacao.bloco,
            f"{transacao.latencia_ms:.3f}",
            transacao.status,
            transacao.erro,
            datetime.now(timezone.utc).isoformat(),
            REDE,
        )
    )


def _chamar_nao_oraculo(
    w3: Web3,
    escrow,
    relogio: RelogioVirtual,
    *,
    servico_id: int,
    emissao: Emissao,
    experimento: str,
    repeticao: int,
    saida: CSVAppender,
) -> None:
    leitura = emissao.leitura.leitura
    argumentos = (
        servico_id,
        Web3.keccak(text=f"teste-nao-oraculo-{repeticao}"),
        leitura.temperatura,
        leitura.umidade,
        leitura.lacre_aberto,
        leitura.timestamp,
    )
    _registrar_transacao(
        w3,
        conta_do_papel("contratante"),
        escrow.functions.registrarCheckpoint(*argumentos),
        funcao="registrarCheckpoint",
        papel="contratante",
        experimento=experimento,
        repeticao=repeticao,
        saida=saida,
        relogio=relogio,
    )


def executar_repeticao(
    w3: Web3,
    cenario,
    experimento: str,
    repeticao: int,
    *,
    saidas: tuple[CSVAppender, CSVAppender, Path],
    artifact_twin: dict,
    artifact_escrow: dict,
) -> None:
    transacoes_csv, classificacao_csv, caminho_rejeicoes = saidas
    dono = conta_do_papel("dono")
    contratante = conta_do_papel("contratante")
    transportador = conta_do_papel("transportador")
    recebedor = conta_do_papel("recebedor")
    conta_oraculo = conta_do_papel("oraculo")
    conta_dispositivo = conta_do_papel("dispositivo")

    twin = _implantar(
        w3,
        artifact_twin,
        "AssetTwin",
        (dono.address,),
        conta=dono,
        experimento=experimento,
        repeticao=repeticao,
        saida=transacoes_csv,
    )
    escrow = _implantar(
        w3,
        artifact_escrow,
        "ShipmentEscrow",
        (twin.address, conta_oraculo.address),
        conta=dono,
        experimento=experimento,
        repeticao=repeticao,
        saida=transacoes_csv,
    )
    recibo, status = _registrar_transacao(
        w3,
        dono,
        twin.functions.criarTwin(
            contratante.address, Web3.keccak(text=f"container-{repeticao}")
        ),
        funcao="criarTwin",
        papel="dono",
        experimento=experimento,
        repeticao=repeticao,
        saida=transacoes_csv,
    )
    if status != "ok":
        raise RuntimeError(f"criarTwin reverteu: {recibo}")
    twin_id = twin.functions.ultimoTwinId().call()
    _publicar_enderecos(w3, twin, escrow, twin_id)

    relogio = RelogioVirtual(max(INICIO_PADRAO, w3.eth.get_block("latest")["timestamp"] + 100))
    bloco_inicial = w3.eth.block_number + 1
    prazo = relogio.agora() + 86_400
    recibo, status = _registrar_transacao(
        w3,
        contratante,
        escrow.functions.abrirServico(
            twin_id,
            transportador.address,
            recebedor.address,
            cenario.servico.temp_min,
            cenario.servico.temp_max,
            cenario.servico.periodicidade_seg,
            prazo,
        ),
        funcao="abrirServico",
        papel="contratante",
        experimento=experimento,
        repeticao=repeticao,
        saida=transacoes_csv,
        relogio=relogio,
        value=VALOR_SERVICO_WEI,
    )
    if status != "ok":
        raise RuntimeError(f"abrirServico reverteu: {recibo}")
    servico_id = escrow.functions.ultimoServicoId().call()
    if servico_id == 0:
        raise RuntimeError(
            "abrirServico foi minerada, mas ultimoServicoId continuou em 0; "
            "verifique a implementação do contrato ShipmentEscrow."
        )

    recibo, status = _registrar_transacao(
        w3,
        transportador,
        escrow.functions.aceitarServico(servico_id),
        funcao="aceitarServico",
        papel="transportador",
        experimento=experimento,
        repeticao=repeticao,
        saida=transacoes_csv,
        relogio=relogio,
    )
    if status != "ok":
        raise RuntimeError(f"aceitarServico reverteu: {recibo}")

    bloco_aceite = w3.eth.get_block(recibo["blockNumber"])["timestamp"]
    relogio.avancar(max(0, bloco_aceite - relogio.agora()) + 1)
    executor = Executor(
        cenario,
        conta_dispositivo,
        servico_id,
        relogio,
        semente=cenario.semente + repeticao,
    )

    janela = executor.janela_lacre()
    if janela is not None:
        recibo, status = _registrar_transacao(
            w3,
            contratante,
            escrow.functions.autorizarJanelaLacre(
                servico_id, janela.inicio, janela.fim
            ),
            funcao="autorizarJanelaLacre",
            papel="contratante",
            experimento=experimento,
            repeticao=repeticao,
            saida=transacoes_csv,
            relogio=relogio,
        )
        if status != "ok":
            raise RuntimeError(f"autorizarJanelaLacre reverteu: {recibo}")

    if cenario.nome == "07_rejeicoes":
        _registrar_transacao(
            w3,
            transportador,
            escrow.functions.aceitarServico(servico_id),
            funcao="aceitarServico",
            papel="transportador",
            experimento=experimento,
            repeticao=repeticao,
            saida=transacoes_csv,
            relogio=relogio,
        )

    log_rejeicoes = LogRejeicoes(caminho_rejeicoes, experimento, repeticao)
    oraculo = Oraculo(
        w3,
        conta_oraculo=conta_oraculo,
        endereco_escrow=escrow.address,
        relogio=relogio,
        dispositivos_por_servico={servico_id: conta_dispositivo.address},
        log_rejeicoes=log_rejeicoes,
        modo_agregacao=cenario.modo_agregacao,
    )

    cursor_txs = 0
    testou_nao_oraculo = False
    for emissao in executor:
        recebimento = oraculo.receber(emissao.leitura)
        cursor_txs = _transacoes_oraculo(
            oraculo,
            cursor_txs,
            experimento=experimento,
            repeticao=repeticao,
            saida=transacoes_csv,
        )
        if (
            cenario.nome == "07_rejeicoes"
            and not testou_nao_oraculo
            and recebimento.transacao is not None
            and recebimento.transacao.status == "ok"
        ):
            _chamar_nao_oraculo(
                w3,
                escrow,
                relogio,
                servico_id=servico_id,
                emissao=emissao,
                experimento=experimento,
                repeticao=repeticao,
                saida=transacoes_csv,
            )
            testou_nao_oraculo = True
        if recebimento.servico_encerrado:
            break

    resultado_final = oraculo.encerrar(servico_id)
    if resultado_final is not None:
        cursor_txs = _transacoes_oraculo(
            oraculo,
            cursor_txs,
            experimento=experimento,
            repeticao=repeticao,
            saida=transacoes_csv,
        )

    if servico_id not in oraculo.encerrados:
        _registrar_transacao(
            w3,
            recebedor,
            escrow.functions.confirmarEntrega(servico_id),
            funcao="confirmarEntrega",
            papel="recebedor",
            experimento=experimento,
            repeticao=repeticao,
            saida=transacoes_csv,
            relogio=relogio,
        )

    if cenario.nome == "07_rejeicoes" and not testou_nao_oraculo:
        raise RuntimeError(
            "o cenário 07 não produziu checkpoint válido; não foi possível "
            "testar o chamador não autorizado em um estado válido."
        )
    _gravar_classificacao(
        cenario,
        experimento=experimento,
        repeticao=repeticao,
        executor=executor,
        escrow=escrow,
        servico_id=servico_id,
        bloco_inicial=bloco_inicial,
        saida=classificacao_csv,
    )


def _nome_experimento(cenario) -> str:
    if not cenario.variante:
        return cenario.nome
    variante = "_".join(f"{chave}={valor}" for chave, valor in cenario.variante.items())
    return f"{cenario.nome}_{variante}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Executa cenários locais do Claustrum e grava CSVs."
    )
    parser.add_argument(
        "cenarios",
        nargs="*",
        help="nome(s) do cenário; sem nomes, executa todos os JSONs e variantes",
    )
    parser.add_argument("--repeticoes", type=int, default=20)
    parser.add_argument("--rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--resultados", type=Path, default=DIRETORIO_RESULTADOS)
    args = parser.parse_args(argv)
    if args.repeticoes < 1:
        parser.error("--repeticoes deve ser pelo menos 1")

    cenarios = carregar_todos(DIRETORIO_CENARIOS)
    desconhecidos = set(args.cenarios) - {cenario.nome for cenario in cenarios}
    if desconhecidos:
        parser.error(f"cenário(s) desconhecido(s): {', '.join(sorted(desconhecidos))}")
    if args.cenarios:
        cenarios = [cenario for cenario in cenarios if cenario.nome in args.cenarios]

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        raise ConnectionError(
            f"Hardhat não respondeu em {args.rpc}. Inicie o nó local com `npm run node` "
            "no diretório contracts/."
        )
    if w3.eth.chain_id != CHAIN_ID_LOCAL:
        raise RuntimeError(
            f"chainId inesperado: {w3.eth.chain_id}; esperado {CHAIN_ID_LOCAL}."
        )

    artifact_twin = _carregar_artifact("AssetTwin")
    artifact_escrow = _carregar_artifact("ShipmentEscrow")
    args.resultados.mkdir(parents=True, exist_ok=True)
    with ExitStack() as pilha:
        transacoes_csv = CSVAppender(
            args.resultados / "transacoes.csv", CABECALHO_TRANSACOES
        )
        classificacao_csv = CSVAppender(
            args.resultados / "classificacao.csv", CABECALHO_CLASSIFICACAO
        )
        pilha.callback(transacoes_csv.fechar)
        pilha.callback(classificacao_csv.fechar)

        caminho_rejeicoes = args.resultados / "rejeicoes.csv"
        # Inicializa o cabeçalho mesmo quando uma rodada não contém rejeições.
        rejeicoes_csv = CSVAppender(caminho_rejeicoes, CABECALHO_REJEICOES)
        pilha.callback(rejeicoes_csv.fechar)

        for cenario in cenarios:
            for variante in cenario.variantes():
                experimento = _nome_experimento(variante)
                for repeticao in range(1, args.repeticoes + 1):
                    executar_repeticao(
                        w3,
                        variante,
                        experimento,
                        repeticao,
                        saidas=(
                            transacoes_csv,
                            classificacao_csv,
                            caminho_rejeicoes,
                        ),
                        artifact_twin=artifact_twin,
                        artifact_escrow=artifact_escrow,
                    )
                    print(
                        f"{experimento}: repetição {repeticao}/{args.repeticoes} concluída"
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
