"""
Browser PySide6 que executa "jobs" descritos em JSON.

Um job e um arquivo como jobs/job1.json:

    {
      "name": "Login exemplo",
      "actions": [
        {"type": "navigate", "value": "https://exemplo.com"},
        {"type": "key",   "xpath": "//*[@id='txt_email']", "value": "a@b.com"},
        {"type": "click", "xpath": "//*[@id='submit2']"},
        {"type": "sleep", "value": 180}
      ]
    }

O mecanismo (classe JobRunner) executa as actions NA SEQUENCIA, esperando
cada uma terminar antes de comecar a proxima:
  - navigate  -> carrega a URL e espera o loadFinished
  - key       -> acha o elemento por XPath e digita o 'value' nele
  - click     -> acha o elemento por XPath e clica
  - press     -> pressiona uma tecla ('value', padrao "Enter") no elemento
                 do XPath (ou no campo focado se nao houver xpath)
  - sleep     -> espera 'value' segundos
  - exists    -> espera ate 'wait' segundos pelo elemento do XPath; se
                 aparecer executa as acoes filhas em 'yes', senao as de 'not'.
                 ex: {"type":"exists","xpath":"...","wait":5,
                      "yes":[...acoes...], "not":[...acoes...]}
  - exit      -> espera 'wait' segundos e desliga o aplicativo (libera tudo)
  - finish    -> chama o hook finish() do run.py e encerra o app (espera 'wait'
                 segundos, padrao 3, para o finish() concluir trabalho async)
  - html      -> le o HTML da pagina (ou, com 'xpath', o outerHTML do
                 elemento) e o devolve nos resultados do lote -- e como um
                 cliente do modo --servir recebe o HTML de volta
  - url       -> devolve a URL atual da pagina nos resultados do lote
  - eval      -> roda o JS de 'value' no contexto da pagina e devolve o
                 retorno nos resultados (ex.: chamar API interna via XHR)
  - title     -> troca o titulo da janela para 'value'. Nao toca a pagina;
                 um 'title' avulso pelo --servir fura a fila (aplica na hora,
                 mesmo com job rodando). Em lote/job.json roda em sequencia.
  - comment   -> escreve '[COMENTARIO] value' na caixa de log. Como o 'title',
                 nao toca a pagina: avulso pelo --servir tambem fura a fila.
  - user_agent-> troca o User-Agent do perfil para 'value'. Vale para as
                 proximas requisicoes (navegue/recarregue para a pagina atual
                 usar). Deixa o cliente do --servir definir o UA em runtime; o
                 campo "user_agent" no topo do job.json define o UA na partida.
  - save_profile -> salva os arquivos do perfil atual num .tar.gz ('value' =
                 caminho; aceita ~). Cria o diretorio de destino se faltar.
  - load_profile -> restaura o perfil de um .tar.gz ('value' = caminho; aceita
                 ~). Se o arquivo nao existir, so avisa (nao eleva erro).

E generico/extensivel: para um novo tipo de acao basta criar um metodo
'_do_<tipo>(self, action)' em JobRunner.

Hooks por job: se a pasta do job tiver um run.py com uma classe Job, ela e
instanciada como Job(browser, json) e seus metodos sao chamados:
  - pre_action(action) antes de cada acao
  - pos_action(action) depois de cada acao terminar
  - finish()           ao fim do job
Todos opcionais. O 'browser' recebido permite manipular a pagina.

Como funciona a ponte Python <-> pagina:
  tudo passa por view.page().runJavaScript(codigo, callback). A leitura do
  resultado e ASSINCRONA: o valor retornado pelo JS chega no callback.

Modo servidor (--servir [porta], padrao 8765): alem (ou em vez) do job
inicial, abre um socket TCP em 127.0.0.1 que recebe actions em JSON -- uma
por linha, mesmo formato do job.json -- e as executa em tempo de execucao
(classe ServidorComandos). Com --servir o -s e opcional; combinando os dois,
o job inicial roda primeiro (ex.: login) e o browser segue aberto servindo.
Obs.: um job com 'exit'/'finish' fecha o app; para servir depois do job,
termine-o sem essas actions. Cliente de referencia: cliente.py e examples/.

Proxy (--proxy/-p ou campo "proxy" no job.json): proxy de aplicacao para todo
o QtWebEngine (ex.: "http://usuario:senha@host:porta", socks5://... ou
"host:porta"). No job.json o campo aceita uma string ou um ARRAY de proxies --
nesse caso um e sorteado na partida. Precedencia: -p > campo "proxy" no .json.

Log de eventos: cada acao (navigate/click/key/...) e registrada via 'logging'
em /tmp/browser_{PORTA}_events.log (PORTA do --servir; sem --servir usa o PID).
O texto DIGITADO (value de 'key') NUNCA e gravado -- so o xpath. Esse log e
separado da caixa de log da janela (Browser._log).

uBlock: o browser empacota o uBlock Origin Lite (MV3) em data/extensions/ e o
instala no perfil na partida (carregar_extensoes). Ligado por padrao; desliga
com --sem-ublock ou "ublock": false no job.json. Precisa Qt 6.10+ (a API de
extensoes so existe a partir dai, e so aceita MV3 -- o MV2 ja foi removido).
"""

import argparse
import importlib.util
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QObject, QTimer, QUrl
from PySide6.QtNetwork import QHostAddress, QNetworkProxy, QTcpServer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView


# User-Agent que o browser anuncia aos sites.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"


# Logger de EVENTOS de pagina (navigate/click/key/...). Configurado em
# configurar_log_eventos() no main; grava em /tmp/browser_{PORTA}_events.log.
# IMPORTANTE: o texto digitado (value de 'key') NUNCA e registrado.
eventos = logging.getLogger("craudiowebot.eventos")


# Funcao JS auxiliar: resolve um XPath e devolve o primeiro elemento.
JS_BUSCA_XPATH = """
function __byXPath(xp) {
  var r = document.evaluate(xp, document, null,
      XPathResult.FIRST_ORDERED_NODE_TYPE, null);
  return r.singleNodeValue;
}
"""

# Funcao JS que escreve um valor num elemento, lidando com os dois casos:
#  - input/textarea: define .value pelo SETTER NATIVO do prototipo e dispara
#    input/change. O setter nativo e necessario para inputs controlados por
#    React/Vue (ex.: busca do DuckDuckGo): atribuir el.value direto nao
#    atualiza o estado do framework, que re-renderiza o campo vazio.
#  - contenteditable (ProseMirror, editores ricos): usa execCommand('insertText'),
#    que dispara os eventos beforeinput/input que esses editores escutam.
JS_ESCREVER = """
function __escrever(el, val) {
  el.focus();
  if (el.isContentEditable) {
    var sel = window.getSelection();
    sel.selectAllChildren(el);                 // substitui o conteudo atual
    if (!document.execCommand('insertText', false, val)) {
      el.textContent = val;                    // fallback
      el.dispatchEvent(new InputEvent('input',
          {bubbles: true, data: val, inputType: 'insertText'}));
    }
  } else {
    var proto = el instanceof HTMLTextAreaElement ?
        HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) { desc.set.call(el, val); } else { el.value = val; }
    el.dispatchEvent(new Event('input',  {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }
}
"""

# Pausa (ms) entre acoes rapidas, para a pagina reagir antes da proxima.
PAUSA_ENTRE_ACOES = 300

# Intervalo (ms) entre tentativas do 'exists' enquanto espera o elemento.
EXISTS_POLL_MS = 300

# Quadros do spinner: 1o caractere do titulo da janela, avanca a cada acao
# executada (sinal visual de "algo esta rodando").
SPINNER = "|/-\\"

# Pasta onde os downloads sao salvos (auto-aceitos por Browser._ao_baixar).
DIR_DOWNLOADS = "~/Downloads"

# Nomes legiveis dos estados de um download (QWebEngineDownloadRequest).
# Mapa montado por enum (nao por numero) para nao depender dos valores.
try:
    _DS = QWebEngineDownloadRequest.DownloadState
    ESTADO_DOWNLOAD = {
        _DS.DownloadRequested: "solicitado",
        _DS.DownloadInProgress: "baixando",
        _DS.DownloadCompleted: "concluido",
        _DS.DownloadCancelled: "cancelado",
        _DS.DownloadInterrupted: "interrompido",
    }
except Exception:                              # versao de Qt sem o enum
    ESTADO_DOWNLOAD = {}


class JobRunner(QObject):
    """Executa, em sequencia, as actions de um job sobre um QWebEngineView."""

    def __init__(self, view: QWebEngineView, log, job_obj=None, janela=None):
        super().__init__()
        # self.view = view da JANELA ATIVA (a que o servidor dirige). Repontada
        # pelo controlador (Browser.trocar_janela) quando a janela ativa muda.
        self.view = view
        # controlador (Browser): registro de janelas, titulo/spinner, downloads.
        # Usado para o que e da JANELA, nao da pagina (self.view.window() daria a
        # janela ativa concreta, nao o controlador).
        self.janela = janela
        self.log = log            # funcao para registrar mensagens
        # objeto Job opcional (de run.py) com hooks pre_action/pos_action/finish
        self.job_obj = job_obj
        # pilha de frames [lista_de_actions, indice]. Acoes com filhos (exists)
        # empilham um novo frame; ao terminar, o controle volta ao frame pai.
        self.stack = []
        # acao cuja pos_action ainda nao foi chamada (so roda quando ela termina)
        self._pending_pos = None
        # fila de LOTES de actions: cada item e (actions, ao_terminar). O job
        # inicial e os pedidos do ServidorComandos entram aqui e rodam um por
        # vez, na ordem de chegada -- nunca dois lotes mexem na pagina juntos.
        self._fila = []
        self._ao_terminar = None   # callback do lote atual
        self._resultados = []      # saidas das acoes de leitura ('html')
        self._rodando = False

    def _hook(self, nome, *args):
        """Chama um hook do run.py (se existir), sem deixar erro travar o job."""
        if self.job_obj is None:
            return
        fn = getattr(self.job_obj, nome, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception as e:
                self.log(f"    [run.py {nome}] ERRO: {e}")

    # -- controle ------------------------------------------------------
    def run(self, job: dict):
        actions = job.get("actions", [])
        nome = job.get("name", "(sem nome)")
        self.log(f"=== JOB: {nome} | {len(actions)} acao(oes) ===")

        def fim(_resultados):
            self.log("=== JOB concluido ===")
            self._hook("finish")

        self.executar(actions, fim)

    def executar(self, actions, ao_terminar=None):
        """Enfileira uma sequencia de actions (mesmo formato do job.json).

        Quando o lote termina, chama ao_terminar(resultados) com a lista de
        saidas das acoes de leitura (type 'html'). Lotes rodam em sequencia:
        um pedido do servidor espera o job/pedido anterior terminar.
        """
        self._fila.append((list(actions or []), ao_terminar))
        if not self._rodando:
            self._proximo_lote()

    def _proximo_lote(self):
        if not self._fila:
            self._rodando = False
            return
        actions, cb = self._fila.pop(0)
        self._rodando = True
        self._ao_terminar = cb
        self._resultados = []
        self.stack = [[actions, 0]]
        self._pending_pos = None
        self._next()

    def _next(self):
        # remove frames ja terminados (volta para o pai)
        while self.stack and self.stack[-1][1] >= len(self.stack[-1][0]):
            self.stack.pop()
        # a acao anterior terminou -> pos_action dela
        if self._pending_pos is not None:
            self._hook("pos_action", self._pending_pos)
            self._pending_pos = None
        if not self.stack:
            # lote concluido: entrega os resultados e segue para o proximo
            cb, resultados = self._ao_terminar, self._resultados
            self._ao_terminar, self._resultados = None, []
            if cb is not None:
                try:
                    cb(resultados)
                except Exception as e:
                    self.log(f"    [ao_terminar] ERRO: {e}")
            self._proximo_lote()
            return
        frame = self.stack[-1]
        action = frame[0][frame[1]]
        frame[1] += 1
        ind = "  " * (len(self.stack) - 1)   # indentacao por profundidade
        tipo = action.get("type")
        handler = getattr(self, f"_do_{tipo}", None)
        alvo = action.get("xpath") or action.get("value", "")
        self.log(f"{ind}[{frame[1]}/{len(frame[0])}] {tipo}: {alvo}")
        # avanca o spinner (1o caractere do titulo): sinal de "algo rodou".
        # Sempre na janela principal (controlador), nao na janela ativa.
        janela = self.janela if self.janela is not None else self.view.window()
        if janela is not None and hasattr(janela, "girar_titulo"):
            janela.girar_titulo()
        if handler is None:
            self.log(f"{ind}  AVISO: tipo desconhecido {tipo!r}; pulando")
            self._continuar()
            return
        # pre_action antes de executar; pos_action sera chamada quando terminar
        self._hook("pre_action", action)
        # registra o evento DEPOIS do pre_action (que pode injetar a URL etc.),
        # no log de eventos /tmp/browser_{PORTA}_events.log
        self._registrar_evento(action)
        self._pending_pos = action
        try:
            handler(action)
        except Exception as e:  # nunca trava o job inteiro por uma acao
            self.log(f"{ind}  ERRO na acao: {e}")
            self._continuar()

    def _continuar(self):
        """Agenda a proxima acao apos uma pequena pausa."""
        QTimer.singleShot(PAUSA_ENTRE_ACOES, self._next)

    def _entrar_ramo(self, children):
        """Empilha uma sub-sequencia de acoes (filhos de yes/not)."""
        self.stack.append([list(children or []), 0])
        self._continuar()

    def _registrar_evento(self, action):
        """Registra a acao no log de eventos (/tmp/browser_{PORTA}_events.log).

        NUNCA inclui o texto digitado (value de 'key'). Para 'navigate' grava a
        URL; para 'key' grava so o xpath (value oculto); para os demais grava o
        xpath (se houver) e o value, truncado a 200 chars.
        """
        tipo = action.get("type") or "?"
        # 'html'/'url' sao leituras puras (sem value/xpath) -> so poluem o log de
        # navegou/clicou. Nao registra (desnecessario).
        if tipo in ("html", "url"):
            return
        partes = [tipo]
        xp = action.get("xpath")
        if xp:
            partes.append(f"xpath={xp}")
        if tipo == "navigate":
            partes.append(f"url={action.get('value', '')}")
        elif tipo == "key":
            partes.append("value=<oculto>")        # nunca registra o digitado
        else:
            val = action.get("value")
            if val is not None and val != "":
                s = str(val)
                partes.append("value=" + (s[:200] + "..." if len(s) > 200 else s))
        eventos.info(" ".join(partes))

    # -- handlers de cada tipo de acao ---------------------------------
    def _do_navigate(self, action):
        url = action["value"]
        feito = {"v": False}

        def concluir(msg):
            if feito["v"]:                 # garante UMA continuacao so
                return
            feito["v"] = True
            try:
                self.view.loadFinished.disconnect(once)
            except (RuntimeError, TypeError):
                pass
            self.log(f"    {msg}")
            self._continuar()

        def once(ok):
            concluir(f"carregada ({'ok' if ok else 'falha'})")

        self.view.loadFinished.connect(once)
        self.view.load(QUrl(url))
        # algumas navegacoes nao emitem loadFinished (ex.: mudanca so de
        # fragmento '#', ou mesma URL): fallback por tempo para nao travar a
        # fila. 'timeout' (s) e configuravel na action; padrao 30s.
        espera = int(float(action.get("timeout", 30)) * 1000)
        QTimer.singleShot(espera, lambda: concluir("navegacao sem loadFinished (timeout)"))

    def _do_key(self, action):
        xp = json.dumps(action["xpath"])
        val = json.dumps(action.get("value", ""))
        js = JS_BUSCA_XPATH + JS_ESCREVER + (
            "(function () { var el = __byXPath(%s);"
            " if (!el) return 'NAO_ENCONTRADO';"
            " __escrever(el, %s); return 'OK'; })();" % (xp, val)
        )
        self.view.page().runJavaScript(js, self._apos_js)

    def _do_click(self, action):
        xp = json.dumps(action["xpath"])
        js = JS_BUSCA_XPATH + f"""
        (function () {{
          var el = __byXPath({xp});
          if (!el) return "NAO_ENCONTRADO";
          el.click();
          return "OK";
        }})();
        """
        self.view.page().runJavaScript(js, self._apos_js)

    def _do_press(self, action):
        # tecla a pressionar (padrao Enter); alvo: xpath dado ou elemento focado
        tecla = json.dumps(action.get("value", "Enter"))
        xp = json.dumps(action["xpath"]) if action.get("xpath") else "null"
        js = JS_BUSCA_XPATH + f"""
        (function () {{
          var xp = {xp};
          var el = xp ? __byXPath(xp) : (document.activeElement || document.body);
          if (!el) return "NAO_ENCONTRADO";
          var tecla = {tecla};
          var codigos = {{Enter: 13, Tab: 9, Escape: 27, " ": 32}};
          var code = codigos[tecla] || 0;
          var opts = {{key: tecla, code: tecla, keyCode: code,
                       which: code, bubbles: true, cancelable: true}};
          el.focus();
          el.dispatchEvent(new KeyboardEvent('keydown',  opts));
          el.dispatchEvent(new KeyboardEvent('keypress', opts));
          el.dispatchEvent(new KeyboardEvent('keyup',    opts));
          // Enter num campo de formulario costuma submeter o form
          if (tecla === 'Enter' && el.form && el.form.requestSubmit) {{
            el.form.requestSubmit();
          }}
          return "OK";
        }})();
        """
        self.view.page().runJavaScript(js, self._apos_js)

    def _do_type_real(self, action):
        """Digitacao TRUSTED (isTrusted=true): foca o input (via JS) e envia
        QKeyEvents REAIS ao focusProxy da view -> passam pelo pipeline de input
        nativo do Chromium, ao contrario de _do_key (runJavaScript sintetico). Use
        quando setar o value NAO habilita o botao: formularios React/V2 (mercado
        'send', Settle de fundar aldeia, submit do renomear) so validam input
        trusted. Mesma assinatura de 'key' (xpath + value)."""
        xp = json.dumps(action["xpath"])
        val = str(action.get("value", ""))
        js = JS_BUSCA_XPATH + (
            "(function(){var el=__byXPath(%s);if(!el)return 'NAO_ENCONTRADO';"
            "el.focus();try{el.setSelectionRange(0,(el.value||'').length);}"
            "catch(e){if(el.select)el.select();}return 'OK';})();" % xp)

        def depois(status):
            if status == "OK":
                self._digitar_trusted(val)
            self._apos_js(status)

        self.view.page().runJavaScript(js, depois)

    def _digitar_trusted(self, texto):
        """Envia o texto como QKeyEvents reais ao widget que o Chromium usa para
        input (focusProxy da QWebEngineView). O elemento ja deve estar focado/
        selecionado. Cobre digitos e -.,/ (suficiente p/ coords e recursos)."""
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        w = self.view.focusProxy()
        if w is None:
            self.log("    AVISO: sem focusProxy p/ digitacao trusted")
            return
        mapa = {"-": Qt.Key.Key_Minus, ".": Qt.Key.Key_Period,
                ",": Qt.Key.Key_Comma, "/": Qt.Key.Key_Slash}
        for ch in str(texto):
            if ch.isdigit():
                key = Qt.Key.Key_0 + (ord(ch) - ord("0"))
            else:
                key = mapa.get(ch, Qt.Key.Key_unknown)
            for et in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QApplication.sendEvent(
                    w, QKeyEvent(et, int(key),
                                 Qt.KeyboardModifier.NoModifier, ch))

    def _do_exists(self, action):
        # Espera ate 'wait' segundos pelo elemento do xpath. Se aparecer,
        # executa os filhos de 'yes'; se esgotar o tempo, os de 'not'.
        deadline = time.monotonic() + float(action.get("wait", 0))
        xp = json.dumps(action.get("xpath", ""))
        js = JS_BUSCA_XPATH + f"(function () {{ return !!__byXPath({xp}); }})();"
        ind = "  " * (len(self.stack) - 1)

        def checar():
            self.view.page().runJavaScript(js, resultado)

        def resultado(achou):
            if achou:
                self.log(f"{ind}  exists: encontrado -> ramo 'yes'")
                self._entrar_ramo(action.get("yes", []))
            elif time.monotonic() < deadline:
                QTimer.singleShot(EXISTS_POLL_MS, checar)   # tenta de novo
            else:
                self.log(f"{ind}  exists: nao encontrado -> ramo 'not'")
                self._entrar_ramo(action.get("not", []))

        checar()

    def _do_sleep(self, action):
        segundos = float(action.get("value", 0))
        QTimer.singleShot(int(segundos * 1000), self._next)

    def _do_exit(self, action):
        # aguarda 'wait' segundos e desliga o aplicativo inteiro, liberando tudo
        segundos = float(action.get("wait", action.get("value", 0)))
        self.log(f"    exit: desligando em {segundos}s")
        QTimer.singleShot(int(segundos * 1000), self._desligar)

    def _desligar(self):
        self.log("    exit: encerrando e liberando recursos")
        self._hook("finish")          # finish() do run.py antes de sair
        QApplication.quit()           # sai do event loop -> teardown

    def _do_finish(self, action):
        # finaliza a execucao: chama o hook finish() do run.py e encerra o app.
        # da um tempo antes de sair para o finish() concluir trabalho assincrono
        # (ex.: pegar/imprimir o HTML da pagina, que vem por callback).
        espera = float(action.get("wait", 3))
        self.log("    finish: chamando finish() e encerrando")
        self._hook("finish")
        QTimer.singleShot(int(espera * 1000), QApplication.quit)

    def _do_html(self, action):
        # le o HTML da pagina inteira (ou, com 'xpath', o outerHTML do
        # elemento) e o guarda nos resultados do lote -- e assim que um
        # cliente do ServidorComandos "pega o HTML" de volta.
        xp = action.get("xpath")
        if xp:
            js = JS_BUSCA_XPATH + (
                "(function () { var el = __byXPath(%s);"
                " return el ? el.outerHTML : null; })();" % json.dumps(xp)
            )
        else:
            js = "document.documentElement.outerHTML"

        def recebido(html):
            item = {"type": "html", "html": html}
            if action.get("id"):
                item["id"] = action["id"]
            self._resultados.append(item)
            self._continuar()

        self.view.page().runJavaScript(js, recebido)

    def _do_url(self, action):
        # devolve a URL atual da pagina nos resultados do lote -- util para o
        # cliente checar "cheguei na pagina X?" antes de decidir o proximo passo
        def recebido(url):
            item = {"type": "url", "url": url}
            if action.get("id"):
                item["id"] = action["id"]
            self._resultados.append(item)
            self._continuar()

        self.view.page().runJavaScript("window.location.href", recebido)

    def _do_screenshot(self, action):
        # captura a pagina atual num PNG. 'value' = caminho do arquivo; 'largura'
        # (opcional) redimensiona. Tenta a view (conteudo web) e, se sair vazio,
        # cai na janela de topo. Devolve {type:screenshot, ok, path} nos results.
        caminho = action.get("value") or "/tmp/craudiowebot_shot.png"
        ok = False
        try:
            pix = self.view.grab()
            if pix.isNull() or pix.width() < 4:        # view nao renderizou
                janela = self.view.window()
                if janela is not None:
                    pix = janela.grab()
            larg = action.get("largura")
            if larg and not pix.isNull():
                pix = pix.scaledToWidth(int(larg), Qt.SmoothTransformation)
            ok = bool(not pix.isNull() and pix.save(caminho, "PNG"))
        except Exception as e:
            self.log(f"    screenshot ERRO: {e}")
        item = {"type": "screenshot", "ok": ok, "path": caminho}
        if action.get("id"):
            item["id"] = action["id"]
        self._resultados.append(item)
        self._continuar()

    def _do_windows(self, action):
        # action de LEITURA: lista as janelas abertas. Devolve nos resultados
        # {type:windows, janelas:[{index,url,title,ativa}], ativa: <indice>}.
        janela = self.janela
        infos = janela.listar_janelas() if janela is not None else []
        item = {"type": "windows", "janelas": infos,
                "ativa": getattr(janela, "janela_ativa", 0)}
        if action.get("id"):
            item["id"] = action["id"]
        self._resultados.append(item)
        self._continuar()

    def _do_window(self, action):
        # troca a janela ATIVA (a que o servidor dirige) para 'value' (indice).
        janela = self.janela
        ok = False
        if janela is not None:
            try:
                ok = janela.trocar_janela(int(action.get("value")))
            except (TypeError, ValueError):
                self.log("    window: 'value' deve ser o indice da janela")
        item = {"type": "window", "ok": ok,
                "ativa": getattr(janela, "janela_ativa", 0)}
        if action.get("id"):
            item["id"] = action["id"]
        self._resultados.append(item)
        self._continuar()

    def _do_window_close(self, action):
        # fecha uma janela: 'value' (indice) ou, sem value, a ativa. Nao fecha
        # a janela principal (#0) nem a unica.
        janela = self.janela
        ok = False
        if janela is not None:
            v = action.get("value")
            try:
                ok = janela.fechar_janela(int(v) if v is not None and v != "" else None)
            except (TypeError, ValueError):
                self.log("    window_close: 'value' deve ser o indice (ou vazio p/ a ativa)")
        item = {"type": "window_close", "ok": ok,
                "ativa": getattr(janela, "janela_ativa", 0)}
        if action.get("id"):
            item["id"] = action["id"]
        self._resultados.append(item)
        self._continuar()

    def _do_downloads(self, action):
        # action de LEITURA: lista os downloads desta sessao (salvos em
        # ~/Downloads). Cada item: {path, recebido, total, estado}, com estado
        # em solicitado|baixando|concluido|cancelado|interrompido.
        janela = self.janela
        itens = list(getattr(janela, "downloads", []))
        item = {"type": "downloads", "downloads": itens}
        if action.get("id"):
            item["id"] = action["id"]
        self._resultados.append(item)
        self._continuar()

    def _do_eval(self, action):
        # roda o JS em 'value' e devolve o retorno nos resultados do lote.
        # O JS roda no contexto da pagina (mesma origem/cookies), entao serve
        # para chamar APIs internas do site via XHR sincrono. Promises NAO sao
        # aguardadas (runJavaScript pega o valor de retorno imediato).
        def recebido(res):
            item = {"type": "eval", "result": res}
            if action.get("id"):
                item["id"] = action["id"]
            self._resultados.append(item)
            self._continuar()

        self.view.page().runJavaScript(action.get("value", ""), recebido)

    def definir_titulo(self, titulo):
        # troca o ROTULO do titulo da janela de topo (Browser). Nao toca a
        # pagina, entao pode ser chamado fora da fila de lotes -- o
        # ServidorComandos usa isso para aplicar um 'title' avulso na hora,
        # mesmo com um job rodando. O spinner (1o caractere) e o ":{PORTA}" sao
        # mantidos pela janela em _compor_titulo.
        janela = self.janela if self.janela is not None else self.view.window()
        if janela is not None and hasattr(janela, "definir_titulo_base"):
            janela.definir_titulo_base(titulo)
            self.log(f"    title: {titulo}")

    def _do_title(self, action):
        # troca o titulo da janela para 'value'. Util para o cliente do
        # ServidorComandos rotular a janela em tempo de execucao (ex.: mostrar
        # qual etapa esta rodando).
        self.definir_titulo(action.get("value", ""))
        self._continuar()

    def comentar(self, texto):
        # escreve um comentario na caixa de log (self.saida via self.log). Nao
        # toca a pagina, entao -- como 'title' -- pode rodar fora da fila: o
        # ServidorComandos aplica um 'comment' avulso na hora, mesmo com um
        # job rodando, para anotar o log em tempo real.
        self.log(f"[COMENTARIO] {texto}")

    def _do_comment(self, action):
        # registra um comentario no log para 'value'.
        self.comentar(action.get("value", ""))
        self._continuar()

    def _do_zoom(self, action):
        # ajusta o nivel de zoom da view (QWebEngineView.setZoomFactor). 'value'
        # e a porcentagem (ex.: 150 = 150%); ausente/vazio = 100%. Aceita numero
        # ou string ("150" ou "150%"). O fator do Qt vale de 0.25 a 5.0, entao a
        # porcentagem e limitada a 25%..500%. Vale para a pagina ja carregada e
        # persiste nas proximas navegacoes do mesmo perfil.
        bruto = action.get("value", "")
        if bruto is None or bruto == "":
            percent = 100.0
        else:
            try:
                percent = float(str(bruto).strip().rstrip("%"))
            except ValueError:
                self.log(f"    zoom: valor invalido {bruto!r}; ignorado")
                self._continuar()
                return
        # limita ao intervalo aceito pelo Qt (0.25..5.0)
        percent = max(25.0, min(500.0, percent))
        self.view.setZoomFactor(percent / 100.0)
        self.log(f"    zoom: {percent:g}%")
        self._continuar()

    def _do_user_agent(self, action):
        # troca o User-Agent anunciado pelo perfil. Vale para as PROXIMAS
        # requisicoes (navegacoes/recargas): a pagina ja carregada so muda ao
        # navegar/recarregar. Permite ao cliente do --servir definir o UA em
        # tempo de execucao e ao job.json troca-lo no meio da sequencia.
        ua = action.get("value", "")
        if not ua:
            self.log("    user_agent: vazio; ignorado")
            self._continuar()
            return
        self.view.page().profile().setHttpUserAgent(ua)
        self.log(f"    user_agent: {ua}")
        self._continuar()

    def _dir_perfil(self):
        """Diretorio do perfil em disco; '' se o perfil for em memoria."""
        try:
            return self.view.page().profile().persistentStoragePath()
        except Exception:
            return ""

    def _do_save_profile(self, action):
        # empacota os arquivos do perfil atual num .tar.gz. 'value' = caminho
        # do arquivo (aceita ~). O diretorio de destino e criado se faltar.
        # Best-effort: o Chromium pode estar mexendo nos arquivos; itens que
        # somem/erram durante a leitura sao pulados, sem travar o job.
        destino = Path(action.get("value", "")).expanduser()
        perfil = self._dir_perfil()
        if not perfil or not Path(perfil).is_dir():
            self.log("    save_profile: perfil em memoria/inexistente; nada a salvar")
            self._continuar()
            return
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            base = Path(perfil)
            n = 0
            with tarfile.open(destino, "w:gz") as tar:
                for item in base.rglob("*"):
                    try:
                        # recursive=False: rglob ja visita cada item uma vez
                        tar.add(item, arcname=item.relative_to(base),
                                recursive=False)
                        if item.is_file():
                            n += 1
                    except (FileNotFoundError, OSError) as e:
                        self.log(f"      (pulado {item.name}: {e})")
            self.log(f"    save_profile: {n} arquivo(s) -> {destino}")
        except Exception as e:
            self.log(f"    save_profile: ERRO: {e}")
        self._continuar()

    def _do_load_profile(self, action):
        # restaura um perfil salvo de um .tar.gz para o diretorio do perfil.
        # 'value' = caminho do arquivo (aceita ~). Se o arquivo nao existir,
        # apenas avisa e segue (nao eleva erro).
        origem = Path(action.get("value", "")).expanduser()
        if not origem.is_file():
            self.log(f"    load_profile: arquivo .tar.gz nao existe: {origem}")
            self._continuar()
            return
        perfil = self._dir_perfil()
        if not perfil:
            self.log("    load_profile: perfil em memoria; sem destino em disco")
            self._continuar()
            return
        try:
            Path(perfil).mkdir(parents=True, exist_ok=True)
            with tarfile.open(origem, "r:gz") as tar:
                try:
                    tar.extractall(perfil, filter="data")   # Python 3.12+
                except TypeError:
                    tar.extractall(perfil)                   # versoes antigas
            self.log(f"    load_profile: restaurado de {origem} -> {perfil}")
            self.log("      (navegue/recarregue para o perfil ter efeito)")
        except Exception as e:
            self.log(f"    load_profile: ERRO: {e}")
        self._continuar()

    def _do_clear_profile(self, action):
        # exclui dados do perfil. 'value':
        #   "sessao" (padrao) -> limpa cookies + cache + links visitados
        #                        (equivale a um logout); efeito imediato.
        #   "disco"           -> alem da sessao, apaga a PASTA do perfil em disco.
        # rmtree e best-effort: o Chromium ainda tem arquivos abertos, entao o
        # wipe em disco so tem efeito pleno ao reiniciar; itens travados sao
        # ignorados (ignore_errors). Nunca eleva erro / trava o lote.
        modo = (action.get("value") or "sessao").lower()
        try:
            profile = self.view.page().profile()
            profile.cookieStore().deleteAllCookies()
            profile.clearHttpCache()
            profile.clearAllVisitedLinks()
            self.log("    clear_profile: cookies + cache + links limpos (sessao)")
        except Exception as e:
            self.log(f"    clear_profile: ERRO ao limpar sessao: {e}")
        if modo == "disco":
            perfil = self._dir_perfil()
            if not perfil or not Path(perfil).is_dir():
                self.log("    clear_profile: perfil em memoria/inexistente; nada em disco")
            else:
                shutil.rmtree(perfil, ignore_errors=True)
                self.log(f"    clear_profile: pasta apagada -> {perfil}")
                self.log("      (reinicie para o wipe em disco ter efeito pleno)")
        self._continuar()

    # callback comum para key/click
    def _apos_js(self, status):
        if status == "NAO_ENCONTRADO":
            self.log("    AVISO: elemento (xpath) nao encontrado")
        self._continuar()


class AutoDialogPage(QWebEnginePage):
    """Page que auto-aceita os dialogos JS (alert/confirm/prompt).

    Sem isso, um alert()/confirm() da pagina BLOQUEIA o event loop esperando
    o usuario clicar - o que trava a automacao (em headless, trava de vez).
    """

    def __init__(self, profile, parent=None, log=None, ao_abrir_janela=None):
        super().__init__(profile, parent)
        self._log = log or (lambda *a: None)
        # callback (do Browser) que cria/registra uma nova JANELA e devolve a
        # page destino. Quando None, o pedido de nova janela e ignorado (Qt).
        self._ao_abrir_janela = ao_abrir_janela

    def createWindow(self, _tipo):
        """Chamado pelo QtWebEngine quando a pagina pede uma JANELA/aba nova
        (window.open, target=_blank, redirect de relatorio). Sem isso o pedido
        e descartado silenciosamente. Abre uma janela nova (JanelaWeb) e devolve
        a page dela; o QtWebEngine carrega o conteudo pedido nessa page."""
        if self._ao_abrir_janela is not None:
            return self._ao_abrir_janela(_tipo)
        return super().createWindow(_tipo)

    def javaScriptAlert(self, origin, msg):
        self._log(f"[dialogo] alert: {msg}")          # fecha (aceita)

    def javaScriptConfirm(self, origin, msg):
        self._log(f"[dialogo] confirm: {msg} -> aceito")
        return True                                    # sempre OK

    def javaScriptPrompt(self, origin, msg, defaultValue):
        self._log(f"[dialogo] prompt: {msg} -> {defaultValue!r}")
        return True, defaultValue                      # aceita com o padrao


class JanelaWeb(QMainWindow):
    """Janela web ADICIONAL (alem da principal).

    Criada quando a pagina pede uma janela/aba nova (window.open, target=_blank,
    redirect de relatorio) -- ver Browser._abrir_janela. Tem view propria e
    mostra a sua page o tempo todo (cada janela e visivel, diferente do antigo
    modelo de abas que trocava a page de uma view so). Compartilha profile e
    cookies com a principal. O controlador (Browser) mantem o registro das
    janelas, o log e a janela ATIVA que o servidor dirige. Fechar a janela (pela
    action 'window_close' ou pelo usuario) avisa o controlador via closeEvent."""

    def __init__(self, page, controlador, indice):
        super().__init__()
        # WA_DeleteOnClose: ao fechar, o Qt destroi a janela (e sua view). A page
        # NAO e filha da view (e do controlador), entao e liberada a parte.
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(1024, 860)
        self.controlador = controlador
        self._indice = indice
        self.view = QWebEngineView()
        self.view.setPage(page)
        self.setCentralWidget(self.view)
        self.setWindowTitle(f"janela #{indice}")
        # o titulo segue o titulo da pagina, p/ dar contexto visual
        self.view.titleChanged.connect(
            lambda t, i=indice: self.setWindowTitle(f"#{i} {t}" if t else f"janela #{i}")
        )

    def closeEvent(self, evento):
        # ao fechar (pela action ou pelo usuario), desregistra no controlador
        try:
            self.controlador._janela_fechada(self)
        except Exception:
            pass
        super().closeEvent(evento)


class Browser(QMainWindow):
    def __init__(self, job: dict, data_dir: str | None = None,
                 job_dir: str | None = None, ublock: bool = True):
        super().__init__()
        self.resize(1024, 860)
        self.job_dir = job_dir
        self.ublock = ublock
        # porta do modo --servir (definida no main); None quando nao serve.
        # Mostrada no titulo como ":{PORTA}" e mantida pelo definir_titulo.
        self.porta_servir = None
        # Titulo da janela = "<spinner> <base> [:PORTA]". 'base' e o rotulo
        # (trocavel pela action 'title'); o spinner e o 1o caractere e avanca a
        # cada acao executada. Composto sempre por _compor_titulo.
        self.titulo_base = f"Browser PySide6 - job: {job.get('name', '')}"
        self._spinner_idx = 0
        self._compor_titulo()
        # User-Agent do perfil: campo "user_agent" do job.json sobrescreve o
        # default. Em runtime, a action 'user_agent' (e o cliente do --servir)
        # pode troca-lo via profile.setHttpUserAgent.
        self.user_agent = job.get("user_agent") or USER_AGENT

        self.view = QWebEngineView()

        # --- Perfil ---
        # Com -d: perfil persistente (cache, cookies e storage salvos no disco
        #         e reaproveitados entre execucoes).
        # Sem -d: perfil persistente "anonimo" em memoria.
        if data_dir:
            # expanduser() resolve ~/ para o home; resolve() torna absoluto/fixo
            d = Path(data_dir).expanduser().resolve()
            d.mkdir(parents=True, exist_ok=True)
            self._perfil_info = f"persistente em {d}"
            self.profile = QWebEngineProfile("craudiowebot", self)
            self.profile.setPersistentStoragePath(str(d))
            self.profile.setCachePath(str(d))
            self.profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )
        else:
            self._perfil_info = "em memoria (sem -d)"
            self.profile = QWebEngineProfile(self)

        # JANELAS: registro das janelas web. self e a janela #0 (principal: tem
        # log, navbar, runner e servidor). Janelas extras (window.open,
        # target=_blank, redirect) sao JanelaWeb, criadas por _abrir_janela via
        # AutoDialogPage.createWindow; todas compartilham o mesmo profile
        # (login/cookies). O servidor dirige sempre a janela ATIVA; a action
        # 'window' troca qual e a ativa (repontando self.runner.view).
        self.janelas = [self]
        self.janela_ativa = 0
        # downloads desta sessao (salvos em ~/Downloads), p/ a action 'downloads'.
        self.downloads = []
        # auto-aceita downloads e os salva em ~/Downloads (ver _ao_baixar). Sem
        # isso o QtWebEngine descarta o download (era a limitacao antiga).
        self.profile.downloadRequested.connect(self._ao_baixar)

        # page sempre criada com nosso profile (profile deve viver enquanto a page)
        # AutoDialogPage auto-aceita dialogos JS para nao travar a automacao
        self.page = self._criar_page()
        self.view.setPage(self.page)

        # no shutdown, libera a page ANTES do profile (ordem correta no QtWebEngine)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._liberar)

        # --- Perfil bem permissivo: roda JS, carrega imagens, toca video, etc. ---
        self._configurar_permissivo()

        # --- barra de navegacao: voltar/avancar/recarregar + URL + Ir ---
        b_voltar = QPushButton("←")      # <-
        b_avancar = QPushButton("→")     # ->
        b_recarregar = QPushButton("↻")  # reload
        b_voltar.setToolTip("Voltar")
        b_avancar.setToolTip("Avancar")
        b_recarregar.setToolTip("Recarregar")
        b_voltar.clicked.connect(self.view.back)
        b_avancar.clicked.connect(self.view.forward)
        b_recarregar.clicked.connect(self.view.reload)

        self.barra_url = QLineEdit()
        self.barra_url.setPlaceholderText("Digite uma URL e clique em Ir (ou Enter)")
        self.barra_url.returnPressed.connect(self._ir)
        botao_ir = QPushButton("Ir")
        botao_ir.clicked.connect(self._ir)

        barra = QHBoxLayout()
        barra.addWidget(b_voltar)
        barra.addWidget(b_avancar)
        barra.addWidget(b_recarregar)
        barra.addWidget(self.barra_url, stretch=1)
        barra.addWidget(botao_ir)
        # mantem o campo sincronizado com a pagina atual
        self.view.urlChanged.connect(
            lambda u: self.barra_url.setText(u.toString())
        )

        self.saida = QPlainTextEdit()
        self.saida.setReadOnly(True)
        self.saida.setMaximumHeight(160)

        layout = QVBoxLayout()
        layout.addLayout(barra)
        layout.addWidget(self.view, stretch=1)
        layout.addWidget(self.saida)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        # Extensoes Chromium (uBlock Origin) -- instala no profile assim que a
        # caixa de log existe. Assincrono: nao bloqueia a partida do job.
        carregar_extensoes(self.profile, self._log, habilitado=self.ublock)

        # Se houver run.py na pasta do job, instancia Job(browser, json) para
        # receber os hooks pre_action/pos_action/finish durante a execucao.
        self.job_obj = carregar_run_py(job_dir, self, job) if job_dir else None
        self.runner = JobRunner(self.view, self._log, job_obj=self.job_obj,
                                janela=self)

        def iniciar():
            self._log(f"perfil: {self._perfil_info}")
            self._log(f"user-agent: {self.user_agent}")
            if self.job_obj is not None:
                self._log("run.py: Job() carregado (hooks ativos)")
            self.runner.run(job)

        # comeca o job assim que o event loop estiver rodando
        QTimer.singleShot(0, iniciar)

    def _ir(self):
        """Navega para a URL digitada na barra (completa o esquema se faltar)."""
        url = self.barra_url.text().strip()
        if not url:
            return
        if "://" not in url:
            url = "https://" + url
        self.navegar(url)

    def _log(self, msg):
        # toda mensagem leva o horario [HH:MM:SS] na frente
        self.saida.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # --- titulo da janela: "<spinner> <base> [:PORTA]" ---

    def _compor_titulo(self):
        """Reescreve o titulo a partir do spinner atual, da base e da porta."""
        girando = SPINNER[self._spinner_idx % len(SPINNER)]
        porta = f" :{self.porta_servir}" if self.porta_servir is not None else ""
        self.setWindowTitle(f"{girando} {self.titulo_base}{porta}")

    def girar_titulo(self):
        """Avanca o spinner (1o caractere do titulo) -- 1 passo por acao."""
        self._spinner_idx += 1
        self._compor_titulo()

    def definir_titulo_base(self, base):
        """Troca o rotulo do titulo (action 'title') mantendo spinner e porta."""
        self.titulo_base = base
        self._compor_titulo()

    # --- facilitadores para os scripts (run.py) manipularem a pagina ---
    # Leitura e assincrona: o valor chega no callback. Sem callback, loga.
    # Atuam sempre na JANELA ATIVA (a mesma que o servidor dirige).

    def _view_atual(self):
        """View da janela ATIVA. janelas[0] e o proprio Browser (janela
        principal), cujo .view e a view da pagina principal; janelas extras sao
        JanelaWeb, tambem com .view propria."""
        return self.janelas[self.janela_ativa].view

    def navegar(self, url, callback=None):
        """Carrega uma URL na janela ativa. Como o load e assincrono, o
        callback(ok) (opcional) e chamado quando a pagina termina de carregar."""
        view = self._view_atual()
        if callback is not None:
            def once(ok):
                view.loadFinished.disconnect(once)
                callback(ok)
            view.loadFinished.connect(once)
        view.load(QUrl(url))

    def _avaliar(self, xpath, expr, callback, rotulo):
        """Acha o elemento do xpath e avalia 'expr' (JS) sobre 'el';
        entrega o resultado no callback (ou loga, se callback for None)."""
        if callback is None:
            callback = lambda v: self._log(f"{rotulo} {xpath} = {v!r}")
        js = JS_BUSCA_XPATH + (
            "(function () { var el = __byXPath(%s);"
            " return el ? (%s) : null; })();" % (json.dumps(xpath), expr)
        )
        self._view_atual().page().runJavaScript(js, callback)

    def ler_valor(self, xpath, callback=None):
        """Le o .value do elemento (inputs/textarea)."""
        self._avaliar(xpath, "el.value", callback, "valor de")

    def ler_texto(self, xpath, callback=None):
        """Le o textContent do elemento."""
        self._avaliar(xpath, "el.textContent", callback, "texto de")

    def ler_atributo(self, xpath, attr, callback=None):
        """Le o atributo 'attr' do elemento."""
        self._avaliar(
            xpath, "el.getAttribute(%s)" % json.dumps(attr), callback,
            f"atributo {attr} de",
        )

    def pegar_elemento(self, xpath, callback=None):
        """Pega o elemento como HTML (outerHTML) - DOM nao cruza p/ o Python."""
        self._avaliar(xpath, "el.outerHTML", callback, "elemento")

    def pegar_html(self, callback=None):
        """Pega o HTML inteiro da pagina (outerHTML do documento)."""
        if callback is None:
            callback = lambda h: self._log(h)
        self._view_atual().page().runJavaScript(
            "document.documentElement.outerHTML", callback
        )

    def escrever_elemento(self, xpath, valor, enter=False):
        """Escreve 'valor' no elemento (dispara input/change). Se enter=True,
        pressiona Enter depois (e submete o form, se houver)."""
        enter_js = ""
        if enter:
            enter_js = (
                "var o={key:'Enter',code:'Enter',keyCode:13,which:13,"
                "bubbles:true,cancelable:true};"
                "el.dispatchEvent(new KeyboardEvent('keydown',o));"
                "el.dispatchEvent(new KeyboardEvent('keypress',o));"
                "el.dispatchEvent(new KeyboardEvent('keyup',o));"
                "if(el.form&&el.form.requestSubmit)el.form.requestSubmit();"
            )
        js = JS_BUSCA_XPATH + JS_ESCREVER + (
            "(function () { var el = __byXPath(%s); if(!el) return false;"
            " __escrever(el, %s); %s return true; })();"
            % (json.dumps(xpath), json.dumps(valor), enter_js)
        )
        self._view_atual().page().runJavaScript(js)

    def _esperar_estavel(self, js, callback, estavel, timeout):
        """Laco de polling: roda 'js' (que retorna JSON {texto, pronto})
        repetidamente ate o texto ficar 'estavel' segundos sem mudar (e pronto),
        ou estourar o 'timeout'. Entrega o texto final no callback."""
        inicio = time.monotonic()
        estado = {"texto": None, "mudou": inicio}

        def passo():
            self._view_atual().page().runJavaScript(js, recebeu)

        def recebeu(raw):
            d = json.loads(raw) if raw else {}
            t = d.get("texto")
            pronto = d.get("pronto", True)
            agora = time.monotonic()
            if t != estado["texto"]:
                estado["texto"] = t
                estado["mudou"] = agora
            estavel_ok = bool(t) and (agora - estado["mudou"]) >= estavel
            if (estavel_ok and pronto) or (agora - inicio) >= timeout:
                callback(estado["texto"])
            else:
                QTimer.singleShot(700, passo)

        passo()

    def esperar_resposta(self, xpath, callback, concluido_xpath=None,
                         estavel=1.5, timeout=180.0):
        """Aguarda o ULTIMO elemento do xpath parar de mudar e entrega o texto
        final no callback.

          concluido_xpath: se dado, so considera pronto quando esse elemento
                           existir (ex.: o botao 'copiar' que so aparece quando
                           a resposta termina) - evita parar na fase 'Thinking'.
        """
        cx = json.dumps(concluido_xpath) if concluido_xpath else "null"
        js = (
            "(function () {"
            " var r = document.evaluate(%s, document, null,"
            " XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);"
            " var el = r.snapshotLength ? r.snapshotItem(r.snapshotLength-1) : null;"
            " var pronto = true; var cx = %s;"
            " if (cx) { pronto = !!document.evaluate(cx, document, null,"
            "   XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; }"
            " return JSON.stringify({texto: el ? el.innerText : null,"
            "   pronto: pronto}); })();" % (json.dumps(xpath), cx)
        )
        self._esperar_estavel(js, callback, estavel, timeout)

    def esperar_pagina(self, callback, concluido_xpath=None,
                       estavel=2.0, timeout=180.0):
        """Aguarda a PAGINA INTEIRA parar de mudar (streaming terminar) e
        retorna todo o texto da pagina (document.body.innerText) no callback.

        Mais robusto que mirar um elemento: durante o streaming o texto da
        pagina muda; quando estabiliza, a resposta terminou. Use
        concluido_xpath para exigir tambem um marcador de 'pronto'.
        """
        cx = json.dumps(concluido_xpath) if concluido_xpath else "null"
        js = (
            "(function () {"
            " var pronto = true; var cx = %s;"
            " if (cx) { pronto = !!document.evaluate(cx, document, null,"
            "   XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; }"
            " return JSON.stringify({texto: document.body.innerText,"
            "   pronto: pronto}); })();" % cx
        )
        self._esperar_estavel(js, callback, estavel, timeout)

    def clicar_elemento(self, xpath):
        """Clica no elemento apontado pelo xpath."""
        js = JS_BUSCA_XPATH + (
            "(function () { var el = __byXPath(%s);"
            " if(el) el.click(); return !!el; })();" % json.dumps(xpath)
        )
        self._view_atual().page().runJavaScript(js)

    def _liberar(self):
        """Libera as pages (de todas as janelas) e o profile na ordem certa
        (pages primeiro)."""
        try:
            for jw in getattr(self, "janelas", [self]):
                pg = jw.view.page()
                jw.view.setPage(None)
                if pg is not None:
                    pg.deleteLater()
        except Exception:
            pass

    def _configurar_permissivo(self):
        """Liga tudo: JavaScript, imagens, video com autoplay, WebGL,
        fullscreen, clipboard, conteudo inseguro, etc., e auto-concede
        as permissoes que a pagina pedir (camera, microfone, ...)."""
        # User-Agent (anunciado em cada requisicao)
        self.profile.setHttpUserAgent(self.user_agent)

        WA = QWebEngineSettings.WebAttribute
        s = self.profile.settings()

        # atributos que queremos LIGADOS
        ligar = [
            WA.JavascriptEnabled,
            WA.AutoLoadImages,
            WA.AutoLoadIconsForPage,
            WA.LocalStorageEnabled,
            WA.PluginsEnabled,
            WA.PdfViewerEnabled,
            WA.WebGLEnabled,
            WA.Accelerated2dCanvasEnabled,
            WA.FullScreenSupportEnabled,
            WA.ScreenCaptureEnabled,
            WA.JavascriptCanOpenWindows,
            WA.JavascriptCanAccessClipboard,
            WA.JavascriptCanPaste,
            WA.AllowWindowActivationFromJavaScript,
            WA.AllowRunningInsecureContent,
            WA.AllowGeolocationOnInsecureOrigins,
            WA.LocalContentCanAccessRemoteUrls,
            WA.LocalContentCanAccessFileUrls,
            WA.ReadingFromCanvasEnabled,
            WA.DnsPrefetchEnabled,
            WA.ErrorPageEnabled,
            WA.ScrollAnimatorEnabled,
            WA.ShowScrollBars,
            WA.TouchEventsApiEnabled,
            WA.BackForwardCacheEnabled,
        ]
        for a in ligar:
            s.setAttribute(a, True)

        # autoplay de video/audio sem exigir clique do usuario
        s.setAttribute(WA.PlaybackRequiresUserGesture, False)
        # a permissao por-pagina (permissionRequested) e ligada em _criar_page,
        # para valer tambem nas janelas novas.

    def _criar_page(self):
        """Cria uma AutoDialogPage no nosso profile, ja permissiva e capaz de
        abrir janelas. Usada para a janela inicial e para cada janela nova."""
        page = AutoDialogPage(self.profile, self, log=self._log,
                              ao_abrir_janela=self._abrir_janela)
        # auto-concede permissoes pedidas pela pagina (API nova, Qt 6.8+)
        page.permissionRequested.connect(self._conceder_permissao)
        return page

    def _abrir_janela(self, _tipo=None):
        """Cria uma JANELA nova (window.open, target=_blank, redirect) com page
        propria no mesmo profile/permissoes. A janela aparece, mas NAO vira a
        ativa automaticamente (o servidor segue na janela atual ate um 'window'
        trocar). Devolve a page para o QtWebEngine carregar nela."""
        page = self._criar_page()
        indice = len(self.janelas)
        jw = JanelaWeb(page, controlador=self, indice=indice)
        self.janelas.append(jw)
        jw.show()
        self._log(f"[janela] nova janela #{indice} aberta (total: "
                  f"{len(self.janelas)}; ativa segue #{self.janela_ativa})")
        return page

    def listar_janelas(self):
        """Lista as janelas abertas: [{index, url, title, ativa}]."""
        infos = []
        for i, jw in enumerate(self.janelas):
            pg = jw.view.page()
            infos.append({
                "index": i,
                "url": pg.url().toString() if pg is not None else "",
                "title": pg.title() if pg is not None else "",
                "ativa": (i == self.janela_ativa),
            })
        return infos

    def trocar_janela(self, indice):
        """Torna a janela 'indice' a ATIVA (a que o servidor e o run.py dirigem)
        repontando o runner para a view dela e trazendo-a para frente."""
        if not (0 <= indice < len(self.janelas)):
            self._log(f"[janela] indice invalido: {indice} "
                      f"(janelas: {len(self.janelas)})")
            return False
        self.janela_ativa = indice
        jw = self.janelas[indice]
        self.runner.view = jw.view          # facilitadores usam _view_atual()
        jw.raise_()
        jw.activateWindow()
        self._log(f"[janela] ativa agora: #{indice}")
        return True

    def fechar_janela(self, indice=None):
        """Fecha a janela 'indice' (default: a ativa). Nao fecha a principal
        (#0) nem a unica. O closeEvent da JanelaWeb chama _janela_fechada, que
        desregistra e reajusta a ativa."""
        if indice is None:
            indice = self.janela_ativa
        if not (0 <= indice < len(self.janelas)):
            self._log(f"[janela] indice invalido: {indice}")
            return False
        if indice == 0:
            self._log("[janela] nao fecho a janela principal (#0)")
            return False
        self.janelas[indice].close()        # dispara _janela_fechada
        return True

    def _janela_fechada(self, jw):
        """Desregistra uma JanelaWeb fechada (pela action ou pelo usuario) e
        reajusta a janela ativa. Nunca e a principal (#0, que e o Browser)."""
        if jw not in self.janelas:
            return
        indice = self.janelas.index(jw)
        self.janelas.remove(jw)
        pg = jw.view.page()
        jw.view.setPage(None)
        if pg is not None:
            pg.deleteLater()
        # reajusta a ativa: se fechou a ativa, cai para a principal (#0); se a
        # ativa vinha depois da fechada, desloca um para tras.
        if self.janela_ativa == indice:
            self.janela_ativa = 0
        elif self.janela_ativa > indice:
            self.janela_ativa -= 1
        self.janela_ativa = min(self.janela_ativa, len(self.janelas) - 1)
        self.runner.view = self.janelas[self.janela_ativa].view
        self._log(f"[janela] fechada #{indice} (restam {len(self.janelas)}; "
                  f"ativa #{self.janela_ativa})")

    def _ao_baixar(self, download):
        """Aceita um download e o salva em ~/Downloads (cria a pasta se faltar),
        registrando o andamento em self.downloads para a action 'downloads'.
        Sem accept() o QtWebEngine descarta o download (era a limitacao antiga)."""
        try:
            destino = Path(DIR_DOWNLOADS).expanduser()
            destino.mkdir(parents=True, exist_ok=True)
            download.setDownloadDirectory(str(destino))
            # alguns servidores sugerem o nome com o caminho do servidor
            # ("/fs.../arquivos/.../X.PDF"); o Qt tentaria criar essa pasta
            # localmente e cancela o download. Fica so o basename.
            nome = (download.suggestedFileName() or "download").replace("\\", "/")
            nome = nome.rsplit("/", 1)[-1] or "download"
            download.setDownloadFileName(nome)
            download.accept()
            caminho = str(destino / download.downloadFileName())
            registro = {"path": caminho, "recebido": 0, "total": 0,
                        "estado": "baixando"}
            self.downloads.append(registro)
            self._log(f"[download] iniciado: {caminho}")

            def atualizar(*_):
                try:
                    registro["recebido"] = download.receivedBytes()
                    registro["total"] = download.totalBytes()
                    registro["estado"] = ESTADO_DOWNLOAD.get(
                        download.state(), registro["estado"])
                except Exception:
                    pass

            def mudou_estado(*_):
                atualizar()
                self._log(f"[download] {registro['estado']}: {registro['path']}")

            # os nomes dos sinais variam entre versoes do Qt; conecta o que houver
            for nome_sinal, slot in (("receivedBytesChanged", atualizar),
                                     ("stateChanged", mudou_estado),
                                     ("isFinishedChanged", mudou_estado)):
                sinal = getattr(download, nome_sinal, None)
                if sinal is not None:
                    try:
                        sinal.connect(slot)
                    except Exception:
                        pass
        except Exception as e:
            self._log(f"[download] ERRO ao iniciar: {e}")

    def _conceder_permissao(self, permissao):
        try:
            permissao.grant()
            self._log(f"    [perm] concedida: {permissao.permissionType()}")
        except Exception as e:
            self._log(f"    [perm] erro ao conceder: {e}")


class ServidorComandos(QObject):
    """Servidor TCP que recebe actions em JSON e as executa em tempo real.

    Permite que OUTRO processo comande o browser enquanto ele esta aberto,
    com o mesmo formato de action do job.json. Protocolo: NDJSON (um JSON
    por linha; as respostas voltam na ordem dos pedidos).

      pedido:   uma action   {"type": "navigate", "value": "https://..."}
                ou um lote   {"actions": [ {...}, {...} ]}
      resposta: {"ok": true, "resultados": [...]}   ao terminar o lote;
                'resultados' traz as saidas das acoes type "html"
                {"ok": false, "erro": "..."}        se o pedido for invalido

    Os pedidos entram na MESMA fila do JobRunner: rodam um por vez, depois
    do job inicial (se houver). So escuta em 127.0.0.1 -- cliente remoto
    deve usar tunel (ssh -L). Veja cliente.py e examples/.
    """

    def __init__(self, runner, log, porta, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.log = log
        self._buffers = {}    # socket -> bytes de linha ainda incompleta
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._nova_conexao)
        self.server.listen(QHostAddress("127.0.0.1"), porta)

    def _nova_conexao(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._ler(s))
            sock.disconnected.connect(lambda s=sock: self._desconectar(s))

    def _desconectar(self, sock):
        self._buffers.pop(sock, None)
        sock.deleteLater()

    def _ler(self, sock):
        if sock not in self._buffers:
            return
        self._buffers[sock] += bytes(sock.readAll())
        while b"\n" in self._buffers.get(sock, b""):
            linha, self._buffers[sock] = self._buffers[sock].split(b"\n", 1)
            if linha.strip():
                self._processar(sock, linha)

    def _processar(self, sock, linha):
        try:
            pedido = json.loads(linha.decode("utf-8"))
            actions = pedido["actions"] if "actions" in pedido else [pedido]
            if not isinstance(actions, list):
                raise ValueError("'actions' deve ser uma lista")
        except Exception as e:
            # lote vazio so para a resposta de erro sair NA ORDEM dos pedidos
            erro = f"pedido invalido: {e}"
            self.runner.executar([], lambda _r, s=sock, m=erro:
                                 self._responder(s, {"ok": False, "erro": m}))
            return
        # acoes avulsas que so mexem na janela/log (nao na pagina) furam a fila:
        # aplicadas na hora, o cliente as usa no meio de um job longo sem esperar
        # o lote em andamento terminar.
        if len(actions) == 1 and actions[0].get("type") in ("title", "comment"):
            ac = actions[0]
            if ac.get("type") == "title":
                self.runner.definir_titulo(ac.get("value", ""))
            else:
                self.runner.comentar(ac.get("value", ""))
            self._responder(sock, {"ok": True, "resultados": []})
            return
        self.runner.executar(actions, lambda resultados, s=sock:
                             self._responder(s, {"ok": True,
                                                 "resultados": resultados}))

    def _responder(self, sock, resposta):
        if sock not in self._buffers:   # cliente ja desconectou; descarta
            return
        dados = json.dumps(resposta, ensure_ascii=False).encode("utf-8")
        sock.write(dados + b"\n")


def carregar_run_py(job_dir, browser, job):
    """Se existir <job_dir>/run.py com uma classe Job, devolve Job(browser, job).

    A classe Job pode definir os hooks (todos opcionais):
      - pre_action(self, action)  -> antes de cada acao
      - pos_action(self, action)  -> depois de cada acao terminar
      - finish(self)              -> ao fim do job
    O browser recebido permite manipular a pagina (browser.view, browser.page).
    """
    run_py = Path(job_dir) / "run.py"
    if not run_py.is_file():
        return None
    spec = importlib.util.spec_from_file_location("job_run", run_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "Job"):
        return None
    return mod.Job(browser, job)


def resolver_job(caminho: str):
    """Resolve um job e devolve (job_dict, pasta_do_job).

    Aceita:
      - a pasta do job        -> jobs/job0       (le jobs/job0/job.json)
      - o arquivo job.json    -> jobs/job0/job.json
      - so o nome do job      -> job0            (le jobs/job0/job.json)
    """
    p = Path(caminho).expanduser()
    if p.is_dir():
        arquivo = p / "job.json"
    elif p.is_file():
        arquivo = p
    else:
        arquivo = Path("jobs") / caminho / "job.json"  # tenta como nome
    if not arquivo.is_file():
        raise SystemExit(f"Job nao encontrado: {caminho}")
    job = json.loads(arquivo.read_text(encoding="utf-8"))
    return job, arquivo.parent.resolve()


def primeiro_job() -> str:
    """Fallback: primeira pasta jobs/<nome>/job.json quando -s nao e informado."""
    jobs = sorted(Path("jobs").glob("*/job.json"))
    if not jobs:
        raise SystemExit("Nenhum job em jobs/*/job.json e nenhum -s informado.")
    return str(jobs[0].parent)


def configurar_log_eventos(porta) -> str:
    """Configura o logger de eventos para /tmp/browser_{porta}_events.log.

    'porta' e a porta do --servir; sem --servir usa o PID (para o arquivo nao
    colidir entre execucoes). Append entre execucoes. Devolve o caminho.
    """
    caminho = f"/tmp/browser_{porta}_events.log"
    handler = logging.FileHandler(caminho, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    eventos.setLevel(logging.INFO)
    eventos.addHandler(handler)
    eventos.propagate = False
    return caminho


def _parse_proxy(spec: str) -> QNetworkProxy:
    """Converte 'spec' numa QNetworkProxy. Formatos aceitos:
      host:porta                         -> assume HTTP
      http://host:porta                  -> HTTP
      http://usuario:senha@host:porta    -> HTTP com autenticacao
      socks5://host:porta                -> SOCKS5
    """
    spec = spec.strip()
    if "://" not in spec:
        spec = "http://" + spec          # sem esquema -> assume HTTP
    u = urlparse(spec)
    tipo = QNetworkProxy.ProxyType.HttpProxy
    if u.scheme in ("socks5", "socks"):
        tipo = QNetworkProxy.ProxyType.Socks5Proxy
    proxy = QNetworkProxy(tipo, u.hostname or "", u.port or 0)
    if u.username:
        proxy.setUser(u.username)
    if u.password:
        proxy.setPassword(u.password)
    return proxy


def configurar_proxy(spec) -> str | None:
    """Define o proxy de aplicacao (vale para TODO o QtWebEngine).

    'spec' pode ser uma string ("http://[usuario:senha@]host:porta", socks5://
    ou 'host:porta' sem esquema) ou uma LISTA de specs -- nesse caso sorteia
    uma. Sem spec, nao mexe (o QtWebEngine usa o proxy do sistema). Devolve uma
    descricao 'host:porta' do proxy escolhido (sem usuario/senha), ou None.
    """
    if not spec:
        return None
    if isinstance(spec, (list, tuple)):
        specs = [s for s in spec if s]
        if not specs:
            return None
        spec = random.choice(specs)      # array no job -> sorteia 1
    proxy = _parse_proxy(spec)
    QNetworkProxy.setApplicationProxy(proxy)
    return f"{proxy.hostName()}:{proxy.port()}"


# Diretorio das extensoes Chromium empacotadas (uBlock Origin etc.).
EXTENSOES_DIR = Path(__file__).parent / "data" / "extensions"


def carregar_extensoes(profile, log, habilitado=True):
    """Instala as extensoes Chromium de data/extensions/ no profile.

    Hoje empacotamos o uBlock Origin Lite (data/extensions/uBOLite.chromium,
    Manifest V3) para bloquear anuncios/rastreadores durante a automacao. Usa a
    API nativa do QtWebEngine (profile.extensionManager().installExtension) --
    requer Qt 6.10+, que so aceita extensoes MV3 (o Chromium ja removeu o MV2).
    A instalacao e ASSINCRONA (sinal installFinished); o job ja pode comecar a
    navegar antes de terminar -- a extensao passa a valer assim que carrega.

    'habilitado=False' (flag --sem-ublock) pula tudo. Perfil em memoria (sem -d)
    pode recusar a instalacao; nesse caso so logamos a falha, sem travar.
    """
    if not habilitado:
        log("extensoes: desativadas (--sem-ublock)")
        return
    if not EXTENSOES_DIR.is_dir():
        return
    mgr = getattr(profile, "extensionManager", lambda: None)()
    if mgr is None:
        log("extensoes: API indisponivel nesta versao do Qt (precisa 6.9+)")
        return
    a_instalar = [
        d for d in sorted(EXTENSOES_DIR.iterdir())
        if d.is_dir() and (d / "manifest.json").exists()
    ]
    if not a_instalar:
        return
    # Perfil persistente REUSA a extensao instalada em execucoes anteriores:
    # nesse caso installExtension() FALHA ("Failed to create install directory",
    # o diretorio ja existe) e a extensao NAO carrega -- por isso o browser
    # "abria sem uBlock" da 2a execucao em diante.
    #
    # As alternativas da API nao servem nesta versao do QtWebEngine:
    #   - loadExtension() carrega a ja-instalada, mas vem DESABILITADA (nao
    #     bloqueia nada);
    #   - setExtensionEnabled() trava o processo (segfault).
    # Solucao: APAGAR a instalacao antiga e reinstalar LIMPO toda vez -- a
    # instalacao via installExtension() ja vem habilitada e bloqueando. No
    # __init__ o QtWebEngine ainda nao carregou as extensoes do disco, entao
    # apagar <perfil>/Extensions/<nome>_* aqui e seguro.
    base_instal = Path(mgr.installPath())

    def _limpar_instalacao_previa(ext):
        if base_instal.is_dir():
            for p in base_instal.iterdir():
                if p.name.startswith(ext.name + "_"):
                    shutil.rmtree(p, ignore_errors=True)

    # uma de cada vez: installFinished nao diz QUAL extensao terminou, entao so
    # iniciamos a proxima apos a anterior -- assim o nome logado sempre bate com
    # o resultado, qualquer que seja a ordem do Chromium.
    idx = [0]

    def _proxima():
        if idx[0] >= len(a_instalar):
            mgr.installFinished.disconnect(_ao_instalar)
            return
        ext = a_instalar[idx[0]]
        _limpar_instalacao_previa(ext)       # reinstala limpo (vem habilitada)
        mgr.installExtension(str(ext))

    def _ao_instalar(info):
        # installFinished entrega um QWebEngineExtensionInfo (NAO um bool):
        # isLoaded()=True so quando carregou de fato; error() diz o motivo da
        # falha (ex.: "Unsupported manifest version" p/ extensao MV2).
        nome = a_instalar[idx[0]].name
        if info.isLoaded():
            log(f"extensoes: carregada: {info.name()}")
        else:
            log(f"extensoes: FALHOU ({info.error() or 'desconhecido'}): {nome}")
        idx[0] += 1
        _proxima()

    mgr.installFinished.connect(_ao_instalar)
    _proxima()


def _remover_perfil_temporario(data_dir):
    """Apaga o diretorio de perfil temporario DEPOIS que este processo morrer.

    O QtWebEngine mantem subprocessos (storage/render do Chromium) vivos ate o
    fim do teardown e RECRIA o diretorio se ele for apagado cedo demais -- por
    isso apagar aqui mesmo (ou via atexit) nao basta. Um processo destacado
    espera este PID sumir (ai todos os subprocessos ja morreram, nada recria) e
    so entao remove tudo. So Linux (usa /proc); best-effort, nunca trava a saida.
    """
    espera_e_apaga = (
        "import os, sys, time, shutil\n"
        "pai, alvo = int(sys.argv[1]), sys.argv[2]\n"
        "while os.path.exists('/proc/%d' % pai): time.sleep(0.2)\n"
        "time.sleep(0.5)\n"                       # margem p/ subprocessos morrerem
        "shutil.rmtree(alvo, ignore_errors=True)\n"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", espera_e_apaga, str(os.getpid()), data_dir],
            start_new_session=True,              # destaca: sobrevive a nossa saida
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        eventos.info("=== fim | remocao do perfil temporario agendada: %s ===",
                     data_dir)
    except Exception as e:
        eventos.info("=== fim | falha ao agendar remocao de %s: %s ===",
                     data_dir, e)


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Executa um job JSON (sequencia de actions) num browser PySide6."
    )
    p.add_argument(
        "-s", "--script",
        help="job a executar: pasta jobs/<nome>, arquivo job.json ou so o nome "
             "(padrao: primeira pasta de jobs/)",
    )
    p.add_argument(
        "-d", "--data-dir",
        help="diretorio do cache/cookies/storage (persistente). Precedencia: "
             "-d  >  campo 'data_dir' no .json  >  /tmp/craudiowebot-<uuid>.",
    )
    p.add_argument(
        "--servir", nargs="?", const=8765, type=int, metavar="PORTA",
        help="abre um servidor TCP em 127.0.0.1 (porta padrao 8765) que "
             "recebe actions em JSON (uma por linha) e as executa em tempo "
             "de execucao; com --servir o job inicial (-s) e opcional",
    )
    p.add_argument(
        "-p", "--proxy", metavar="PROXY",
        help="proxy de aplicacao (vale p/ todo o QtWebEngine), ex.: "
             "'http://usuario:senha@host:porta' ou 'host:porta'. Precedencia: "
             "-p  >  campo 'proxy' no .json (string ou array; array sorteia 1).",
    )
    p.add_argument(
        "--sem-ublock", dest="sem_ublock", action="store_true",
        help="nao carrega o uBlock Origin (extensao empacotada em "
             "data/extensions/). Por padrao o uBlock vem ligado; o campo "
             "'ublock': false no .json tambem desliga.",
    )
    return p.parse_args(argv)


def main():
    args = parse_args(sys.argv[1:])
    if args.script or args.servir is None:
        job, job_dir = resolver_job(args.script or primeiro_job())
    else:
        # --servir sem -s: abre vazio e espera os comandos pelo socket
        job, job_dir = {"name": "servidor", "actions": []}, None
    # precedencia do profile: -d  >  campo no .json  >  /tmp/<uuid> efemero.
    # Quando nenhum diretorio foi pedido (nem flag nem .json), NOS criamos um
    # temporario em /tmp e o apagamos por completo ao terminar (mais abaixo).
    data_dir_pedido = args.data_dir or job.get("data_dir") or job.get("profile")
    data_dir = data_dir_pedido or f"/tmp/craudiowebot-{uuid.uuid4()}"
    data_dir_temporario = not data_dir_pedido
    # log de eventos: /tmp/browser_{PORTA}_events.log (PID quando sem --servir)
    porta_log = args.servir if args.servir is not None else os.getpid()
    caminho_eventos = configurar_log_eventos(porta_log)
    eventos.info("=== inicio | job=%s | porta=%s ===",
                 job.get("name", ""), porta_log)

    app = QApplication(sys.argv)
    # precedencia do proxy: -p  >  campo 'proxy' no .json (string ou array;
    # array sorteia 1). Definido antes do Browser (que cria o profile) p/ valer
    # ja na 1a navegacao. Sem proxy: usa o do sistema.
    proxy_desc = configurar_proxy(args.proxy or job.get("proxy"))
    # uBlock ligado por padrao; --sem-ublock ou "ublock": false no .json desliga.
    # Precedencia: --sem-ublock  >  campo 'ublock' no .json  >  default (True).
    ublock = not args.sem_ublock and job.get("ublock", True)
    janela = Browser(job, data_dir=data_dir,
                     job_dir=str(job_dir) if job_dir else None, ublock=ublock)
    janela._log(f"eventos: {caminho_eventos}")
    if data_dir_temporario:
        janela._log(f"perfil temporario (sera apagado ao sair): {data_dir}")
    if proxy_desc:
        janela._log(f"proxy: {proxy_desc}")
    if args.servir is not None:
        # parent=janela mantem o servidor vivo enquanto a janela existir
        ServidorComandos(janela.runner, janela._log, args.servir,
                         parent=janela)
        # guarda a porta e recompoe o titulo (ex.: "| Browser ... job: X :8765").
        # _compor_titulo mantem porta e spinner; definir_titulo so troca a base.
        janela.porta_servir = args.servir
        janela._compor_titulo()
    janela.show()
    codigo = app.exec()
    # Perfil temporario que NOS criamos (sem -d e sem campo no .json): apaga
    # tudo ao sair (vale para exit/finish, fechar a janela, qualquer saida limpa).
    if data_dir_temporario:
        _remover_perfil_temporario(data_dir)
    sys.exit(codigo)


if __name__ == "__main__":
    main()
