#!/usr/bin/env bash
#
# install.sh - instala o browser PySide6 deste projeto.
#
# Cria um ambiente virtual (.venv), instala as dependencias Python
# (PySide6 + QtWebEngine) e checa as bibliotecas de sistema que o
# Chromium embutido no QtWebEngine precisa para rodar no Linux.
#
# Uso:
#   ./install.sh
#
set -euo pipefail

# diretorio do script (funciona mesmo chamado de outro lugar)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

VENV="$DIR/.venv"
PY="${PYTHON:-python3}"

echo "==> Projeto: $DIR"

# --- 1. Python ---------------------------------------------------------
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERRO: '$PY' nao encontrado. Instale o Python 3 e tente de novo." >&2
  exit 1
fi
echo "==> Usando $("$PY" --version)"

# --- 2. Ambiente virtual (com fallback) -------------------------------
# Tenta criar uma venv. Se o sistema nao tiver suporte (falta python3-venv
# / ensurepip), cai para instalacao no Python do sistema com 'pip --user'.
VPY=""
if [ -d "$VENV" ] && [ -x "$VENV/bin/python" ]; then
  echo "==> Ambiente virtual .venv ja existe"
  VPY="$VENV/bin/python"
elif "$PY" -m venv "$VENV" 2>/dev/null; then
  echo "==> Ambiente virtual criado em .venv"
  VPY="$VENV/bin/python"
else
  echo "==> AVISO: nao foi possivel criar a venv (falta python3-venv/ensurepip)."
  echo "    Debian/Ubuntu: sudo apt install python3-venv"
  echo "    Continuando com o Python do sistema (pip install --user)."
  rm -rf "$VENV"
  VPY="$PY"
fi

# define como o pip sera chamado (com --user quando fora de venv)
PIP_ARGS=()
if [ "$VPY" = "$PY" ]; then
  PIP_ARGS=(--user)
fi

echo "==> Atualizando pip"
"$VPY" -m pip install "${PIP_ARGS[@]}" --upgrade pip >/dev/null 2>&1 || \
  echo "    (nao foi possivel atualizar o pip; seguindo mesmo assim)"

echo "==> Instalando dependencias (requirements.txt)"
"$VPY" -m pip install "${PIP_ARGS[@]}" -r "$DIR/requirements.txt"

# --- 3. Teste de importacao do QtWebEngine ----------------------------
# Esta e a verificacao que importa: se o QtWebEngine carrega, as libs de
# sistema necessarias (o Chromium embutido precisa de varias) estao OK.
echo "==> Testando importacao do QtWebEngine"
if QT_QPA_PLATFORM=offscreen "$VPY" -c \
   "from PySide6.QtWebEngineWidgets import QWebEngineView; print('   OK')"; then
  :
else
  echo "ERRO: falha ao importar/iniciar o QtWebEngine." >&2
  echo "    O Chromium embutido precisa de bibliotecas de sistema. Instale:" >&2
  echo "    Debian/Ubuntu: sudo apt install libnss3 libxcomposite1 libxdamage1 \\" >&2
  echo "                   libxrandr2 libxtst6 libasound2 libxkbcommon0 libxcb-cursor0" >&2
  echo "    Fedora:        sudo dnf install nss libXcomposite libXdamage \\" >&2
  echo "                   libXrandr libXtst alsa-lib libxkbcommon xcb-util-cursor" >&2
  exit 1
fi

# Aviso a parte: a JANELA grafica (plugin xcb) precisa do libxcb-cursor0.
# O teste acima roda em modo offscreen e nao detecta a falta dele, entao
# avisamos aqui se a lib nao estiver presente. Checamos o cache do ldconfig
# E os diretorios padrao, pois em alguns sistemas o cache fica desatualizado.
has_xcb_cursor() {
  ldconfig -p 2>/dev/null | grep -q "libxcb-cursor.so" && return 0
  for d in /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu; do
    ls "$d"/libxcb-cursor.so.* >/dev/null 2>&1 && return 0
  done
  return 1
}
if ! has_xcb_cursor; then
  echo "==> AVISO: 'libxcb-cursor0' parece ausente; a janela grafica (xcb) pode"
  echo "    falhar ao abrir. Instale:"
  echo "    Debian/Ubuntu: sudo apt install libxcb-cursor0"
  echo "    Fedora:        sudo dnf install xcb-util-cursor"
fi

echo
echo "==> Instalacao concluida."
echo "    Para rodar:"
echo "        $VPY browser.py"
if [ "$VPY" != "$PY" ]; then
  echo "    ou ative a venv primeiro:"
  echo "        source .venv/bin/activate && python browser.py"
fi
