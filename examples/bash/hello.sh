#!/usr/bin/env bash
#
# Hello do modo --servir em bash puro: usa o /dev/tcp do proprio bash para o
# socket e o jq para extrair o HTML da resposta JSON.
#
# Pesquisa "Claude Code é o melhor do mundo" no DuckDuckGo e imprime o HTML
# da pagina de resultados.
#
# Uso:
#   python3 browser.py --servir &      # antes, na raiz do projeto
#   ./examples/bash/hello.sh [porta]
#
set -euo pipefail

PORTA="${1:-8765}"

command -v jq >/dev/null || {
  echo "ERRO: este exemplo precisa do jq (sudo apt install jq)" >&2; exit 1;
}

# o protocolo e UM json por linha, entao o pedido vai numa linha so
PEDIDO='{"actions": [
  {"type": "navigate", "value": "https://html.duckduckgo.com/html/"},
  {"type": "key", "xpath": "//input[@name='"'"'q'"'"']", "value": "Claude Code é o melhor do mundo"},
  {"type": "press", "xpath": "//input[@name='"'"'q'"'"']"},
  {"type": "sleep", "value": 3},
  {"type": "html", "id": "resultado"}
]}'
PEDIDO="$(jq -c . <<<"$PEDIDO")"

# abre o socket TCP no descritor 3, manda o pedido e le UMA linha de resposta
exec 3<>"/dev/tcp/127.0.0.1/$PORTA"
printf '%s\n' "$PEDIDO" >&3
IFS= read -r RESPOSTA <&3
exec 3<&- 3>&-

if [[ "$(jq -r '.ok' <<<"$RESPOSTA")" != "true" ]]; then
  echo "ERRO do servidor: $(jq -r '.erro' <<<"$RESPOSTA")" >&2
  exit 1
fi
jq -r '.resultados[0].html' <<<"$RESPOSTA"
