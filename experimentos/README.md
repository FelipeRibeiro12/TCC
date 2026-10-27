# Experimentos locais

## Pré-requisitos

Instale o Node.js LTS, que inclui `npm`, e abra um terminal novo. Confirme que
os dois comandos estão no `PATH`:

```powershell
node --version
npm --version
```

Se o PowerShell disser que `npm` não é reconhecido, o Node.js não está
instalado ou o terminal ainda não atualizou o `PATH`. Resolva isso antes de
continuar.

Com o nó Hardhat em execução e os contratos compilados, o runner implanta
`AssetTwin` e `ShipmentEscrow` novamente em cada repetição. Assim, cada rodada
começa com estado de contrato independente.

```powershell
cd contracts
npm install
npm run compile
npm run node
```

Em outro terminal, na raiz do projeto:

```powershell
python -m pip install -r oraculo/requirements.txt
python -m experimentos.runner
```

Sem argumentos, todos os cenários e variantes são executados 20 vezes.
Para uma execução curta de validação, selecione um cenário e reduza as
repetições:

```powershell
python -m experimentos.runner 01_conforme --repeticoes 1
```

Os CSVs são acrescentados em `experimentos/resultados/`. O runner também
atualiza `abi/enderecos.json` com os endereços da implantação mais recente.
As leituras rejeitadas pelo oráculo ficam em `rejeicoes.csv`, separadas da
matriz de classificação de eventos que chegaram à cadeia.
