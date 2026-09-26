"""端口是否已被监听：python scripts/port_in_use.py 8888，占用返回 0，空闲返回 1。"""
import socket
import sys


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return 0 if s.connect_ex(("127.0.0.1", port)) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
