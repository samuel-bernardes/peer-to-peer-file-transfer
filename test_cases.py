"""Executa cenarios do enunciado com peers locais e confere SHA-256."""
import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).parent
PYTHON = sys.executable


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


def scenario(peers, block_size, size, timeout):
    with tempfile.TemporaryDirectory(prefix='p2p_tp2_') as tmp:
        tmp = Path(tmp)
        source = tmp / 'original.bin'
        source.write_bytes(os.urandom(size))
        meta = tmp / 'file.meta.json'
        subprocess.run([PYTHON, str(ROOT / 'peer.py'), 'meta', str(source), '--block-size', str(block_size), '--output', str(meta)], check=True)
        ports = free_ports(peers)
        jobs = []
        logs = []
        try:
            for i in range(peers):
                folder = tmp / f'peer{i}'
                command = [PYTHON, '-u', str(ROOT / 'peer.py'), 'run', '--port', str(ports[i]), '--meta', str(meta), '--dir', str(folder)]
                if i == 0:
                    command += ['--seed', str(source)]
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
            actual = checksum(source)
            results = []
            for i in range(1, peers):
                downloaded = tmp / f'peer{i}' / 'original.bin'
                results.append(downloaded.is_file() and downloaded.stat().st_size == size and checksum(downloaded) == actual)
            ok = all(results)
            elapsed = time.monotonic() - start
            print(f'peers={peers} bloco={block_size}B arquivo={size}B tempo={elapsed:.1f}s => {"PASSOU" if ok else "FALHOU"}', flush=True)
            if not ok:
                for i in range(1, peers):
                    log_path = tmp / f'peer{i}.log'
                    last = [line for line in log_path.read_text(errors='replace').splitlines() if 'progresso=' in line]
                    print(f'peer{i}: {last[-1] if last else "sem progresso"}; timeout={timeout}s', flush=True)
            if not ok:
                for i, log in enumerate(logs):
                    log.flush()
                    print(f'----- peer{i} -----')
                    print((tmp / f'peer{i}.log').read_text()[-3000:])
            return ok
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='Executar combinacoes de 2/4 peers, 1/4 KB, tamanhos padrao e variacoes')
    parser.add_argument('--timeout', type=float, default=600, help='Limite por cenario em segundos (padrao: 600)')
    args = parser.parse_args()
    sizes = [10 * 1024, 20 * 1024, 1024 * 1024, 5 * 1024 * 1024, 10 * 1024 * 1024, 20 * 1024 * 1024] if args.full else [10 * 1024]
    peer_counts = [2, 4] if args.full else [2, 4]
    blocks = [1024, 4096] if args.full else [1024]
    status = [scenario(p, b, s, args.timeout) for p in peer_counts for b in blocks for s in sizes]
    print(f'RESUMO: {sum(status)}/{len(status)} cenarios passaram')
    sys.exit(0 if all(status) else 1)


if __name__ == '__main__':
    main()
