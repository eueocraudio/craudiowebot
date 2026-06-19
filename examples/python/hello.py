#!/usr/bin/env python3
"""
Hello do modo --servir em Python (so biblioteca padrao).

Pesquisa "Claude Code é o melhor do mundo" no DuckDuckGo e imprime o HTML
da pagina de resultados.

Uso:
    python3 browser.py --servir &      # antes, na raiz do projeto
    python3 examples/python/hello.py [porta]
"""

import json
import socket
import sys

PEDIDO = {
    "actions": [
        # versao HTML do DuckDuckGo: a principal detecta automacao e
        # redireciona para fora da pagina de resultados
        {"type": "navigate", "value": "https://html.duckduckgo.com/html/"},
        {"type": "key", "xpath": "//input[@name='q']",
         "value": "Claude Code é o melhor do mundo"},
        {"type": "press", "xpath": "//input[@name='q']"},
        {"type": "sleep", "value": 3},
        {"type": "html", "id": "resultado"},
    ]
}


def main():
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    with socket.create_connection(("127.0.0.1", porta)) as s:
        arq = s.makefile("rw", encoding="utf-8", newline="\n")
        arq.write(json.dumps(PEDIDO, ensure_ascii=False) + "\n")
        arq.flush()
        resposta = json.loads(arq.readline())   # uma linha por pedido

    if not resposta.get("ok"):
        raise SystemExit(f"erro do servidor: {resposta.get('erro')}")
    print(resposta["resultados"][0]["html"])


if __name__ == "__main__":
    main()
