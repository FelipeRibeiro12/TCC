# oraculo — divisão 2

Simulador de dispositivo e serviço de oráculo do Claustrum. É a camada que
fica entre o mundo físico simulado e o `ShipmentEscrow`, e é onde mora a
contribuição do trabalho em relação à tese de Vilas Boas: lá o dispositivo
escrevia direto no contrato; aqui ele só **assina na origem**, e um oráculo
**qualifica** a leitura — assinatura, reapresentação, atualidade,
plausibilidade — antes de qualquer contato com a cadeia. O oráculo é o único
endereço autorizado a chamar `registrarCheckpoint`; a chave do dispositivo
nunca chega à cadeia.

É uma biblioteca Python importável, sem HTTP, sem CLI e sem gráfico. O runner
da divisão 3 importa os módulos, conduz o relógio e grava os CSVs de
resultado.

Responsável: Felipe Silveira (`felipera11`). Branch: `oraculo`.

---

## Sumário

1. [Estado atual](#estado-atual)
2. [Arquitetura e fluxo](#arquitetura-e-fluxo)
3. [Módulos](#módulos)
4. [Instalação](#instalação)
5. [Testes](#testes)
6. [Contas](#contas)
7. [Leitura e assinatura](#leitura-e-assinatura)
8. [Simulador de dispositivo](#simulador-de-dispositivo)
9. [Validação no oráculo](#validação-no-oráculo)
10. [Agregação em checkpoints](#agregação-em-checkpoints)
11. [Integração com a cadeia](#integração-com-a-cadeia)
12. [Cenários declarativos](#cenários-declarativos)
13. [Como usar o pacote](#como-usar-o-pacote)
14. [CSV de rejeições](#csv-de-rejeições)
15. [Decisões de projeto](#decisões-de-projeto)
16. [Pressupostos de segurança](#pressupostos-de-segurança)
17. [Pendências e limitações](#pendências-e-limitações)
18. [Histórico](#histórico)

---

## Estado atual

Tudo o que o `CLAUDE.md` pede da divisão 2 está implementado e testado:

- simulador que assina cada leitura na origem, com nonce sequencial e
  timestamp, e produz sob comando leitura normal, excursão de temperatura,
  abertura de lacre (dentro e fora de janela), silêncio e as três leituras
  maliciosas (assinatura inválida, nonce repetido, valor impossível);
- oráculo que confere assinatura, rejeita replay, checa atualidade e
  plausibilidade, agrega em checkpoint e só então chama
  `registrarCheckpoint`, registrando toda rejeição com o motivo;
- oito roteiros declarativos em `cenarios/`, cobrindo os cenários
  obrigatórios do `CLAUDE.md`, com o rótulo esperado de cada evento validado
  contra o que o roteiro de fato produz.

Verificação:

| O quê | Resultado |
|---|---|
| `pytest oraculo/tests`, sem nó | 283 passaram, 10 pulados (os `cadeia`) |
| `pytest oraculo/tests -m cadeia`, com `npx hardhat node` | 8 passaram, 2 pulados |
| Python 3.11.17 (stack oficial) e 3.14 | mesma suíte, mesmo resultado |

Os 2 pulados com nó são os testes ponta a ponta que dependem de
`abrirServico` abrir serviço de verdade; o `ShipmentEscrow` da divisão 1
ainda é esqueleto (estado em `74abf87`). Eles passam a rodar sozinhos quando
o contrato estiver pronto.

---

## Arquitetura e fluxo

```
 cenarios/*.json ──► cenario.carregar ──► Cenario (validado)
                                              │
                                              ▼
 RelogioVirtual ◄──────────────────────── Executor ── segundo a segundo
      │                                       │
      │                                       ▼
      │                                  Dispositivo  (conta 5)
      │                                  mede · assina · injeta maliciosos
      │                                       │  LeituraAssinada
      │                                       ▼
      │                              Oraculo.receber
      │                                       │
      │              ┌────────────────────────┴───────────────┐
      │              ▼                                        │
      │          Validador ── rejeitada ──► LogRejeicoes (CSV) + oraculo.rejeicoes
      │   assinatura → replay → atualidade → plausibilidade
      │              │ aceita
      │              ▼
      │          Agregador ── nada a enviar ──► leitura fica no lote
      │   primeira · faixa · lacre · batimento · bruto · final
      │              │ Checkpoint
      ▼              ▼
 sincronizar_no ─► registrarCheckpoint  (conta 4, a única autorizada)
                     │
                     ▼
              ResultadoTransacao ──► runner (div. 3) ──► transacoes.csv
```

O relógio é virtual e compartilhado por todos: uma rodada de dez minutos roda
em segundos, sempre igual, e o nó Hardhat é alinhado a ele antes de cada
transação, para que o contrato meça o tempo do roteiro e não o do relógio
real.

---

## Módulos

| Módulo | Papel |
|---|---|
| `relogio.py` | `RelogioVirtual` e `sincronizar_no` — o único relógio do pacote |
| `contas.py` | contas do Hardhat por índice e por papel |
| `assinatura.py` | `Leitura`, hash `keccak256` ABI-encoded, assinatura EIP-191 |
| `simulador.py` | `Dispositivo`: leituras assinadas na origem, eventos físicos e maliciosos |
| `oraculo.py` | `Validador`, `Limites`, `Motivo`, `Rejeicao` e `Oraculo` (valida, agrega, chama `registrarCheckpoint`) |
| `agregacao.py` | `Agregador`, `Checkpoint`, `Disparo`, `Modo`, `hash_lote` |
| `log_rejeicoes.py` | `LogRejeicoes`: CSV das leituras recusadas |
| `cenario.py` | carrega, valida e expande variantes dos roteiros de `cenarios/*.json` |
| `executor.py` | `Executor`: aplica um roteiro ao dispositivo, segundo a segundo |
| `cenarios/` | os oito roteiros (ver [Cenários declarativos](#cenários-declarativos)) |
| `tests/` | suíte `pytest`; os marcados `cadeia` precisam de nó local |

---

## Instalação

Requer Python 3.11 ou mais recente. A partir da raiz do repositório:

```bash
python3 -m venv oraculo/.venv
source oraculo/.venv/bin/activate
pip install -r oraculo/requirements.txt
```

Todas as dependências que o código importa diretamente estão fixadas em
`requirements.txt` (`web3`, `eth-account`, `eth-abi`, `eth-utils`,
`eth-keys`, `hexbytes`, `pytest`), inclusive as que antes só vinham como
dependência transitiva.

---

## Testes

A partir da raiz do repositório, com o venv ativo:

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

| Arquivo | Cobre |
|---|---|
| `test_relogio.py` | relógio só anda para frente; alinhamento do nó |
| `test_contas.py` | índice → papel, endereços determinísticos do mnemônico |
| `test_assinatura.py` | hash igual ao `abi.encode` do Solidity, assinatura e recuperação, assinatura malformada |
| `test_simulador.py` | ritmo de amostragem, excursão, lacre, silêncio, os três maliciosos, determinismo por semente |
| `test_validacao.py` | um teste por motivo de rejeição, ordem das checagens, nonce consumido, CSV |
| `test_agregacao.py` | cada disparo, pior valor da janela, hash do lote, batimento antecipado, descarga final |
| `test_oraculo.py` | ABI, `_resumo_revert`, serviço encerrado, rejeição em memória, latência sem o SLA |
| `test_cenarios.py` | forma e coerência dos roteiros, variantes, executor e **simulação seca** de todos os cenários pelo validador e pelo agregador |
| `test_integracao.py` (`cadeia`) | envio real, revert de chamador não autorizado, maliciosos nunca viram transação, rescisão, latência sem o alinhamento |

A simulação seca de `test_cenarios.py` roda cada cenário (e cada variante do
06) inteiro, sem cadeia, e confere que as rejeições batem com
`rejeicoes_esperadas()` e que só os silêncios abrem lacuna acima da
periodicidade. É a parte lenta da suíte (ECDSA em Python puro, ~1,5 min).

---

## Contas

Todas as chaves são derivadas do mnemônico público do Hardhat
(`test test test test test test test test test test test junk`, caminho
`m/44'/60'/0'/0/<índice>`). O índice define o papel:

| Índice | Papel |
|---|---|
| `0` | dono (implanta) |
| `1` | contratante |
| `2` | transportador |
| `3` | recebedor |
| `4` | oráculo — único remetente de `registrarCheckpoint` |
| `5` | dispositivo — só assina leituras, nunca envia transação |
| `9` | atacante — assina as leituras forjadas |

Nenhuma chave é lida de argumento de linha de comando ou de `.env`.

```python
from oraculo.contas import conta_do_papel
oraculo = conta_do_papel("oraculo")      # LocalAccount
```

---

## Leitura e assinatura

Cada leitura tem, nesta ordem e com estes tipos ABI:

```
address dispositivo
uint256 servicoId
uint64  nonce
uint64  timestamp
int16   temperatura   (décimos de grau: -180 = -18,0 °C)
uint8   umidade       (%)
bool    lacreAberto
```

- `hash_leitura = keccak256(abi.encode(...))` — o mesmo que o contrato
  calcularia, porque é ele que vai como `hashLeitura`. Por isso a mensagem é
  ABI-encoded com tipos fixos, e não JSON.
- `dispositivo` e `servicoId` entram na mensagem para que uma leitura de um
  serviço não possa ser reapresentada em outro.
- A assinatura é EIP-191 sobre o hash (`encode_defunct(primitive=hash)`), o
  formato que `ECDSA.toEthSignedMessageHash` reproduz em Solidity, caso a
  verificação um dia também seja feita em cadeia.
- Nenhum float e nenhum rótulo de evento na mensagem: o dispositivo só mede.
  Se a leitura é desvio ou violação, quem decide é o contrato.
- `celsius_para_decimos` converte com arredondamento decimal (meio para longe
  do zero), sem o erro de ponto flutuante.

---

## Simulador de dispositivo

`Dispositivo(conta, servico_id, relogio, periodo_amostra_seg, base, semente)`
amostra no próprio ritmo — no máximo uma leitura a cada
`periodo_amostra_seg`, mesmo que o relógio ande de segundo em segundo — e
assina cada uma com a própria chave. O ruído é sorteado de um gerador próprio
e semeado: mesma semente e mesmo roteiro dão exatamente as mesmas leituras e
assinaturas.

Os comandos descrevem o mundo físico, nunca o rótulo do evento: o dispositivo
não conhece a faixa do serviço nem a janela de lacre.

| Comando | Efeito |
|---|---|
| `excursao_temperatura(temp, duracao, inicio)` | temperatura vai para `temp` (± ruído) em `[inicio, inicio + duracao)` |
| `abrir_lacre(duracao, inicio)` | `lacreAberto = true` no intervalo |
| `silenciar(duracao, inicio)` | nenhuma emissão no intervalo — aparelho desligado |
| `assinatura_invalida(modo, inicio, quantidade)` | `outra_chave`: forjada pelo atacante com o próximo nonce; `adulterada`: leitura real assinada e o lacre invertido depois, em trânsito |
| `nonce_repetido(inicio, quantidade)` | reenvio byte a byte da última leitura legítima |
| `valor_impossivel(modo, inicio, quantidade)` | assinatura legítima com `temperatura` 120 °C, `umidade` 250 % ou `salto` de 40 °C em relação à anterior |

Regras de convivência:

- silêncio vence tudo: aparelho desligado não mede nem é imitado;
- os maliciosos contam em **amostras**, não em segundos — cada um toma o
  lugar de uma amostra a partir de `inicio`, para que o roteiro saiba
  exatamente quantas rejeições esperar;
- intervalos do mesmo tipo não podem se sobrepor (duas excursões ao mesmo
  tempo não têm leitura única possível);
- na forjada por `outra_chave` o contador do dispositivo não anda: quem
  forjou foi o atacante, e a próxima leitura legítima reusa aquele nonce.

---

## Validação no oráculo

O `Validador` checa nesta ordem, e a ordem faz parte da garantia:

1. **cadastro** — o serviço tem dispositivo cadastrado no oráculo;
2. **assinatura** — o signatário recuperado e o campo `dispositivo` são o
   aparelho cadastrado. Vem primeiro porque, sem ela, nada na leitura é
   confiável; validar o nonce antes deixaria um atacante queimar nonces do
   dispositivo com leituras forjadas;
3. **replay** — nonce estritamente crescente **por dispositivo** (não por
   serviço). O nonce é consumido logo aqui: uma leitura autêntica mas
   implausível não pode ser reapresentada depois;
4. **atualidade** — timestamp perto do relógio do oráculo e nunca anterior ao
   da última leitura aceita;
5. **plausibilidade** — faixa física absoluta e taxa de variação em relação à
   **última aceita** (nunca à última recebida, senão um salto rejeitado faria
   a volta ao normal parecer outro salto).

Limites padrão (`Limites`, trocáveis ao criar o `Oraculo`):

| Limite | Padrão | Por quê |
|---|---|---|
| temperatura | −40,0 a 85,0 °C | faixa de operação de sensores de contêiner refrigerado; fora dela é defeito ou fraude |
| umidade | 0 a 100 % | |
| variação admitida | `10,0 °C + 0,2 °C × Δt` | a parcela fixa absorve ruído e o degrau de uma excursão real; a por segundo, a deriva depois de um intervalo longo. 40 °C é barrado para qualquer período de amostra abaixo de 2,5 min |
| atraso tolerado | 30 s | o relógio é virtual, atraso real é zero; a tolerância existe para um dispositivo com relógio próprio |
| adiantamento tolerado | 5 s | leitura do futuro não tem explicação benigna além de deriva |

Toda rejeição vai para `oraculo.rejeicoes` (lista de `Rejeicao`: `leitura`,
`instante_recebimento`, `motivo`) e, se o `Oraculo` recebeu `log_rejeicoes`,
também para o CSV. Os motivos estão em [CSV de rejeições](#csv-de-rejeições).

---

## Agregação em checkpoints

Cada checkpoint custa gás, então o `Agregador` só escreve na cadeia quando
algo muda ou quando o contrato precisa de sinal de vida. Há um agregador por
serviço, criado na primeira leitura aceita com o SLA lido do contrato.

| `Disparo` | Quando |
|---|---|
| `primeira` | primeira leitura aceita do serviço |
| `faixa` | a temperatura mudou de condição (abaixo / dentro / acima) em relação ao **último checkpoint** — o estado que o contrato conhece; abaixo→acima também conta |
| `lacre` | o lacre mudou de estado em relação ao último checkpoint |
| `batimento` | a próxima amostra prevista passaria da periodicidade: `decorrido + último intervalo + margem_seg > periodicidadeSeg` |
| `bruto` | modo `bruto`: toda leitura aceita vira checkpoint (cenário 6) |
| `final` | `encerrar` / `descarregar`: o que sobrou no lote no fim da rodada |

Valor enviado:

- é sempre o **pior** da janela, nunca a média — 21 °C e 42 °C têm média
  dentro de muitas faixas, e a excursão sumiria. Acima da faixa, o máximo;
  abaixo, o mínimo; dentro, o mais perto de sair. Umidade: a máxima (risco de
  condensação). Lacre: aberto se alguma leitura estava aberta;
- o pior sai só das leituras na **mesma condição do gatilho**: numa troca de
  faixa, as leituras antigas são da condição que o contrato já conhece, e
  mandar o pior delas com o timestamp do gatilho faria a volta à faixa parecer
  mais tempo fora;
- `timestampLeitura` é o da leitura que disparou.

`hashLeitura`:

- modo `bruto`: `hash_leitura` da própria leitura;
- modo `agregado`: `hash_lote` = `keccak256` da concatenação dos hashes de
  todas as leituras do lote, em ordem. O lote inteiro fica em
  `Checkpoint.lote`, fora da cadeia: é com ele que um auditor recalcula o hash
  e confere o que foi resumido.

O agregador **nunca inventa checkpoint**. Se o dispositivo cala, nada sai, e
o intervalo chega ao contrato como lacuna entre dois checkpoints — é isso que
vira tempo não observado e impede que desligar o aparelho compense.

---

## Integração com a cadeia

- **SLA do contrato, não do roteiro.** Na primeira leitura aceita, o oráculo
  lê `tempMin`, `tempMax` e `periodicidadeSeg` com `verServico`. Se a divisão
  3 errar a faixa no cenário, o oráculo continua avaliando pelo que foi
  contratado. Serviço sem periodicidade levanta `ServicoSemSla`. Os campos são
  lidos pelos nomes da ABI, não pela ordem da struct.
- **Relógio alinhado.** Antes de cada transação, `sincronizar_no` chama
  `evm_setNextBlockTimestamp` com o instante virtual; o bloco sai carimbado
  com o tempo do roteiro.
- **Limite de gás fixo** (500 000) em vez de `estimate_gas`: a estimativa
  reverte antes de a transação existir, e a rejeição precisa ir para um bloco
  para que o gás gasto nela também seja medido (cenário 7).
- **Revert não derruba o experimento.** Volta `status="revert"` com gás e
  bloco preenchidos (em automine o Hardhat minera a transação revertida).
  `erro` traz só o motivo: o texto do `require`, o custom error
  (`Nome(...)`) ou `panic 0x..`.
- **Fim do serviço.** Quando um recibo do oráculo traz `ServicoRescindido` ou
  `ServicoConcluido`, o serviço entra em `oraculo.encerrados` e nenhuma
  leitura dele vai mais para a cadeia — todo `registrarCheckpoint` reverteria.
  A leitura ainda passa pelo validador, para que rejeição depois do fim
  continue no log. `enviar_checkpoint`, chamado direto, não é bloqueado:
  fica livre para o cenário 7 forçar um envio ilegal.
- **Latência.** Tempo real, da entrega da leitura ao oráculo até o recibo,
  incluindo validação e agregação. Ficam de fora o alinhamento do relógio do
  nó e a leitura única do SLA: os dois são artefatos do experimento ou
  preparação, não caminho do evento.

---

## Cenários declarativos

Roteiros em `oraculo/cenarios/*.json`. Tempos em segundos desde o início da
rodada; temperaturas em **décimos de grau**, como no contrato. Cada rodada tem
600 s, como no artigo.

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

Os quatro primeiros valores de `esperado` são o vocabulário de
`classificacao.csv`; `rejeitado_oraculo` marca o que o oráculo barra e nunca
chega ao contrato. A `janela` é o que a div. 3 passa para
`autorizarJanelaLacre`; o dispositivo não a vê.

**O rótulo é o gabarito da matriz de confusão**, então a validação vai além
da forma do JSON e recusa, com mensagem que aponta o campo:

- campo desconhecido ou ausente, tipo errado;
- evento fora da duração, ou que não cobre nenhuma amostra;
- período de amostra maior que a periodicidade do serviço;
- base com ruído fora da faixa do serviço;
- sobreposição de eventos (exceto excursão com lacre aberto, que é um cenário
  físico legítimo);
- mais de uma janela por cenário (o contrato guarda uma só);
- evento depois de uma violação de lacre (que rescinde o serviço);
- `nonce_repetido` sem leitura legítima anterior para reenviar;
- excursão com degrau que o oráculo barraria como variação impossível, ou com
  temperatura fora da faixa física;
- `salto` que caberia na variação admitida com o período de amostra escolhido;
- quando o rótulo dependeria de detalhe da agregação — excursão encostada no
  limite da faixa, lacre aberto só em parte dentro da janela — o roteiro é
  recusado em vez de rotulado no chute;
- `esperado` ou `motivo_esperado` diferente do que o roteiro de fato produz.

Toda variante de `varrer` passa pela mesma checagem.

| Arquivo | O que exercita | Cenário do `CLAUDE.md` |
|---|---|---|
| `01_conforme` | serviço sem violação — caso base de custo | 1 |
| `02_excursao_crescente` | excursões de 30, 60, 120 e 240 s a 12,0 °C (faixa 2,0–8,0 °C) | 2 |
| `03_lacre_dentro_janela` | abertura de 30 s dentro da janela autorizada | 3 |
| `04_lacre_fora_janela` | abertura fora da janela, rescisão | 4 |
| `05_indisponibilidade_crescente` | silêncios de 1×, 2×, 4× e 8× a periodicidade (30 s, amostra de 5 s) | 5 |
| `06_volume_telemetria` | período de amostra 1, 2, 5, 10, 30 s × modo `bruto`/`agregado` (10 variantes) | 6 |
| `07_rejeicoes` | assinatura inválida (dois modos), nonce repetido, valor e variação impossíveis | 7 (parte do oráculo) |
| `08_combinado` | excursão seguida de lacre fora de janela | combinação pedida pelo orientador |

As transições ilegais e o chamador não autorizado do cenário 7 são do runner
da divisão 3; o oráculo oferece `enviar_checkpoint` e aceita qualquer conta
em `conta_oraculo` para isso.

### Executor

`Executor(cenario, conta_dispositivo, servico_id, relogio, semente)` agenda o
roteiro no dispositivo e, ao ser iterado, avança o relógio de segundo em
segundo durante `duracao_seg`, produzindo uma `Emissao` a cada leitura:

| Campo | Conteúdo |
|---|---|
| `instante` | relógio virtual |
| `segundo` | desde o início da rodada, na escala do roteiro |
| `leitura` | `LeituraAssinada` |
| `eventos` | índices em `cenario.eventos` de que a leitura faz parte (vazio = leitura normal) |

O executor só produz leituras: não chama a cadeia, não grava CSV e não
autoriza janela. `janela_lacre()` devolve a janela já em instantes absolutos.

---

## Como usar o pacote

Interface pública da divisão 2, do ponto de vista de quem a importa.

### Exemplo de runner

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

from oraculo.cenario import DIRETORIO_CENARIOS, carregar
from oraculo.contas import conta_do_papel
from oraculo.executor import Executor
from oraculo.log_rejeicoes import LogRejeicoes
from oraculo.oraculo import Oraculo
from oraculo.relogio import RelogioVirtual, sincronizar_no

w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
cenario = carregar(DIRETORIO_CENARIOS / "04_lacre_fora_janela.json")


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
    # Um Oraculo novo por repetição: ele guarda o último nonce do dispositivo.
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

    # Com servico_encerrado (lacre fora de janela) o contrato já rescindiu:
    # não chame confirmarEntrega, que reverteria e sujaria transacoes.csv.
    if servico_id not in oraculo.encerrados:
        confirmar_entrega(servico_id)  # div. 3


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

Com `servico_encerrado=True` a leitura volta sem checkpoint nem transação.
Recomendado: sair do loop do executor nesse ponto. `encerrar` também não
envia nada para serviço encerrado.

`ResultadoTransacao` mapeia direto para uma linha de `transacoes.csv`:

| `transacoes.csv` | `ResultadoTransacao` |
|---|---|
| `funcao` | `funcao` (`registrarCheckpoint`) |
| `chamador` | o papel da `conta_oraculo` da instância: `oraculo`, ou o impostor que o cenário 7 usa para o chamador não autorizado |
| `gas_usado` | `gas_usado` |
| `bloco` | `bloco` |
| `latencia_ms` | `latencia_ms` — tempo real, da entrega da leitura ao recibo, sem o alinhamento do relógio do nó nem a leitura única do SLA |
| `status` | `status` (`ok` ou `revert`) |
| `erro` | `erro` — só o motivo: texto do `require` (`apenas oraculo`), custom error (`Nome(...)`) ou `panic 0x..` |
| `timestamp_envio` | `instante_injecao` — relógio virtual |

Todas as transações da instância também ficam em `oraculo.transacoes`, e
todas as rejeições em `oraculo.rejeicoes`.

O `Checkpoint` traz `disparo` e `lote`, as leituras assinadas que o
`hash_leitura` resume.

### Ligar emissão ao rótulo esperado

O `esperado` de cada evento em `emissao.eventos` é o `evento_injetado` do
`classificacao.csv`; o `evento_classificado` sai dos eventos emitidos pelo
contrato. Silêncio não gera emissão — o `nao_observado` aparece como
`TempoNaoObservado` no checkpoint seguinte. `cenario.rejeicoes_esperadas()`
lista as rejeições que o roteiro deve produzir, na ordem, para conferir contra
`rejeicoes.csv`.

### Cuidados

- **Serviço aberto antes do primeiro checkpoint.** O oráculo lê o SLA com
  `verServico` na primeira leitura aceita. Serviço sem periodicidade em cadeia
  levanta `ServicoSemSla`.
- **Um executor por repetição.** Ele carrega nonce e relógio; percorrer duas
  vezes levanta `RuntimeError`.
- **Um `Oraculo` por repetição.** Ele guarda o último nonce de cada
  dispositivo, e o dispositivo de uma repetição nova volta a contar do 1:
  reaproveitado, o oráculo rejeitaria toda leitura da segunda repetição em
  diante como `nonce_repetido`.
- **Cenário com `varrer`** (o 06) não roda direto: itere `variantes()`.
- **`margem_seg` do `Oraculo`** (padrão `0`) antecipa o batimento. Com `0`,
  uma leitura rejeitada exatamente no instante do batimento deixa a janela
  passar da periodicidade e o contrato conta tempo não observado. O cenário 07
  injeta fora dos múltiplos de 60 s por isso; em roteiros próprios, ou
  faça o mesmo, ou use `margem_seg=cenario.periodo_amostra_seg`.

---

## CSV de rejeições

Gravado por `LogRejeicoes(caminho, experimento, repeticao)`, uma linha por
leitura recusada. Sem `log_rejeicoes`, nada vai para disco, mas toda rejeição
continua em `oraculo.rejeicoes`. O arquivo é aberto em modo de acréscimo e
fechado a cada linha, então as 20 repetições podem usar o mesmo caminho e o
que já foi rejeitado sobrevive a uma queda do experimento; o cabeçalho só sai
se o arquivo for novo.

```
experimento,repeticao,servico_id,dispositivo,nonce,timestamp_leitura,instante_recebimento,motivo
```

`dispositivo` é o endereço que a leitura declara, não o recuperado da
assinatura: numa forjada, o que interessa é de quem o atacante tentou se
passar. Os dois instantes são do relógio virtual. Valores de `motivo`, na
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

---

## Decisões de projeto

Decisões tomadas ao longo da implementação, com o motivo, para a seção de
metodologia e para quem for mexer depois.

| Decisão | Por quê |
|---|---|
| Relógio virtual único, sem `time.time()` no pacote | uma rodada de 10 min roda em segundos e as 20 repetições são reproduzíveis |
| Nó alinhado ao relógio virtual antes de cada transação | o contrato mede tempo pelo bloco; sem isso, 10 min virariam segundos em cadeia |
| Mensagem ABI-encoded, hash keccak256, EIP-191 | o hash é o mesmo que o contrato calcularia e a assinatura é verificável em Solidity |
| Dispositivo sem conhecimento de faixa ou janela | quem classifica é o contrato; o dispositivo só relata o mundo físico |
| Maliciosos como ataques reais, não leituras etiquetadas | cada um precisa ser barrado pelo motivo próprio, e é isso que o oráculo prova |
| Assinatura antes do nonce; nonce consumido antes da plausibilidade | ninguém queima nonce do dispositivo forjando; leitura autêntica e implausível não volta |
| Nonce por dispositivo, não por serviço | o nonce é do aparelho; reaproveitado em outro serviço, não volta do zero |
| Variação comparada com a última **aceita** | um salto rejeitado não faz a volta ao normal parecer outro salto |
| Motivo extra `dispositivo_nao_cadastrado` | leitura de serviço que o oráculo não atende não tem contra o que verificar a assinatura |
| Pior valor da janela, nunca média | a média esconde a excursão |
| Pior valor só das leituras na condição do gatilho | evita que a volta à faixa pareça mais tempo fora |
| Hash do lote no modo agregado | o checkpoint ancora todas as leituras resumidas, auditáveis fora da cadeia |
| Batimento pela previsão da próxima amostra | com amostra de 7 s e periodicidade de 60 s, o batimento aos 63 s geraria 3 s de não observado a cada janela |
| Agregador nunca inventa checkpoint | silêncio precisa chegar ao contrato como tempo não observado |
| Descarga do lote no fim (`encerrar`) | sem ela, o fim da viagem não chegaria à cadeia |
| Parar de enviar depois de rescisão ou conclusão | todo envio reverteria, gastando gás e sujando o CSV com reverts que não são do cenário 7 |
| SLA lido do contrato, não do roteiro | o oráculo avalia pelo que foi contratado, mesmo se o roteiro errar |
| Limite de gás fixo | a rejeição vai para um bloco e o gás dela também é medido |
| Latência sem alinhamento do nó nem leitura do SLA | são artefatos do experimento, não caminho do evento |
| Uma janela de lacre por cenário | o `Servico` guarda uma só; uma segunda sobrescreveria a primeira |
| Rótulo validado contra o roteiro, e ambiguidade recusada | o rótulo é o gabarito da matriz de confusão |
| Simulação seca de todos os cenários na suíte | um roteiro que quebra no meio da rodada aparece no teste, não no experimento |
| Cenário 05 com amostra de 5 s e periodicidade de 30 s | os silêncios de 1×, 2×, 4× e 8× a periodicidade cabem nos 600 s |

---

## Pressupostos de segurança

Como na Seção IV do artigo, a proposta desloca a confiança para dois
componentes, com limites declarados:

- **Dispositivo não comprometido.** A assinatura na origem prova procedência e
  detecta alteração, mas não atesta que o valor medido é verdadeiro. Assume-se
  que o dispositivo não foi violado fisicamente e que a chave não foi
  extraída; quem tem acesso físico ao equipamento produz leitura falsa e
  validamente assinada, e esse vetor está fora do escopo. A checagem de
  plausibilidade só reduz essa superfície.
- **Oráculo desinteressado.** O serviço de oráculo é operado por parte sem
  interesse no resultado da liquidação. Ele é o único endereço autorizado a
  chamar `registrarCheckpoint`, e a chave do dispositivo nunca chega à cadeia.

A queda do oráculo é tratada como a do dispositivo: o intervalo sem
checkpoint vira tempo não observado no contrato, qualquer que seja a causa.

---

## Pendências e limitações

O que a divisão 2 ainda não fecha sozinha, porque depende de resposta de
outra divisão ou do grupo.

- **Medida de tempo no contrato.** O agregador funciona com
  `timestampLeitura` ou `block.timestamp`, mas o tempo fora da faixa e não
  observado que resulta de cada checkpoint muda conforme a escolha. Falta a
  resposta da divisão 1.
- **Ponta a ponta com contrato real.** Enquanto o `ShipmentEscrow` for
  esqueleto, os 2 testes ponta a ponta ficam pulados; a fumaça foi feita com o
  SLA do roteiro injetado no agregador.
- **Segundos esperados por evento.** O cenário traz o rótulo esperado, mas não
  os segundos esperados fora da faixa ou sem observação, necessários para a
  penalidade esperada. Dependem da grade de amostragem (divisão 2) e da medida
  de tempo acima.
- **`hashLeitura` no modo agregado** é o hash do lote, não o de uma leitura
  só, como diz o texto da interface no `CLAUDE.md`. Precisa ser avisado ao
  grupo.
- **Falha do oráculo e atraso de rede.** A Seção IV.B do artigo diz que a
  instrumentação distingue essas causas da queda do dispositivo, mas o formato
  de cenário só tem `silencio`. Decidir se entra como tipo de evento novo ou se
  a frase do artigo muda.
- **`margem_seg`** fica em `0` por padrão; `margem_seg = periodo_amostra_seg`
  evita tempo não observado espúrio quando uma leitura é rejeitada no
  instante do batimento.

**Limitações conhecidas:**

- Rede local apenas; latência em rede de teste pública está fora desta fase.
- O dispositivo é simulado; a acurácia medida é a da classificação dos eventos
  injetados, não a de um sensor físico.

---

## Histórico

Todos os commits na branch `oraculo`, sobre a `main`:

| Tarefa | Commit | O quê |
|---|---|---|
| T1 | `d4e6293` | base do pacote, relógio virtual e contas do Hardhat |
| T2 | `feb6c05` | assinatura da leitura: hash ABI-encoded e EIP-191 |
| T3 | `75bc55c` | simulador com eventos físicos e leituras maliciosas |
| T4 | `31ff396` | validação no oráculo e CSV de rejeições |
| T5 | `a6dea7a` | agregação em checkpoints com pior valor da janela |
| T6 | `1f6cbe0` | integração com `registrarCheckpoint` e alinhamento do nó |
| T7 | `0b52111` | cenários declarativos, validação e executor |
| T8 | `8ca590b` | README para a divisão 3 |
| T9 | `126b368` | descarga do lote final |
| T10 | `417b9b5` | parar de enviar depois de rescisão ou conclusão |
| T11 | `094fac5` | simulação seca de todos os cenários na suíte |
| T12 | `5c245c8` | `nonce_repetido` sem leitura legítima anterior é recusado no roteiro |
| T13 | `b6a53ce` | latência sem o alinhamento do nó |
| T14 | `32ab6f2` | dependências fixadas, revert de custom error e panic, caminhos do README |
| auditoria | `004d392` | rejeições em memória sem CSV, latência sem a leitura do SLA |
| T15 | — | suíte verificada em Python 3.11.17, sem mudança de código |
