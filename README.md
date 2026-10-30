# TP2 - Transferência de Arquivos Peer-to-Peer

Implementação em Python 3.9+ utilizando apenas a biblioteca padrão (sockets TCP, threads, JSON e SHA-256).

## Funcionamento

- Cada peer abre um servidor TCP, atende `HAVE` (lista de blocos disponíveis) e `GET` (envio de bloco) e, ao mesmo tempo, consulta peers vizinhos para baixar blocos.
- Metadados JSON: nome, tamanho original, tamanho de bloco, SHA-256 do arquivo e SHA-256 de cada bloco.
- Protocolo TCP: `[4 bytes de comprimento JSON (big-endian)][JSON UTF-8][4 bytes de comprimento payload][payload]`.
- Assim que um bloco é verificado e armazenado, outros peers podem baixá-lo. Blocos são persistidos em `diretorio/<hash>.parts/`.
- Seleção **rarest-first** para o próximo bloco disponível. Vizinhos são estáticos via argumentos `--neighbor`.
- Após o download, remonta, confere tamanho e SHA-256 total e continua servindo blocos até `Ctrl+C`.

## Exemplo local: 3 terminais

Crie arquivo de teste e metadados:

```bash
python -c "import os; open('original.bin','wb').write(os.urandom(10240))"
python peer.py meta original.bin --block-size 1024 --output file.meta.json
```

Terminal 1 (seeder):

```bash
python peer.py run --port 9001 --meta file.meta.json --dir dados_seed --seed original.bin --neighbor 127.0.0.1:9002 --neighbor 127.0.0.1:9003
```

Terminal 2 (leecher):

```bash
python peer.py run --port 9002 --meta file.meta.json --dir dados_2 --neighbor 127.0.0.1:9001 --neighbor 127.0.0.1:9003
```

Terminal 3 (leecher):

```bash
python peer.py run --port 9003 --meta file.meta.json --dir dados_3 --neighbor 127.0.0.1:9001 --neighbor 127.0.0.1:9002
```

Ao final, confira `dados_2/original.bin` e `dados_3/original.bin`.

## Testes automatizados

```bash
python test_cases.py          # teste rápido com 2 e 4 peers, 1 KB de bloco, arquivo de 10 KB
python test_cases.py --full   # 24 combinações com todos os tamanhos e configurações da tabela
```

O script cria arquivos aleatórios, inicializa processos, verifica tamanho e hash de cada cópia e mostra o resultado. Use `--timeout 180` caso o computador seja lento. Para relatório com tempos, medições, logs e comparação entre fontes, estenda o script conforme desejado.

## Rede entre computadores

Passe `--host 0.0.0.0` para escutar em todas as interfaces e configure os `--neighbor` com os IPs reais dos demais computadores. O protocolo é didático e **não implementa autenticação nem criptografia**, portanto use apenas numa rede de testes confiável.

## Windows / PowerShell

Se o comando `python` abrir a Microsoft Store, use o iniciador `py`:

```powershell
py test_cases.py
py test_cases.py --full
```

Execute os comandos na pasta que contem `test_cases.py` (confira com `dir`).

### Ajuste de conexoes

A versao atual reutiliza conexoes TCP com os vizinhos e atualiza o inventario de blocos a cada 64 transferencias, evitando milhares de conexoes de curta duracao e consultas repetidas de HAVE. Testes de 5 MB (2 e 4 peers, 1024 B) e 20 MB (2 peers, 4096 B) foram executados com sucesso no ambiente de desenvolvimento. Execute os testes novamente no Windows para validar nesse sistema.
