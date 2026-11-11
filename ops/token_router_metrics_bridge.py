"""Expose only the trial /metrics port on the Docker bridge.

The mock router stays on 127.0.0.1:4010. Prometheus runs in Docker and scrapes
172.18.0.1:4010; every other path is rejected before it reaches LiteLLM.
"""
import socket
import threading

BRIDGE = ("172.18.0.1", 4010)
UPSTREAM = ("127.0.0.1", 4010)


def relay(source, destination):
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            destination.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, destination):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client):
    try:
        client.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 16384:
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
        request = data.split(b"\r\n", 1)[0]
        if not request.startswith(b"GET /metrics"):
            client.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        upstream = socket.create_connection(UPSTREAM, 10)
        upstream.sendall(data)
        client.settimeout(None)
        upstream.settimeout(None)
        threading.Thread(target=relay, args=(client, upstream), daemon=True).start()
        relay(upstream, client)
    except OSError:
        pass
    finally:
        client.close()


def main():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(BRIDGE)
    server.listen(32)
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()