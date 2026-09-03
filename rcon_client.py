"""Minimal Source RCON protocol client for Palworld's RCON server."""
import socket
import struct


class RconError(Exception):
    pass


class RconAuthError(RconError):
    pass


def _send_packet(sock, pkt_id, pkt_type, body):
    payload = struct.pack('<ii', pkt_id, pkt_type) + body.encode('utf-8') + b'\x00\x00'
    sock.sendall(struct.pack('<i', len(payload)) + payload)


def _read_packet(sock):
    header = b''
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            raise RconError('Connection closed while reading packet length')
        header += chunk
    length = struct.unpack('<i', header)[0]
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise RconError('Connection closed while reading packet body')
        data += chunk
    pkt_id, pkt_type = struct.unpack('<ii', data[:8])
    body = data[8:-2].decode('utf-8', errors='replace')
    return pkt_id, pkt_type, body


def execute(host, port, password, command, timeout=5):
    """Open a fresh RCON connection, authenticate, run one command, close."""
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        _send_packet(sock, 1, 3, password)
        pkt_id, _, _ = _read_packet(sock)
        if pkt_id == -1:
            raise RconAuthError('RCON authentication failed - check password')
        _send_packet(sock, 2, 2, command)
        _, _, body = _read_packet(sock)
        return body
    finally:
        sock.close()
