# oraculo — divisão 2

Simulador de dispositivo e serviço de oráculo do Claustrum. É uma biblioteca
Python importável: não tem HTTP nem CLI. O runner da divisão 3 importa os
módulos, conduz o relógio e grava os CSVs de resultado.

| Módulo | Papel |
|---|---|
| `relogio.py` | `RelogioVirtual` e `sincronizar_no` — o único relógio do pacote |
| `contas.py` | contas do Hardhat por índice e por papel |
| `assinatura.py` | `Leitura`, hash `keccak256` ABI-encoded, assinatura EIP-191 |
| `simulador.py` | `Dispositivo`: leituras assinadas na origem, eventos físicos e maliciosos |
| `oraculo.py` | `Validador` (assinatura → replay → atualidade → plausibilidade) e `Oraculo` (valida, agrega, chama `registrarCheckpoint`) |
| `agregacao.py` | `Agregador`: checkpoint por evento + batimento, pior valor da janela |
| `log_rejeicoes.py` | CSV das leituras recusadas |
| `cenario.py` | carrega e valida os roteiros de `cenarios/*.json` |
| `executor.py` | aplica um roteiro ao dispositivo, segundo a segundo |

## Instalação

Requer Python 3.11 ou mais recente. A partir da raiz do repositório:

```bash
python3 -m venv oraculo/.venv
source oraculo/.venv/bin/activate
pip install -r oraculo/requirements.txt
```

## Testes

Também a partir da raiz do repositório, com o venv ativo:

```bash
pytest oraculo/tests
```

Os testes marcados `cadeia` precisam de um nó Hardhat local com os contratos
compilados; sem nó, são pulados. Em outro terminal, a partir de `contracts/`:

```bash
npm install --no-package-lock
npx hardhat compile
npx hardhat node
```

e então `pytest oraculo/tests -m cadeia`. O teste ponta a ponta
(`test_receber_leitura_valida_registra_checkpoint`) é pulado enquanto o
`ShipmentEscrow` for esqueleto e `abrirServico` não abrir serviço.

## Contas

Todas as chaves são derivadas do mnemônico público do Hardhat
(`oraculo/contas.py`). O índice define o papel: `0` dono, `1` contratante,
`2` transportador, `3` recebedor, `4` oráculo, `5` dispositivo. O índice `9`
é o atacante que assina as leituras forjadas. Nenhuma chave é lida de
argumento de linha de comando ou de `.env`.

```python
from oraculo.contas import conta_do_papel
oraculo = conta_do_papel("oraculo")      # LocalAccount
```

## Exemplo de runner

Esqueleto de uma repetição de um cenário. O que está marcado `div. 3` é
responsabilidade do runner — implantar, abrir e aceitar o serviço, autorizar a
janela, confirmar a entrega, classificar pelos eventos e gravar os CSVs de
resultado. O oráculo não escreve `transacoes.csv` nem `classificacao.csv`.

O pacote é importado como `oraculo`, então a raiz do repositório precisa
estar no caminho: rode o runner a partir dela como módulo
(`python -m experimentos.runner`) ou com `PYTHONPATH=.`. Os caminhos de
`abi/` e `cenarios/` são resolvidos pelo pacote, não pelo diretório atual.

```python
from web3 import Web3

from oraculo.cenario import carregar
from oraculo.contas import conta_do_papel
from oraculo.executor import Executor
from oraculo.log_rejeicoes import LogRejeicoes
from oraculo.oraculo import Oraculo
from oraculo.relogio import RelogioVirtual, sincronizar_no

w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
cenario = carregar("oraculo/cenarios/04_lacre_fora_janela.json")


def rodar(cenario, repeticao: int, endereco_escrow: str) -> None:
    # O nó só anda para frente; o relógio parte depois do último bloco para
    # que sincronizar_no consiga carimbar os blocos com o instante virtual.
    ultimo = w3.eth.get_block("latest")["timestamp"]
    relogio = RelogioVirtual(ultimo + 100)

    # div. 3: chamar sincronizar_no(w3, relogio) antes de cada transação
    # própria, abrir o serviço com o SLA de cenario.servico e aceitá-lo.
    servico_id = abrir_e_aceitar(cenario.servico, relogio)

    log = LogRejeicoes(
        "experimentos/resultados/rejeicoes.csv", cenario.nome, repeticao
    )
    oraculo = Oraculo(
        w3,
        conta_oraculo=conta_do_papel("oraculo"),
        endereco_escrow=endereco_escrow,
        relogio=relogio,
        dispositivos_por_servico={servico_id: conta_do_papel("dispositivo").address},
        log_rejeicoes=log,
        modo_agregacao=cenario.modo_agregacao,
    )
    # O executor nasce no segundo 0 do roteiro: crie depois de abrir o
    # serviço. Semente diferente por repetição, senão as 20 são idênticas.
    executor = Executor(
        cenario,
        conta_do_papel("dispositivo"),
        servico_id,
        relogio,
        semente=cenario.semente + repeticao,
    )

    janela = executor.janela_lacre()  # já em instantes absolutos
    if janela is not None:
        autorizar_janela(servico_id, janela.inicio, janela.fim)  # div. 3

    for emissao in executor:
        # O relógio está parado no instante da leitura: entregar agora, e
        # não depois de percorrer tudo, ou a leitura sai desatualizada.
        resultado = oraculo.receber(emissao.leitura)
        esperados = [cenario.eventos[i].esperado for i in emissao.eventos]
        if not resultado.aceita:
            ...  # rejeitada; o motivo já foi para rejeicoes.csv
        elif resultado.transacao is not None:
            ...  # div. 3: linha em transacoes.csv (ver abaixo)
        if resultado.servico_encerrado:
            # O contrato rescindiu (lacre fora de janela) ou concluiu: o
            # oráculo não envia mais nada, então não há o que medir adiante.
            break

    # Manda o que ficou no lote depois do último checkpoint; sem isso, o fim
    # da viagem não chega à cadeia. Devolve None se não havia nada pendente
    # ou se o serviço já terminou em cadeia.
    final = oraculo.encerrar(servico_id)
    if final is not None:
        ...  # div. 3: linha em transacoes.csv, como as do loop

    if servico_id not in oraculo.encerrados:
        confirmar_entrega(servico_id)  # div. 3; rescindido, reverteria


for variante in cenario.variantes():  # cenários com "varrer" expandem aqui
    for repeticao in range(1, 21):
        endereco_escrow = implantar()  # div. 3: contratos novos por repetição
        rodar(variante, repeticao, endereco_escrow)
```

### O que volta de `Oraculo.receber`

`ResultadoRecebimento`:

| Campo | Conteúdo |
|---|---|
| `aceita` | `True` se passou pelo validador |
| `motivo` | `Motivo` da rejeição, ou `None` |
| `checkpoint` | `Checkpoint` enviado, ou `None` se a leitura só entrou no lote |
| `transacao` | `ResultadoTransacao` do envio, ou `None` |
| `servico_encerrado` | `True` se o contrato já emitiu `ServicoRescindido` ou `ServicoConcluido` para o serviço |

Depois que um recibo do oráculo traz `ServicoRescindido` ou
`ServicoConcluido`, o serviço entra em `oraculo.encerrados` e nenhuma leitura
dele vai mais para a cadeia: todo `registrarCheckpoint` reverteria, gastando
gás e pondo em `transacoes.csv` linhas `revert` que não são as do cenário 7.
A leitura continua passando pelo validador — rejeição depois do fim ainda vai
para `rejeicoes.csv` —, mas volta sem checkpoint nem transação e com
`servico_encerrado=True`. Recomendado: sair do loop do executor nesse ponto.
`encerrar` também não envia nada para serviço encerrado. `enviar_checkpoint`,
chamado direto pelo runner, não é bloqueado: fica livre para o cenário 7
forçar um envio ilegal de propósito.

`ResultadoTransacao` mapeia direto para uma linha de `transacoes.csv`:

| `transacoes.csv` | `ResultadoTransacao` |
|---|---|
| `funcao` | `funcao` (`registrarCheckpoint`) |
| `chamador` | sempre `oraculo` |
| `gas_usado` | `gas_usado` |
| `bloco` | `bloco` |
| `latencia_ms` | `latencia_ms` — tempo real, da entrega da leitura ao recibo |
| `status` | `status` (`ok` ou `revert`) |
| `erro` | `erro` — só o texto do `require`, ex. `apenas oraculo` |
| `timestamp_envio` | `instante_injecao` — relógio virtual |

Revert não levanta exceção: volta com `status="revert"` e, como o Hardhat em
automine minera a transação revertida, com gás e bloco preenchidos. Todas as
transações da instância também ficam em `oraculo.transacoes`.

O `Checkpoint` traz `disparo` (`primeira`, `faixa`, `lacre`, `batimento`,
`bruto`, `final` — este último do `encerrar`) e `lote`, as leituras assinadas que o `hash_leitura` resume — é com
ele que se confere fora da cadeia o que foi agregado.

### Ligar emissão ao rótulo esperado

Cada `Emissao` do executor traz `instante`, `segundo` (desde o início da
rodada), `leitura` e `eventos`: os índices em `cenario.eventos` de que a
leitura faz parte (vazio = leitura normal). O `esperado` de cada evento é o
`evento_injetado` do `classificacao.csv`; o `evento_classificado` sai dos
eventos emitidos pelo contrato. Silêncio não gera emissão — o
`nao_observado` aparece como `TempoNaoObservado` no checkpoint seguinte.
`cenario.rejeicoes_esperadas()` lista as rejeições que o roteiro deve
produzir, na ordem, para conferir contra `rejeicoes.csv`.

### Cuidados

- **Serviço aberto antes do primeiro checkpoint.** O oráculo lê o SLA
  (`tempMin`, `tempMax`, `periodicidadeSeg`) com `verServico` na primeira
  leitura aceita. Serviço sem periodicidade em cadeia levanta `ServicoSemSla`.
- **Um executor por repetição.** Ele carrega nonce e relógio; percorrer duas
  vezes levanta `RuntimeError`.
- **Cenário com `varrer`** (o 06) não roda direto: itere `variantes()`.
- **`margem_seg` do `Oraculo`** (padrão `0`) antecipa o batimento. Com `0`,
  uma leitura rejeitada exatamente no instante do batimento deixa a janela
  passar da periodicidade e o contrato conta tempo não observado. O cenário 07
  injeta fora dos múltiplos de 60 s por isso; em roteiros próprios, ou
  faça o mesmo, ou use `margem_seg=cenario.periodo_amostra_seg`.

## Formato do cenário

Roteiros em `oraculo/cenarios/*.json`. Tempos em segundos desde o início da
rodada; temperaturas em **décimos de grau**, como no contrato.

```json
{
  "nome": "04_lacre_fora_janela",
  "duracao_seg": 600,
  "periodo_amostra_seg": 10,
  "servico": { "tempMin": 20, "tempMax": 80, "periodicidadeSeg": 60 },
  "base": { "temperatura": 50, "umidade": 60 },
  "semente": 42,
  "eventos": [
    { "inicio": 300, "tipo": "abrir_lacre", "duracao": 30,
      "janela": { "inicio": 100, "fim": 160 }, "esperado": "violacao_lacre" }
  ]
}
```

| Campo | Obrigatório | Observação |
|---|---|---|
| `nome`, `duracao_seg`, `periodo_amostra_seg`, `semente` | sim | |
| `servico` | sim | `tempMin`, `tempMax`, `periodicidadeSeg` — o que a div. 3 passa para `abrirServico` |
| `base` | sim | `temperatura`, `umidade`; opcionais `ruido_temperatura` (2), `ruido_umidade` (1) |
| `eventos` | sim | lista, pode ser vazia |
| `modo_agregacao` | não | `agregado` (padrão) ou `bruto` |
| `varrer` | não | listas de `periodo_amostra_seg` e/ou `modo_agregacao`; cada combinação vira uma variante |

Eventos — todos têm `inicio`, `tipo` e `esperado`:

| `tipo` | Campos | `esperado` |
|---|---|---|
| `excursao_temperatura` | `duracao`, `temperatura` | `conforme` ou `desvio_temperatura` |
| `abrir_lacre` | `duracao`, `janela` opcional (`inicio`, `fim`) | `conforme` (dentro) ou `violacao_lacre` (fora) |
| `silencio` | `duracao` | `conforme` ou `nao_observado` |
| `assinatura_invalida` | `modo` (`outra_chave`, `adulterada`), `quantidade`, `motivo_esperado` | `rejeitado_oraculo` |
| `nonce_repetido` | `quantidade`, `motivo_esperado` | `rejeitado_oraculo` |
| `valor_impossivel` | `modo` (`temperatura`, `umidade`, `salto`), `quantidade`, `motivo_esperado` | `rejeitado_oraculo` |

Os maliciosos contam em amostras (`quantidade`, padrão 1), não em segundos:
cada um toma o lugar de uma amostra a partir de `inicio`. A `janela` é o que a
div. 3 passa para `autorizarJanelaLacre`; o dispositivo não a vê.

A validação recusa, com mensagem que aponta o campo: campo desconhecido ou
ausente, evento fora da duração, sobreposição de eventos (exceto excursão com
lacre aberto), mais de uma janela por cenário (o contrato guarda uma só),
evento depois de uma violação de lacre (que rescinde o serviço), e `esperado`
ou `motivo_esperado` diferente do que o roteiro de fato produz — o rótulo é o
gabarito da matriz de confusão, então um rótulo errado não passa.

| Arquivo | O que exercita |
|---|---|
| `01_conforme` | serviço sem violação — caso base de custo |
| `02_excursao_crescente` | excursões de 30, 60, 120 e 240 s |
| `03_lacre_dentro_janela` | abertura autorizada, sem penalidade |
| `04_lacre_fora_janela` | abertura fora de janela, rescisão |
| `05_indisponibilidade_crescente` | silêncios de 1×, 2×, 4× e 8× a periodicidade |
| `06_volume_telemetria` | período de amostra 1–30 s × modo `bruto`/`agregado` |
| `07_rejeicoes` | assinatura inválida, nonce repetido, valor e variação impossíveis |
| `08_combinado` | excursão seguida de lacre fora de janela |

## CSV de rejeições

Gravado por `LogRejeicoes(caminho, experimento, repeticao)`, uma linha por
leitura recusada. O arquivo é aberto em modo de acréscimo, então as 20
repetições podem usar o mesmo caminho; o cabeçalho só sai se o arquivo for
novo.

```
experimento,repeticao,servico_id,dispositivo,nonce,timestamp_leitura,instante_recebimento,motivo
```

`dispositivo` é o endereço que a leitura declara, não o recuperado da
assinatura. Os dois instantes são do relógio virtual. Valores de `motivo`, na
ordem em que o validador checa:

| `motivo` | Quando |
|---|---|
| `dispositivo_nao_cadastrado` | serviço sem dispositivo cadastrado no oráculo |
| `assinatura_invalida` | signatário não é o dispositivo do serviço, ou leitura adulterada |
| `nonce_repetido` | nonce não maior que o último aceito do dispositivo |
| `timestamp_desatualizado` | leitura mais de 30 s atrás do relógio |
| `timestamp_futuro` | leitura mais de 5 s à frente do relógio |
| `timestamp_regressivo` | timestamp anterior ao da última leitura aceita |
| `valor_impossivel` | fora de −40,0 a 85,0 °C ou de 0–100 % de umidade |
| `variacao_impossivel` | salto maior que `10,0 °C + 0,2 °C × Δt` desde a última aceita |

Os limites são os padrões de `oraculo.Limites` e podem ser trocados ao
instanciar o `Oraculo`. O nonce é consumido assim que a assinatura confere:
uma leitura autêntica mas implausível não pode ser reapresentada.

## Pressupostos de segurança

Como na Seção IV do artigo, a proposta desloca a confiança para dois
componentes, com limites declarados:

- **Dispositivo não comprometido.** A assinatura na origem prova procedência e
  detecta alteração, mas não atesta que o valor medido é verdadeiro. Assume-se
  que o dispositivo não foi violado fisicamente e que a chave não foi
  extraída; quem tem acesso físico ao equipamento produz leitura falsa e
  validamente assinada, e esse vetor está fora do escopo.
- **Oráculo desinteressado.** O serviço de oráculo é operado por parte sem
  interesse no resultado da liquidação. Ele é o único endereço autorizado a
  chamar `registrarCheckpoint`, e a chave do dispositivo nunca chega à cadeia.

A queda do oráculo é tratada como a do dispositivo: o intervalo sem
checkpoint vira tempo não observado no contrato, qualquer que seja a causa.
