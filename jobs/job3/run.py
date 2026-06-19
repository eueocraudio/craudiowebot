import os
from html.parser import HTMLParser

# Procuramos o nome dentro das linhas da tabela de usuarios (/admin/usuarios).
ALVO = "fulano"
# Estado vazio renderizado pelo @empty da view (vide index.blade.php).
MARCA_VAZIO = "nenhum usuario encontrado"


class _ExtratorTbody(HTMLParser):
    """Captura o texto de dentro do <tbody> da pagina. Assim evitamos o
    falso-positivo do termo buscado ecoado no <input name='q'>."""

    VOID = {"br", "img", "hr", "input", "meta", "link", "source", "area", "col", "wbr"}

    def __init__(self):
        super().__init__()
        self.textos = []
        self._cap = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "tbody" and not self._cap:
            self._cap = True
            self._depth = 1
        elif self._cap and tag not in self.VOID:
            self._depth += 1

    def handle_endtag(self, tag):
        if self._cap and tag not in self.VOID:
            self._depth -= 1
            if self._depth <= 0:
                self._cap = False

    def handle_data(self, data):
        if self._cap:
            self.textos.append(data)


class Job():
    def __init__(self, browser, json_):
        self.json_ = json_
        self.browser = browser

    def pre_action(self, action_json):
        # credenciais vem de variaveis de ambiente (nunca versionadas)
        if action_json.get("id") == "email":
            action_json["value"] = os.environ.get("PAINEL_EMAIL", "")
        if action_json.get("id") == "password":
            action_json["value"] = os.environ.get("PAINEL_PASSWORD", "")

    def pos_action(self, action_json):
        # so loga o valor de inputs (email/password) preenchidos, para conferencia
        if action_json.get("id") in ("email",):
            xpath = action_json.get("xpath")
            if xpath:
                self.browser.ler_valor(xpath)

    def finish(self):
        self.browser.pegar_html(self._analisar)

    def _analisar(self, html):
        html = html or ""
        parser = _ExtratorTbody()
        parser.feed(html)
        corpo = " ".join(parser.textos)
        corpo_norm = self._sem_acento(corpo.lower())

        encontrado = ALVO in corpo_norm and MARCA_VAZIO not in corpo_norm

        print("\n========= RESULTADO DO PLANO DE TESTE =========")
        print("Projeto : painel.exemplo.com")
        print("Acao    : login como admin -> busca por 'Fulano' em /admin/usuarios")
        if encontrado:
            print("VEREDITO: Fulano E usuario do sistema. (ENCONTRADO)")
            # mostra as linhas da tabela como evidencia
            linhas = [t.strip() for t in parser.textos if t.strip()]
            print("Linhas da tabela:", " | ".join(linhas))
        elif MARCA_VAZIO in corpo_norm:
            print("VEREDITO: Fulano NAO foi encontrado. (tabela vazia)")
        else:
            print("VEREDITO: indeterminado (tabela nao localizada no HTML;")
            print("          verifique se o login foi concluido).")
        print("===============================================\n", flush=True)

    @staticmethod
    def _sem_acento(s):
        for a, b in (("á", "a"), ("ã", "a"), ("â", "a"), ("é", "e"),
                     ("ê", "e"), ("í", "i"), ("ó", "o"), ("ô", "o"),
                     ("õ", "o"), ("ú", "u"), ("ç", "c")):
            s = s.replace(a, b)
        return s
