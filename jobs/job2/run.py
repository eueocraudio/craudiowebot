import os
from html.parser import HTMLParser

# xpath da resposta (usado pelo esperar_pagina / inspecao manual, se precisar)
RESPOSTA_XPATH = "//div[contains(@class,'font-claude-response')]"
# o botao Share so aparece quando a resposta TERMINA (evita parar no 'Thinking')
CONCLUIDO_XPATH = "//*[@data-testid='wiggle-controls-actions-share']"
# classe do elemento que contem o texto da resposta do Claude
CLASSE_RESPOSTA = "font-claude-response-body"


class _ExtratorResposta(HTMLParser):
    """Coleta o texto de cada <div class='...font-claude-response-body...'>.
    A ultima entrada e a resposta mais recente."""

    VOID = {"br", "img", "hr", "input", "meta", "link", "source", "area", "col", "wbr"}

    def __init__(self, classe):
        super().__init__()
        self.classe = classe
        self.respostas = []
        self._cap = False
        self._depth = 0
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if not self._cap:
            if self.classe in dict(attrs).get("class", ""):
                self._cap = True
                self._depth = 1
                self._buf = []
        elif tag not in self.VOID:
            self._depth += 1

    def handle_endtag(self, tag):
        if self._cap and tag not in self.VOID:
            self._depth -= 1
            if self._depth <= 0:
                self.respostas.append("".join(self._buf).strip())
                self._cap = False

    def handle_data(self, data):
        if self._cap:
            self._buf.append(data)


class Job():
    def __init__(self, browser, json_):
        self.json_  = json_;
        self.browser = browser;
        pass;

    def pre_action(self, action_json):
        # credenciais vem de variaveis de ambiente (nunca versionadas)
        if action_json.get("id") and action_json.get("id") == "email":
            action_json["value"] = os.environ.get("GMAIL_EMAIL", "");
        if action_json.get("id") and action_json.get("id") == "console":
            action_json["value"] = open("/tmp/texto.txt", "r").read();
        pass;

    def pos_action(self, action_json):
        # demais acoes: se tiver xpath, le o valor do input apontado por ele
        xpath = action_json.get("xpath")
        if xpath:
            self.browser.ler_valor(xpath)

    def finish(self):
        # pega o HTML inteiro numa variavel e tenta achar a resposta do Claude
        self.browser.pegar_html(self._achar_resposta)

    def _achar_resposta(self, html):
        html = html or ""
        parser = _ExtratorResposta(CLASSE_RESPOSTA)
        parser.feed(html)
        resposta = parser.respostas[-1] if parser.respostas else None
        print("\n===== RESPOSTA DO CLAUDE (extraida do HTML) =====")
        print(resposta if resposta else "(resposta nao encontrada no HTML)")
        print("=================================================\n", flush=True)
