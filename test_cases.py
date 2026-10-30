"""Executa cenarios do enunciado com peers locais e confere SHA-256."""
import argparse
import csv
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).parent
PYTHON = sys.executable
CSV_FIELDS = ['peers', 'block_size', 'file_size', 'run', 'peer', 'ok', 'elapsed_s', 'sources']


def checksum(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def free_ports(count):
    sockets = []
    for _ in range(count):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sockets.append(sock)
    ports = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    return ports


def parse_log(text, ports):
    """Extrai o tempo de download e a quantidade de blocos recebida de cada peer de origem."""
    elapsed = re.search(r'CONCLUIDO .* tempo=([0-9.]+)s', text)
    sources = re.search(r'FONTES (.*)', text)
    names = {f'127.0.0.1:{port}': f'peer{i}' for i, port in enumerate(ports)}
    origins = {}
    if sources:
        for item in sources.group(1).split():
            address, _, count = item.rpartition('=')
            origins[names.get(address, address)] = int(count)
    return (float(elapsed.group(1)) if elapsed else None), origins


def scenario(peers, block_size, size, timeout, verbose=False, keep_logs=None):
    with tempfile.TemporaryDirectory(prefix='p2p_tp2_') as tmp:
        tmp = Path(tmp)
        source = tmp / 'original.bin'
        source.write_bytes(os.urandom(size))
        meta = tmp / 'file.meta.json'
        subprocess.run([PYTHON, str(ROOT / 'peer.py'), 'meta', str(source), '--block-size', str(block_size), '--output', str(meta)],
                       check=True, stdout=subprocess.DEVNULL)
        ports = free_ports(peers)
        jobs = []
        logs = []
        try:
            for i in range(peers):
                folder = tmp / f'peer{i}'
                command = [PYTHON, '-u', str(ROOT / 'peer.py'), 'run', '--port', str(ports[i]), '--meta', str(meta), '--dir', str(folder)]
                if i == 0:
                    command += ['--seed', str(source)]
                if verbose:
                    command += ['--verbose']
                for j in range(peers):
                    if j != i:
                        command += ['--neighbor', f'127.0.0.1:{ports[j]}']
                log = (tmp / f'peer{i}.log').open('w')
                logs.append(log)
                jobs.append(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT))
            start = time.monotonic()
            end = start + timeout
            while time.monotonic() < end:
                if all((tmp / f'peer{i}' / 'original.bin').is_file() for i in range(1, peers)):
                    break
                if any(job.poll() is not None for job in jobs):
                    break
                time.sleep(0.1)
            elapsed = time.monotonic() - start
            # Da tempo para o ultimo peer escrever a linha FONTES no log.
            time.sleep(0.3)
            actual = checksum(source)
            results = []
            for i in range(1, peers):
                downloaded = tmp / f'peer{i}' / 'original.bin'
                ok = downloaded.is_file() and downloaded.stat().st_size == size and checksum(downloaded) == actual
                peer_time, origins = parse_log((tmp / f'peer{i}.log').read_text(errors='replace'), ports)
                results.append({'peer': f'peer{i}', 'ok': ok, 'elapsed_s': peer_time, 'sources': origins})
            ok = all(r['ok'] for r in results)
            print(f'peers={peers} bloco={block_size}B arquivo={size}B tempo={elapsed:.1f}s => {"PASSOU" if ok else "FALHOU"}', flush=True)
            for r in results:
                origins = ' '.join(f'{k}={v}' for k, v in sorted(r['sources'].items()))
                print(f'    {r["peer"]}: tempo={r["elapsed_s"]}s fontes: {origins}', flush=True)
            if not ok:
                for i in range(1, peers):
                    log_path = tmp / f'peer{i}.log'
                    last = [line for line in log_path.read_text(errors='replace').splitlines() if 'progresso=' in line]
                    print(f'peer{i}: {last[-1] if last else "sem progresso"}; timeout={timeout}s', flush=True)
                for i, log in enumerate(logs):
                    log.flush()
                    print(f'----- peer{i} -----')
                    print((tmp / f'peer{i}.log').read_text()[-3000:])
            return ok, results
        finally:
            for job in jobs:
                job.terminate()
            for job in jobs:
                try:
                    job.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    job.kill()
                    job.wait()
            for log in logs:
                log.close()
            if keep_logs:
                keep_logs.mkdir(parents=True, exist_ok=True)
                for i in range(peers):
                    shutil.copy(tmp / f'peer{i}.log', keep_logs / f'peer{i}.log')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='Executar combinacoes de 2/4 peers, 1/4 KB, tamanhos padrao e variacoes')
    parser.add_argument('--timeout', type=float, default=600, help='Limite por cenario em segundos (padrao: 600)')
    parser.add_argument('--repeats', type=int, default=1, help='Execucoes de cada cenario (padrao: 1)')
    parser.add_argument('--csv', type=Path, help='Gravar tempo e fontes de cada leecher neste CSV')
    parser.add_argument('--logs', type=Path, help='Copiar os logs da primeira execucao de cada cenario para esta pasta')
    parser.add_argument('--verbose', action='store_true', help='Peers registram todas as mensagens trocadas')
    args = parser.parse_args()
    sizes = [10 * 1024, 20 * 1024, 1024 * 1024, 5 * 1024 * 1024, 10 * 1024 * 1024, 20 * 1024 * 1024] if args.full else [10 * 1024]
    peer_counts = [2, 4]
    blocks = [1024, 4096] if args.full else [1024]
    rows = []
    status = []
    for p in peer_counts:
        for b in blocks:
            for s in sizes:
                for run in range(1, args.repeats + 1):
                    keep = args.logs / f'peers{p}_bloco{b}_arquivo{s}' if args.logs and run == 1 else None
                    ok, results = scenario(p, b, s, args.timeout, args.verbose, keep)
                    status.append(ok)
                    for r in results:
                        origins = ';'.join(f'{k}={v}' for k, v in sorted(r['sources'].items()))
                        rows.append({'peers': p, 'block_size': b, 'file_size': s, 'run': run, 'peer': r['peer'],
                                     'ok': int(r['ok']), 'elapsed_s': r['elapsed_s'], 'sources': origins})
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f'CSV salvo em {args.csv}')
    print(f'RESUMO: {sum(status)}/{len(status)} cenarios passaram')
    sys.exit(0 if all(status) else 1)


if __name__ == '__main__':
    main()
