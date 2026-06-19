#!/usr/bin/env python3
"""
Cliente de referencia do servidor de comandos do browser (--servir).

Conecta no socket TCP, manda pedidos em JSON (um por linha) e imprime as
respostas. So usa a biblioteca padrao: serve de documentacao executavel do
protocolo para clientes em qualquer linguagem (ver examples/).

Uso:
    python3 browser.py --servir &      # browser aberto servindo na 8765

    python3 cliente.py '{"type": "navigate", "value": "https://example.com"}'
    python3 cliente.py '{"type": "html"}'              # HTML da pagina
    python3 cliente.py -p 9000 '{"actions": [
        {"type": "navigate", "value": "https://example.com"},
        {"type": "html", "id": "pagina"}]}'            # lote, porta 9000

Cada argumento posicional e UM pedido; todos vao na mesma conexao e as
respostas voltam na ordem em que foram enviados.
"""

import argparse
import json
import socket
import sys


def enviar(pedidos, host="127.0.0.1", porta=8765):
    """Manda os pedidos (strings JSON) e devolve a lista de respostas (dicts)."""
    with socket.create_connection((host, porta)) as s:
        arq = s.makefile("rw", encoding="utf-8", newline="\n")
        for pedido in pedidos:
            # valida e reescreve numa linha so (o protocolo e um JSON por linha)
            arq.write(json.dumps(json.loads(pedido), ensure_ascii=False) + "\n")
        arq.flush()
        respostas = []
        for _ in pedidos:
            linha = arq.readline()
            if not linha:
                # EOF: o servidor fechou a conexao sem responder. Acontece com
                # 'exit'/'finish', que encerram o app antes de mandar a resposta.
                respostas.append({"ok": True, "encerrado": True,
                                  "info": "servidor encerrou a conexao "
                                          "(exit/finish?)"})
                break
            respostas.append(json.loads(linha))
        return respostas


def main():
    p = argparse.ArgumentParser(
        description="Manda actions em JSON para o browser em modo --servir."
    )
    p.add_argument("-H", "--host", default="127.0.0.1")
    p.add_argument("-p", "--porta", type=int, default=8765)
    p.add_argument("pedidos", nargs="+", metavar="JSON",
                   help="pedido em JSON: uma action ou {\"actions\": [...]}")
    args = p.parse_args()

    try:
        respostas = enviar(args.pedidos, args.host, args.porta)
    except ConnectionRefusedError:
        raise SystemExit(
            f"Nao conectou em {args.host}:{args.porta} -- o browser esta "
            f"rodando com --servir?"
        )
    for resposta in respostas:
        print(json.dumps(resposta, ensure_ascii=False, indent=2))
    if not all(r.get("ok") for r in respostas):
        sys.exit(1)


if __name__ == "__main__":
    main()
