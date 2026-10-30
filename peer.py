import argparse
import hashlib
import json
import math
import os
import shutil
import socket
import struct
import threading
import time
from pathlib import Path

MAX_MESSAGE = 16 * 1024 * 1024
MAX_HEADER = 1024 * 1024  # HAVE pode listar mais de 20 mil blocos


def read_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        part = sock.recv(min(65536, length - len(chunks)))
        if not part:
            raise ConnectionError('Conexao encerrada')
        chunks.extend(part)
    return bytes(chunks)


def send_message(sock, header, payload=b''):
    body = json.dumps(header, separators=(',', ':')).encode('utf-8')
    if len(body) > MAX_HEADER or len(payload) > MAX_MESSAGE:
        raise ValueError('Mensagem grande demais')
    sock.sendall(struct.pack('!I', len(body)) + body + struct.pack('!I', len(payload)) + payload)


def recv_message(sock):
    header_size = struct.unpack('!I', read_exact(sock, 4))[0]
    if not 0 < header_size <= MAX_HEADER:
        raise ValueError('Cabecalho invalido')
    header = json.loads(read_exact(sock, header_size))
    payload_size = struct.unpack('!I', read_exact(sock, 4))[0]
    if payload_size > MAX_MESSAGE:
        raise ValueError('Payload grande demais')
    return header, read_exact(sock, payload_size)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def create_metadata(path, block_size, output):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Arquivo original nao encontrado')
    if not 0 < block_size <= MAX_MESSAGE:
        raise ValueError('Tamanho de bloco invalido')
    block_hashes = []
    with path.open('rb') as f:
        while chunk := f.read(block_size):
            block_hashes.append(sha256_bytes(chunk))
    meta = {
        'name': path.name,
        'size': path.stat().st_size,
        'block_size': block_size,
        'sha256': sha256_file(path),
        'blocks': block_hashes,
    }
    Path(output).write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(f'Metadados criados: {output} ({len(block_hashes)} blocos)', flush=True)


def load_metadata(path):
    meta = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(meta.get('name'), str) or Path(meta['name']).name != meta['name']
            or meta['name'] in ('.', '..') or not isinstance(meta.get('size'), int)
            or meta['size'] < 0 or not isinstance(meta.get('block_size'), int)
            or not 0 < meta['block_size'] <= MAX_MESSAGE or not isinstance(meta.get('blocks'), list)
            or len(meta['blocks']) != math.ceil(meta['size'] / meta['block_size'])
            or not all(isinstance(h, str) and len(h) == 64 for h in meta['blocks'])
            or not isinstance(meta.get('sha256'), str) or len(meta['sha256']) != 64):
        raise ValueError('Metadados invalidos')
    return meta


class Peer:
    def __init__(self, host, port, neighbors, meta, directory, seed_file=None):
        self.host, self.port = host, port
        self.neighbors = neighbors
        self.meta = meta
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.data_path = self.directory / (meta['sha256'] + '.data')
        self.lock = threading.RLock()
        self.blocks = set()
        self.running = threading.Event()
        self.running.set()
        self.rr = 0
        self.seed_file = Path(seed_file) if seed_file else None
        if self.seed_file:
            if not self.seed_file.is_file() or self.seed_file.stat().st_size != meta['size'] or sha256_file(self.seed_file) != meta['sha256']:
                raise ValueError('Seeder: arquivo nao corresponde aos metadados')
            self.blocks = set(range(len(meta['blocks'])))
        else:
            # Um unico arquivo de dados em vez de milhares de arquivos .part.
            # A bitmap de blocos e reconstruida apos reiniciar, verificando os hashes.
            if self.data_path.exists() and self.data_path.stat().st_size == meta['size']:
                with self.data_path.open('rb') as f:
                    for i in range(len(meta['blocks'])):
                        chunk = f.read(meta['block_size'])
                        if self.valid(i, chunk):
                            self.blocks.add(i)
            else:
                with self.data_path.open('wb') as f:
                    f.truncate(meta['size'])

    def valid(self, index, data):
        count = len(self.meta['blocks'])
        if not 0 <= index < count:
            return False
        expected = min(self.meta['block_size'], self.meta['size'] - index * self.meta['block_size'])
        return len(data) == expected and sha256_bytes(data) == self.meta['blocks'][index]

    def get_block(self, index):
        with self.lock:
            if index not in self.blocks:
                return None
        if self.seed_file:
            with self.seed_file.open('rb') as f:
                f.seek(index * self.meta['block_size'])
                data = f.read(self.meta['block_size'])
        else:
            with self.data_path.open('rb') as f:
                f.seek(index * self.meta['block_size'])
                data = f.read(self.meta['block_size'])
        return data if self.valid(index, data) else None

    def handle(self, conn, addr):
        with conn:
            conn.settimeout(30)
            while self.running.is_set():
                try:
                    req, _ = recv_message(conn)
                    if req.get('file_id') != self.meta['sha256']:
                        send_message(conn, {'error': 'arquivo desconhecido'})
                        return
                    if req.get('type') == 'HAVE':
                        with self.lock:
                            available = sorted(self.blocks)
                        send_message(conn, {'type': 'HAVE', 'blocks': available})
                    elif req.get('type') == 'GET':
                        index = req.get('index')
                        data = self.get_block(index) if type(index) is int else None
                        if data is None:
                            send_message(conn, {'error': 'bloco indisponivel'})
                        else:
                            send_message(conn, {'type': 'BLOCK', 'index': index}, data)
                            if index % 256 == 0 or index == len(self.meta['blocks']) - 1:
                                print(f'[{self.port}] SEND bloco={index} para={addr[0]}:{addr[1]}', flush=True)
                    else:
                        send_message(conn, {'error': 'comando invalido'})
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    if not isinstance(exc, ConnectionError) and not isinstance(exc, (ConnectionResetError, BrokenPipeError)):
                        print(f'[{self.port}] Requisicao encerrada: {exc}', flush=True)
                    return

    def serve(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(64)
            server.settimeout(1)
            print(f'[{self.port}] Servidor ouvindo {self.host}:{self.port}', flush=True)
            while self.running.is_set():
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self.handle, args=(conn, addr), daemon=True).start()

    def request(self, neighbor, header):
        # Reutiliza uma conexao TCP por vizinho, evitando esgotar portas efemeras no Windows.
        if not hasattr(self, '_connections'):
            self._connections = {}
        sock = self._connections.get(neighbor)
        for attempt in range(2):
            try:
                if sock is None:
                    sock = socket.create_connection(neighbor, timeout=3)
                    sock.settimeout(8)
                    self._connections[neighbor] = sock
                send_message(sock, {**header, 'file_id': self.meta['sha256']})
                response, data = recv_message(sock)
                if 'error' in response:
                    raise ValueError(response['error'])
                return response, data
            except (OSError, ConnectionError):
                self._connections.pop(neighbor, None)
                if sock is not None:
                    sock.close()
                sock = None
                if attempt == 1:
                    raise

    def store(self, index, data, neighbor):
        if not self.valid(index, data):
            print(f'[{self.port}] REJECT bloco={index}: hash/tamanho invalido', flush=True)
            return
        with self.lock:
            if index in self.blocks:
                return
            with self.data_path.open('r+b') as f:
                f.seek(index * self.meta['block_size'])
                f.write(data)
            self.blocks.add(index)
            if len(self.blocks) % 256 == 0 or len(self.blocks) == len(self.meta['blocks']):
                print(f'[{self.port}] RECV bloco={index} de={neighbor[0]}:{neighbor[1]} progresso={len(self.blocks)}/{len(self.meta["blocks"])}', flush=True)

    def download(self):
        n = len(self.meta['blocks'])
        missing = set(range(n)) - self.blocks
        candidates = {}
        refresh_after = 0
        progress = 0
        cursor = 0
        try:
            while self.running.is_set():
                if not missing:
                    self.assemble()
                    return
                if progress >= refresh_after or not candidates:
                    candidates = {}
                    for neighbor in self.neighbors:
                        try:
                            response, _ = self.request(neighbor, {'type': 'HAVE'})
                            if response.get('type') != 'HAVE' or not isinstance(response.get('blocks'), list):
                                continue
                            for idx in response['blocks']:
                                if type(idx) is int and idx in missing:
                                    candidates.setdefault(idx, []).append(neighbor)
                        except (OSError, ValueError, TypeError) as exc:
                            print(f'[{self.port}] Vizinho {neighbor} indisponivel: {exc}', flush=True)
                    refresh_after = progress + 256
                    cursor = 0
                    available = sorted(candidates)
                if not candidates:
                    time.sleep(0.3)
                    continue
                # Escolhe sem reconstruir e ordenar milhares de blocos a cada iteracao.
                while cursor < len(available) and available[cursor] not in missing:
                    cursor += 1
                if cursor >= len(available):
                    candidates = {}
                    continue
                index = available[cursor]
                cursor += 1
                options = candidates[index]
                neighbor = options[self.rr % len(options)]
                self.rr += 1
                try:
                    response, data = self.request(neighbor, {'type': 'GET', 'index': index})
                    if response.get('type') == 'BLOCK' and response.get('index') == index:
                        self.store(index, data, neighbor)
                        missing.discard(index)
                        progress += 1
                        candidates.pop(index, None)
                    else:
                        candidates = {}
                except (OSError, ValueError, TypeError) as exc:
                    print(f'[{self.port}] GET {index} falhou: {exc}', flush=True)
                    candidates = {}
                    time.sleep(0.1)
        finally:
            for sock in getattr(self, '_connections', {}).values():
                sock.close()

    def assemble(self):
        output = self.directory / self.meta['name']
        temp = self.directory / (self.meta['name'] + '.assembling')
        with self.data_path.open('rb') as source, temp.open('wb') as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        if temp.stat().st_size != self.meta['size'] or sha256_file(temp) != self.meta['sha256']:
            temp.unlink(missing_ok=True)
            raise ValueError('Arquivo remontado nao passou na verificacao SHA-256')
        temp.replace(output)
        print(f'[{self.port}] CONCLUIDO arquivo={output} tamanho={output.stat().st_size} sha256={sha256_file(output)}', flush=True)

    def run(self):
        threading.Thread(target=self.serve, daemon=True).start()
        if not self.seed_file:
            self.download()
        else:
            print(f'[{self.port}] Seeder pronto ({len(self.blocks)} blocos)', flush=True)
        # Permanece como servidor ate Ctrl+C, inclusive apos baixar tudo.
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.running.clear()


def main():
    parser = argparse.ArgumentParser(description='TP2 Sistemas Distribuidos: Peer P2P TCP')
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('meta', help='Gerar metadados do arquivo original')
    create.add_argument('file')
    create.add_argument('--block-size', type=int, default=1024)
    create.add_argument('--output', default='file.meta.json')
    peer = sub.add_parser('run', help='Iniciar peer')
    peer.add_argument('--host', default='127.0.0.1')
    peer.add_argument('--port', type=int, required=True)
    peer.add_argument('--neighbor', action='append', default=[], help='IP:porta (repetivel)')
    peer.add_argument('--meta', required=True)
    peer.add_argument('--dir', required=True)
    peer.add_argument('--seed', help='Caminho para arquivo original, apenas se for seeder')
    args = parser.parse_args()
    if args.command == 'meta':
        create_metadata(args.file, args.block_size, args.output)
    else:
        neighbors = []
        for item in args.neighbor:
            host, sep, port = item.rpartition(':')
            if not sep or not host:
                parser.error('Vizinho deve usar formato IP:porta')
            neighbors.append((host, int(port)))
        Peer(args.host, args.port, neighbors, load_metadata(args.meta), args.dir, args.seed).run()


if __name__ == '__main__':
    main()
