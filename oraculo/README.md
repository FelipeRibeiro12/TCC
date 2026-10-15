# oraculo — divisão 2

Simulador de dispositivo e serviço de oráculo do Claustrum.

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

## Contas

Todas as chaves são derivadas do mnemônico público do Hardhat
(`oraculo/contas.py`). O índice define o papel: `0` dono, `1` contratante,
`2` transportador, `3` recebedor, `4` oráculo, `5` dispositivo. Nenhuma chave
é lida de argumento de linha de comando ou de `.env`.
