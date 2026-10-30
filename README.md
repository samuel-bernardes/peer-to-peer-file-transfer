# peer-to-peer-file-transfer

Trabalho Prático 2 para disciplina de Sistemas Distribuídos na graduação em
Engenharia de Computação. O objetivo deste trabalho é implementar um sistema
elementar de transferência de arquivos no modelo Peer-to-Peer, em que cada nó
é ao mesmo tempo cliente e servidor, fragmenta o arquivo em blocos e os troca
diretamente com os vizinhos.

## Ambiente

Python 3.9 ou mais novo, usando apenas a biblioteca padrão (`socket`,
`threading`, `json`, `hashlib`). Funciona em Linux, macOS e Windows. O script de
gráficos precisa também do `matplotlib`.

No Windows, se o comando `python` abrir a Microsoft Store, use o iniciador `py`.

## Uso

Primeiro gera-se o arquivo de metadados a partir do arquivo original:

```bash
python -c "import os; open('original.bin','wb').write(os.urandom(10240))"
python peer.py meta original.bin --block-size 1024 --output file.meta.json
```

Depois cada peer roda em um terminal. Só o seeder recebe `--seed`:

```bash
# terminal 1 — seeder
python peer.py run --port 9001 --meta file.meta.json --dir dados_1 --seed original.bin \
    --neighbor 127.0.0.1:9002 --neighbor 127.0.0.1:9003

# terminal 2 — leecher
python peer.py run --port 9002 --meta file.meta.json --dir dados_2 \
    --neighbor 127.0.0.1:9001 --neighbor 127.0.0.1:9003

# terminal 3 — leecher
python peer.py run --port 9003 --meta file.meta.json --dir dados_3 \
    --neighbor 127.0.0.1:9001 --neighbor 127.0.0.1:9002
```

| Argumento | Significado |
|---|---|
| `--port` | porta em que o peer escuta |
| `--host` | endereço em que escuta (padrão `127.0.0.1`; use `0.0.0.0` entre máquinas) |
| `--neighbor IP:porta` | vizinho estático, pode repetir |
| `--meta` | arquivo de metadados |
| `--dir` | pasta onde o peer guarda os blocos e o arquivo remontado |
| `--seed` | arquivo completo, só no seeder inicial |
| `--verbose` | registra cada mensagem `HAVE`, `GET` e `BLOCK` |

Ao terminar, o leecher grava `dados_N/original.bin`, confere tamanho e SHA-256 e
imprime no log quantos blocos recebeu de cada vizinho. Ele continua servindo
blocos até `Ctrl+C`.

## Funcionamento

- **Metadados** (JSON): nome, tamanho, tamanho de bloco, SHA-256 do arquivo e
  SHA-256 de cada bloco.
- **Protocolo** (TCP): cada mensagem é
  `[4 bytes: tamanho do cabeçalho][cabeçalho JSON][4 bytes: tamanho do payload][payload]`.
  Há três tipos: `HAVE` (pergunta/resposta com a lista de blocos que o peer tem),
  `GET` (pede um bloco pelo índice) e `BLOCK` (resposta com os bytes do bloco).
- **Simetria**: uma thread aceita conexões e cria uma thread para cada conexão
  recebida; a thread principal, enquanto isso, baixa os blocos. A conexão com cada vizinho é
  reaproveitada para todos os pedidos.
- **Seleção de blocos**: *rarest-first* — primeiro os blocos com menos donos
  entre os vizinhos. O inventário (`HAVE`) é atualizado a cada 256 blocos
  recebidos, e os pedidos são distribuídos em rodízio entre os donos de cada bloco.
- **Tornar-se seeder**: cada bloco recebido é validado pelo hash e gravado no
  arquivo de dados; a partir daí já é anunciado no `HAVE` e servido no `GET`.
- **Remontagem**: os blocos ficam em um único arquivo pré-alocado
  (`<sha256>.data`), na posição `índice × tamanho_do_bloco`. No fim ele é
  copiado para o nome original e verificado. Se o peer for reiniciado, os blocos
  já baixados são recuperados conferindo os hashes.

## Estudo de caso

```bash
python test_cases.py                 # teste rápido: 2 e 4 peers, bloco de 1 KB, arquivo de 10 KB
python test_cases.py --full          # as 24 combinações da tabela do enunciado

# o que foi usado no relatório: 5 repetições, CSV e logs
python test_cases.py --full --repeats 5 --csv results/results.csv --logs results/logs
python scripts/plot_results.py
```

O script cria um arquivo aleatório, gera os metadados, sobe os peers (o `peer0`
é o seeder e todos são vizinhos de todos), espera os downloads, compara tamanho
e SHA-256 de cada cópia com o original e anota o tempo e a origem dos blocos de
cada leecher.

O `plot_results.py` lê o CSV e gera `results/tempo_download.png`,
`results/fontes_blocos.png` e a tabela `results/resumo.csv`. Os logs da primeira
execução de cada cenário ficam em `results/logs/`.

## Organização

```
peer.py                  peer: metadados, protocolo, servidor, download e remontagem
test_cases.py            executa os cenários do enunciado e verifica integridade
scripts/plot_results.py  gráficos e tabela-resumo do estudo de caso
results/                 CSV, gráficos e logs da execução usada no relatório
```

O protocolo é didático e **não tem autenticação nem criptografia**; use apenas
em uma rede de testes confiável.
