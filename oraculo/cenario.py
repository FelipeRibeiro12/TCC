"""Roteiros declarativos de injeção: carregar, validar e expandir variantes.

O roteiro é o gabarito da matriz de confusão: cada evento traz o rótulo
`esperado` que a divisão 3 compara com o que o contrato classificou. Um rótulo
errado no arquivo vira erro de classificação no artigo, então a validação vai
além da forma do JSON e confere que o rótulo é o que o roteiro de fato produz
— excursão fora da faixa do serviço, lacre fora da janela, silêncio maior que
a periodicidade. Quando o resultado dependeria de detalhe da agregação (lacre
aberto metade dentro da janela, excursão encostada no limite da faixa), o
roteiro é recusado em vez de rotulado no chute.

Tempos do roteiro são segundos desde o início da rodada; temperaturas em
décimos de grau, como no contrato.
"""

import itertools
import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator

from oraculo.agregacao import Modo
from oraculo.oraculo import Limites, Motivo
from oraculo.simulador import (
    MODOS_ASSINATURA_INVALIDA,
    MODOS_VALOR_IMPOSSIVEL,
    SALTO_IMPOSSIVEL,
)

DIRETORIO_CENARIOS = Path(__file__).resolve().parent / "cenarios"


class ErroCenario(ValueError):
    pass


class Tipo(StrEnum):
    EXCURSAO_TEMPERATURA = "excursao_temperatura"
    ABRIR_LACRE = "abrir_lacre"
    SILENCIO = "silencio"
    ASSINATURA_INVALIDA = "assinatura_invalida"
    NONCE_REPETIDO = "nonce_repetido"
    VALOR_IMPOSSIVEL = "valor_impossivel"


# Os quatro primeiros são o vocabulário do classificacao.csv; o último marca o
# que o oráculo barra e nunca chega ao contrato.
class Esperado(StrEnum):
    CONFORME = "conforme"
    DESVIO_TEMPERATURA = "desvio_temperatura"
    VIOLACAO_LACRE = "violacao_lacre"
    NAO_OBSERVADO = "nao_observado"
    REJEITADO_ORACULO = "rejeitado_oraculo"


MALICIOSOS = (Tipo.ASSINATURA_INVALIDA, Tipo.NONCE_REPETIDO, Tipo.VALOR_IMPOSSIVEL)
# Parâmetros que um cenário pode varrer, cada combinação uma variante.
VARRIVEIS = ("periodo_amostra_seg", "modo_agregacao")


@dataclass(frozen=True)
class Janela:
    inicio: int
    fim: int

    def contem(self, inicio: int, fim: int) -> bool:
        return self.inicio <= inicio and fim <= self.fim


@dataclass(frozen=True)
class Evento:
    inicio: int
    tipo: Tipo
    esperado: Esperado
    duracao: int | None = None  # excursão, lacre e silêncio
    temperatura: int | None = None  # excursão
    janela: Janela | None = None  # lacre; é da divisão 3, o dispositivo não vê
    modo: str | None = None  # assinatura_invalida e valor_impossivel
    quantidade: int = 1  # maliciosos, em amostras
    motivo_esperado: Motivo | None = None  # maliciosos


@dataclass(frozen=True)
class Servico:
    temp_min: int
    temp_max: int
    periodicidade_seg: int


@dataclass(frozen=True)
class BaseCenario:
    temperatura: int
    umidade: int
    ruido_temperatura: int = 2
    ruido_umidade: int = 1


@dataclass(frozen=True)
class Cenario:
    nome: str
    duracao_seg: int
    periodo_amostra_seg: int
    servico: Servico
    base: BaseCenario
    semente: int
    eventos: tuple[Evento, ...]
    modo_agregacao: Modo = Modo.AGREGADO
    varrer: dict[str, tuple] = field(default_factory=dict)
    # Valores que esta variante fixou; vazio no cenário como escrito.
    variante: dict[str, Any] = field(default_factory=dict)

    @property
    def janela_lacre(self) -> Janela | None:
        return next((e.janela for e in self.eventos if e.janela is not None), None)

    def variantes(self) -> Iterator["Cenario"]:
        if not self.varrer:
            yield self
            return
        nomes = list(self.varrer)
        for valores in itertools.product(*(self.varrer[n] for n in nomes)):
            fixados = dict(zip(nomes, valores))
            yield replace(self, varrer={}, variante=fixados, **fixados)

    # Uma entrada por leitura maliciosa, na ordem em que o dispositivo as emite.
    def rejeicoes_esperadas(self) -> list[tuple[int, Motivo]]:
        rejeicoes = []
        for indice, evento in enumerate(self.eventos):
            if evento.motivo_esperado is not None:
                rejeicoes += [(indice, evento.motivo_esperado)] * evento.quantidade
        return rejeicoes


def carregar(caminho: str | Path) -> Cenario:
    caminho = Path(caminho)
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erro:
        raise ErroCenario(f"{caminho.name}: JSON inválido: {erro}") from None
    return de_dict(dados, origem=caminho.name)


def carregar_todos(diretorio: str | Path = DIRETORIO_CENARIOS) -> list[Cenario]:
    return [carregar(caminho) for caminho in sorted(Path(diretorio).glob("*.json"))]


def de_dict(dados: Any, origem: str = "cenário") -> Cenario:
    cenario = _Leitor(origem).cenario(dados)
    # Toda variante passa pela checagem de coerência: um período de amostra da
    # varredura pode, sozinho, deixar uma excursão sem nenhuma amostra.
    for variante in cenario.variantes():
        _checar_coerencia(variante, origem)
    return cenario


# --- forma ------------------------------------------------------------------


class _Leitor:
    def __init__(self, origem: str) -> None:
        self.origem = origem

    def erro(self, caminho: str, mensagem: str) -> ErroCenario:
        return ErroCenario(f"{self.origem}: {caminho}: {mensagem}")

    def objeto(
        self, valor: Any, caminho: str, obrigatorios: set[str], opcionais: set[str]
    ) -> dict:
        if not isinstance(valor, dict):
            raise self.erro(caminho, f"esperado objeto, veio {type(valor).__name__}")
        # Campo desconhecido é quase sempre erro de digitação, e ignorá-lo
        # rodaria um cenário diferente do que está escrito.
        desconhecidos = set(valor) - obrigatorios - opcionais
        if desconhecidos:
            raise self.erro(caminho, f"campos desconhecidos: {', '.join(sorted(desconhecidos))}")
        faltando = obrigatorios - set(valor)
        if faltando:
            raise self.erro(caminho, f"campos obrigatórios ausentes: {', '.join(sorted(faltando))}")
        return valor

    def inteiro(self, valor: Any, caminho: str, minimo: int | None = None) -> int:
        # bool é subclasse de int em Python; `true` no JSON não é duração.
        if not isinstance(valor, int) or isinstance(valor, bool):
            raise self.erro(caminho, f"esperado inteiro, veio {valor!r}")
        if minimo is not None and valor < minimo:
            raise self.erro(caminho, f"deve ser ≥ {minimo}, veio {valor}")
        return valor

    def escolha(self, valor: Any, caminho: str, opcoes) -> Any:
        validos = [str(o) for o in opcoes]
        if valor not in validos:
            raise self.erro(caminho, f"{valor!r} desconhecido; esperados: {', '.join(validos)}")
        return valor

    def cenario(self, dados: Any) -> Cenario:
        self.objeto(
            dados,
            "raiz",
            {"nome", "duracao_seg", "periodo_amostra_seg", "servico", "base", "semente", "eventos"},
            {"modo_agregacao", "varrer"},
        )
        if not isinstance(dados["nome"], str) or not dados["nome"]:
            raise self.erro("nome", "esperado texto não vazio")
        eventos = dados["eventos"]
        if not isinstance(eventos, list):
            raise self.erro("eventos", "esperada lista")
        return Cenario(
            nome=dados["nome"],
            duracao_seg=self.inteiro(dados["duracao_seg"], "duracao_seg", 1),
            periodo_amostra_seg=self.inteiro(
                dados["periodo_amostra_seg"], "periodo_amostra_seg", 1
            ),
            servico=self.servico(dados["servico"]),
            base=self.base(dados["base"]),
            semente=self.inteiro(dados["semente"], "semente"),
            eventos=tuple(self.evento(e, f"eventos[{i}]") for i, e in enumerate(eventos)),
            modo_agregacao=Modo(
                self.escolha(dados.get("modo_agregacao", "agregado"), "modo_agregacao", Modo)
            ),
            varrer=self.varrer(dados.get("varrer", {})),
        )

    def servico(self, dados: Any) -> Servico:
        self.objeto(dados, "servico", {"tempMin", "tempMax", "periodicidadeSeg"}, set())
        servico = Servico(
            self.inteiro(dados["tempMin"], "servico.tempMin"),
            self.inteiro(dados["tempMax"], "servico.tempMax"),
            self.inteiro(dados["periodicidadeSeg"], "servico.periodicidadeSeg", 1),
        )
        if servico.temp_min > servico.temp_max:
            raise self.erro("servico", "tempMin maior que tempMax")
        return servico

    def base(self, dados: Any) -> BaseCenario:
        self.objeto(
            dados, "base", {"temperatura", "umidade"}, {"ruido_temperatura", "ruido_umidade"}
        )
        umidade = self.inteiro(dados["umidade"], "base.umidade", 0)
        if umidade > 100:
            raise self.erro("base.umidade", f"fora de 0–100: {umidade}")
        return BaseCenario(
            self.inteiro(dados["temperatura"], "base.temperatura"),
            umidade,
            self.inteiro(dados.get("ruido_temperatura", 2), "base.ruido_temperatura", 0),
            self.inteiro(dados.get("ruido_umidade", 1), "base.ruido_umidade", 0),
        )

    def varrer(self, dados: Any) -> dict[str, tuple]:
        self.objeto(dados, "varrer", set(), set(VARRIVEIS))
        varrer = {}
        for nome, valores in dados.items():
            caminho = f"varrer.{nome}"
            if not isinstance(valores, list) or not valores:
                raise self.erro(caminho, "esperada lista não vazia")
            if len(set(valores)) != len(valores):
                raise self.erro(caminho, "valores repetidos")
            if nome == "periodo_amostra_seg":
                varrer[nome] = tuple(
                    self.inteiro(v, f"{caminho}[{i}]", 1) for i, v in enumerate(valores)
                )
            else:
                varrer[nome] = tuple(
                    Modo(self.escolha(v, f"{caminho}[{i}]", Modo)) for i, v in enumerate(valores)
                )
        return varrer

    def evento(self, dados: Any, caminho: str) -> Evento:
        if not isinstance(dados, dict):
            raise self.erro(caminho, "esperado objeto")
        tipo = Tipo(self.escolha(dados.get("tipo"), f"{caminho}.tipo", Tipo))
        comuns = {"inicio", "tipo", "esperado"}
        if tipo is Tipo.EXCURSAO_TEMPERATURA:
            self.objeto(dados, caminho, comuns | {"duracao", "temperatura"}, set())
        elif tipo is Tipo.ABRIR_LACRE:
            self.objeto(dados, caminho, comuns | {"duracao"}, {"janela"})
        elif tipo is Tipo.SILENCIO:
            self.objeto(dados, caminho, comuns | {"duracao"}, set())
        elif tipo is Tipo.NONCE_REPETIDO:
            self.objeto(dados, caminho, comuns | {"motivo_esperado"}, {"quantidade"})
        else:
            self.objeto(dados, caminho, comuns | {"motivo_esperado"}, {"modo", "quantidade"})

        janela = None
        if "janela" in dados:
            self.objeto(dados["janela"], f"{caminho}.janela", {"inicio", "fim"}, set())
            janela = Janela(
                self.inteiro(dados["janela"]["inicio"], f"{caminho}.janela.inicio", 0),
                self.inteiro(dados["janela"]["fim"], f"{caminho}.janela.fim", 0),
            )
            if janela.inicio >= janela.fim:
                raise self.erro(f"{caminho}.janela", "inicio deve ser menor que fim")

        modo = None
        if tipo is Tipo.ASSINATURA_INVALIDA:
            modo = self.escolha(
                dados.get("modo", "outra_chave"), f"{caminho}.modo", MODOS_ASSINATURA_INVALIDA
            )
        elif tipo is Tipo.VALOR_IMPOSSIVEL:
            modo = self.escolha(
                dados.get("modo", "temperatura"), f"{caminho}.modo", MODOS_VALOR_IMPOSSIVEL
            )

        return Evento(
            inicio=self.inteiro(dados["inicio"], f"{caminho}.inicio", 0),
            tipo=tipo,
            esperado=Esperado(self.escolha(dados["esperado"], f"{caminho}.esperado", Esperado)),
            duracao=(
                self.inteiro(dados["duracao"], f"{caminho}.duracao", 1)
                if "duracao" in dados
                else None
            ),
            temperatura=(
                self.inteiro(dados["temperatura"], f"{caminho}.temperatura")
                if "temperatura" in dados
                else None
            ),
            janela=janela,
            modo=modo,
            quantidade=self.inteiro(dados.get("quantidade", 1), f"{caminho}.quantidade", 1),
            motivo_esperado=(
                Motivo(self.escolha(dados["motivo_esperado"], f"{caminho}.motivo_esperado", Motivo))
                if "motivo_esperado" in dados
                else None
            ),
        )


# --- coerência --------------------------------------------------------------


def _motivo_do_malicioso(evento: Evento) -> Motivo:
    if evento.tipo is Tipo.ASSINATURA_INVALIDA:
        return Motivo.ASSINATURA_INVALIDA
    if evento.tipo is Tipo.NONCE_REPETIDO:
        return Motivo.NONCE_REPETIDO
    if evento.modo == "salto":
        return Motivo.VARIACAO_IMPOSSIVEL
    return Motivo.VALOR_IMPOSSIVEL


def _amostras(inicio: int, fim: int, periodo: int) -> list[int]:
    # O dispositivo amostra em 0, p, 2p, ... desde o início da rodada, e o
    # silêncio não desloca essa grade.
    primeira = -(-inicio // periodo) * periodo
    return list(range(primeira, fim, periodo))


def _checar_coerencia(cenario: Cenario, origem: str, limites: Limites = Limites()) -> None:
    rotulo = origem + (f" {cenario.variante}" if cenario.variante else "")

    def erro(caminho: str, mensagem: str) -> ErroCenario:
        return ErroCenario(f"{rotulo}: {caminho}: {mensagem}")

    p = cenario.periodo_amostra_seg
    servico, base = cenario.servico, cenario.base
    if p > servico.periodicidade_seg:
        raise erro(
            "periodo_amostra_seg",
            f"{p} s maior que a periodicidade do serviço ({servico.periodicidade_seg} s): "
            "toda lacuna entre amostras viraria tempo não observado",
        )
    if not (servico.temp_min <= base.temperatura - base.ruido_temperatura
            and base.temperatura + base.ruido_temperatura <= servico.temp_max):
        raise erro("base", "base com ruído precisa ficar dentro da faixa do serviço")

    # Instante (relativo) e amostras ocupadas por cada evento, para checar
    # sobreposição na mesma escala em que o dispositivo resolve conflitos.
    intervalos: dict[int, tuple[int, int]] = {}
    for i, evento in enumerate(cenario.eventos):
        caminho = f"eventos[{i}]"
        if evento.tipo in MALICIOSOS:
            amostras = _amostras(evento.inicio, cenario.duracao_seg, p)[: evento.quantidade]
            if len(amostras) < evento.quantidade:
                raise erro(caminho, f"{evento.quantidade} amostra(s) não cabem antes do fim da rodada")
            intervalos[i] = (amostras[0], amostras[-1] + p)
        else:
            fim = evento.inicio + evento.duracao
            if fim > cenario.duracao_seg:
                raise erro(caminho, f"termina em {fim} s, depois do fim da rodada ({cenario.duracao_seg} s)")
            if evento.tipo is not Tipo.SILENCIO and not _amostras(evento.inicio, fim, p):
                raise erro(caminho, f"não cobre nenhuma amostra com período de {p} s")
            intervalos[i] = (evento.inicio, fim)

    _checar_sobreposicao(cenario, intervalos, erro)
    _checar_reenvio(cenario, intervalos, erro)

    janelas = [i for i, e in enumerate(cenario.eventos) if e.janela is not None]
    # O Servico guarda uma janela só (janelaLacreInicio/Fim); uma segunda
    # autorização sobrescreveria a primeira e o rótulo da abertura anterior.
    if len(janelas) > 1:
        raise erro(f"eventos[{janelas[1]}].janela", "o contrato guarda uma janela de lacre por serviço")
    janela = cenario.janela_lacre
    if janela is not None and janela.fim > cenario.duracao_seg:
        raise erro(f"eventos[{janelas[0]}].janela", "termina depois do fim da rodada")

    for i, evento in enumerate(cenario.eventos):
        caminho = f"eventos[{i}]"
        inicio, fim = intervalos[i]
        esperado = _esperado(cenario, evento, inicio, fim, janela, limites, lambda m: erro(caminho, m))
        if esperado is not None and evento.esperado is not esperado:
            raise erro(f"{caminho}.esperado", f"o roteiro produz {esperado!s}, não {evento.esperado!s}")
        if evento.tipo in MALICIOSOS:
            motivo = _motivo_do_malicioso(evento)
            if evento.motivo_esperado is not motivo:
                raise erro(f"{caminho}.motivo_esperado", f"o oráculo rejeita por {motivo!s}, não {evento.motivo_esperado!s}")

    # Lacre fora de janela rescinde o serviço na hora; o que viesse depois
    # bateria num serviço RESCINDIDO e não mediria o que o rótulo diz.
    for i, evento in enumerate(cenario.eventos):
        if evento.esperado is Esperado.VIOLACAO_LACRE:
            depois = [j for j, e in enumerate(cenario.eventos) if e.inicio > evento.inicio]
            if depois:
                raise erro(
                    f"eventos[{depois[0]}]",
                    f"começa depois da violação de lacre em eventos[{i}], que rescinde o serviço",
                )


def _checar_sobreposicao(cenario: Cenario, intervalos, erro) -> None:
    eventos = cenario.eventos
    for i, j in itertools.combinations(range(len(eventos)), 2):
        a, b = eventos[i], eventos[j]
        (ia, fa), (ib, fb) = intervalos[i], intervalos[j]
        if not (ia < fb and ib < fa):
            continue
        # Excursão com lacre aberto é um cenário físico legítimo (porta aberta
        # aquece a carga). Qualquer outro par perde leituras de um dos dois:
        # silêncio cala tudo, e a injeção maliciosa toma o lugar da amostra.
        if {a.tipo, b.tipo} == {Tipo.EXCURSAO_TEMPERATURA, Tipo.ABRIR_LACRE}:
            continue
        raise erro(
            f"eventos[{j}]",
            f"{b.tipo!s} em [{ib}, {fb}) sobrepõe {a.tipo!s} de eventos[{i}] em [{ia}, {fa})",
        )


def _checar_reenvio(cenario: Cenario, intervalos, erro) -> None:
    # O dispositivo reenvia a última leitura legítima; se toda amostra anterior
    # foi calada por silêncio ou tomada por outro malicioso, não há o que
    # reenviar e o executor quebraria no meio da rodada.
    p = cenario.periodo_amostra_seg
    silencios = [intervalos[i] for i, e in enumerate(cenario.eventos) if e.tipo is Tipo.SILENCIO]
    ocupadas = {
        amostra
        for i, e in enumerate(cenario.eventos)
        if e.tipo in MALICIOSOS
        for amostra in _amostras(*intervalos[i], p)
    }
    for i, evento in enumerate(cenario.eventos):
        if evento.tipo is not Tipo.NONCE_REPETIDO:
            continue
        legitimas = [
            amostra
            for amostra in _amostras(0, intervalos[i][0], p)
            if amostra not in ocupadas
            and not any(inicio <= amostra < fim for inicio, fim in silencios)
        ]
        if not legitimas:
            raise erro(f"eventos[{i}]", "nonce_repetido precisa de uma leitura legítima antes para reenviar")


def _esperado(
    cenario: Cenario,
    evento: Evento,
    inicio: int,
    fim: int,
    janela: Janela | None,
    limites: Limites,
    erro,
) -> Esperado | None:
    servico, base, p = cenario.servico, cenario.base, cenario.periodo_amostra_seg

    if evento.tipo is Tipo.EXCURSAO_TEMPERATURA:
        t, ruido = evento.temperatura, base.ruido_temperatura
        if not (limites.temperatura_min <= t - ruido and t + ruido <= limites.temperatura_max):
            raise erro(f"temperatura {t} fora da faixa física: o oráculo rejeitaria, use valor_impossivel")
        # O degrau base→excursão aparece inteiro entre duas amostras; se passar
        # do admitido, o oráculo barra como variação impossível e a excursão
        # nunca chega ao contrato.
        degrau = abs(t - base.temperatura) + 2 * ruido
        admitido = limites.variacao_base_decimos + limites.max_variacao_decimos_por_seg * p
        if degrau > admitido:
            raise erro(f"degrau de {degrau} décimos excede a variação admitida pelo oráculo ({admitido})")
        if servico.temp_min <= t - ruido and t + ruido <= servico.temp_max:
            return Esperado.CONFORME
        if t + ruido < servico.temp_min or t - ruido > servico.temp_max:
            return Esperado.DESVIO_TEMPERATURA
        raise erro(f"temperatura {t} ± {ruido} cruza o limite da faixa: rótulo dependeria do ruído")

    if evento.tipo is Tipo.ABRIR_LACRE:
        if janela is None or not (janela.inicio < fim and inicio < janela.fim):
            return Esperado.VIOLACAO_LACRE
        if janela.contem(inicio, fim):
            return Esperado.CONFORME
        raise erro(
            f"lacre aberto em [{inicio}, {fim}) só em parte dentro da janela "
            f"[{janela.inicio}, {janela.fim}]: rótulo dependeria da agregação"
        )

    if evento.tipo is Tipo.SILENCIO:
        # A lacuna entre checkpoints é no mínimo a do silêncio mais 1 s, então
        # silêncio ≥ periodicidade sempre estoura. Abaixo disso depende de onde
        # caiu o último batimento, e o rótulo fica a cargo de quem escreveu.
        if evento.duracao >= servico.periodicidade_seg:
            return Esperado.NAO_OBSERVADO
        if evento.esperado not in (Esperado.CONFORME, Esperado.NAO_OBSERVADO):
            raise erro("silêncio só pode ser conforme ou nao_observado")
        return None

    if evento.tipo is Tipo.VALOR_IMPOSSIVEL and evento.modo == "salto":
        # Com amostragem lenta a variação admitida cresce e engole o salto,
        # que passaria como leitura legítima.
        admitido = limites.variacao_base_decimos + limites.max_variacao_decimos_por_seg * p
        if SALTO_IMPOSSIVEL <= admitido:
            raise erro(f"salto de {SALTO_IMPOSSIVEL} décimos cabe na variação admitida com período de {p} s")
    return Esperado.REJEITADO_ORACULO
