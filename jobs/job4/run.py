import os
import re
import sqlite3
from datetime import datetime
from html.parser import HTMLParser

# ── Tela /admin/usuarios/{id}/edit ──────────────────────────────────────────
# Vinculos do professor: pares de <select> cursos[i][id] / cursos[i][papel].
# Nome do professor: <input name="name" value="...">.
RE_NOME = re.compile(r"^cursos\[(\d+)\]\[(id|papel)\]$")
PAPEL_LABEL = {
    "comum": "Comum", "coordenador": "Coordenador",
    "gestor": "Gestor de TCC", "metodologia": "Metodologia",
}

DB_PATH = os.path.join(os.path.dirname(__file__), "resultado.sqlite")


class _ExtratorEdicao(HTMLParser):
    """Extrai o nome do professor e seus vinculos de curso da tela de edicao."""

    def __init__(self):
        super().__init__()
        self.nome = None
        self.vinculos = {}        # idx -> {"curso": str, "papel": str}
        self._idx = self._campo = None
        self._em_option = False
        self._option_sel = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name") == "name" and self.nome is None:
            self.nome = (a.get("value") or "").strip() or None
        elif tag == "select":
            m = RE_NOME.match(a.get("name", ""))
            self._idx, self._campo = (int(m.group(1)), m.group(2)) if m else (None, None)
        elif tag == "option" and self._idx is not None:
            self._em_option = True
            self._option_sel = "selected" in a
            self._buf = []

    def handle_data(self, data):
        if self._em_option:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "option" and self._em_option:
            texto = "".join(self._buf).strip()
            if self._option_sel and texto and texto != "Selecione o curso...":
                reg = self.vinculos.setdefault(self._idx, {})
                if self._campo == "id":
                    reg["curso"] = texto
                else:
                    reg["papel"] = PAPEL_LABEL.get(texto, texto)
            self._em_option = False
        elif tag == "select":
            self._idx = self._campo = None


# ── Tela /professor/tccs ────────────────────────────────────────────────────
# Tres tabelas (aguardando / em andamento / encerrados). Cada <tr> do <tbody>
# tem o titulo na 1a celula; juntamos as demais celulas como "detalhes".
class _ExtratorTccs(HTMLParser):
    VOID = {"br", "img", "hr", "input", "meta", "link", "source", "area", "col", "wbr", "i"}

    def __init__(self):
        super().__init__()
        self.linhas = []          # [(titulo, detalhes)]
        self._in_tbody = False
        self._in_tr = False
        self._celulas = None
        self._in_td = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self._in_tbody = True
        elif self._in_tbody and tag == "tr":
            self._in_tr = True
            self._celulas = []
        elif self._in_tr and tag in ("td", "th"):
            self._in_td = True
            self._buf = []

    def handle_data(self, data):
        if self._in_td:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "tbody":
            self._in_tbody = False
        elif tag == "tr" and self._in_tr:
            cels = [" ".join(c.split()) for c in (self._celulas or [])]
            cels = [c for c in cels if c]
            if cels and not cels[0].lower().startswith("nenhum"):
                titulo = cels[0]
                detalhes = " | ".join(cels[1:])
                self.linhas.append((titulo, detalhes))
            self._in_tr = False
            self._celulas = None
        elif tag in ("td", "th") and self._in_td:
            self._celulas.append("".join(self._buf).strip())
            self._in_td = False


class Job():
    def __init__(self, browser, json_):
        self.json_ = json_
        self.browser = browser
        self._html_cursos = None

    def pre_action(self, action_json):
        # credenciais vem de variaveis de ambiente (nunca versionadas)
        if action_json.get("id") == "email":
            action_json["value"] = os.environ.get("PAINEL_EMAIL", "")
        if action_json.get("id") == "password":
            action_json["value"] = os.environ.get("PAINEL_PASSWORD", "")

    def pos_action(self, action_json):
        # captura o HTML da tela de edicao (cursos) enquanto ela esta aberta,
        # antes de navegar para fora dela
        if action_json.get("id") == "ler_cursos":
            self.browser.pegar_html(self._guardar_cursos)

    def _guardar_cursos(self, html):
        self._html_cursos = html or ""

    def finish(self):
        # ao final estamos em /professor/tccs (impersonando Fulano)
        self.browser.pegar_html(self._analisar_final)

    def _analisar_final(self, html_tccs):
        ed = _ExtratorEdicao()
        ed.feed(self._html_cursos or "")
        nome = ed.nome or "Fulano"
        cursos = [v for v in ed.vinculos.values() if v.get("curso")]

        tx = _ExtratorTccs()
        tx.feed(html_tccs or "")
        tccs = tx.linhas

        self._reportar(nome, cursos, tccs)
        self._salvar_sqlite(nome, cursos, tccs)

    def _reportar(self, nome, cursos, tccs):
        print("\n====== PLANO DE TESTE: professor (cursos + TCCs) ======")
        print(f"Professor: {nome}")
        print(f"\nCursos vinculados ({len(cursos)}):")
        for c in cursos:
            print(f"  - {c['curso']}  [papel: {c.get('papel', '?')}]")
        if not cursos:
            print("  (nenhum)")
        print(f"\nTCCs orientados ({len(tccs)}):")
        for titulo, det in tccs:
            print(f"  - {titulo}" + (f"  [{det}]" if det else ""))
        if not tccs:
            print("  (nenhum TCC orientado)")
        print("=======================================================\n", flush=True)

    def _salvar_sqlite(self, nome, cursos, tccs):
        # IMPORTANTE: ao mudar este schema, atualizar tambem DICIONARIO_DADOS.md
        # (mesmo diretorio deste run.py).
        con = sqlite3.connect(DB_PATH)
        try:
            cur = con.cursor()
            cur.executescript("""
                DROP TABLE IF EXISTS cursos;
                DROP TABLE IF EXISTS tccs_orientados;
                CREATE TABLE cursos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    professor TEXT NOT NULL,
                    curso     TEXT NOT NULL,
                    papel     TEXT,
                    coletado_em TEXT NOT NULL
                );
                CREATE TABLE tccs_orientados (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    professor TEXT NOT NULL,
                    titulo    TEXT NOT NULL,
                    detalhes  TEXT,
                    coletado_em TEXT NOT NULL
                );
            """)
            agora = datetime.now().isoformat(timespec="seconds")
            cur.executemany(
                "INSERT INTO cursos (professor, curso, papel, coletado_em) VALUES (?,?,?,?)",
                [(nome, c["curso"], c.get("papel"), agora) for c in cursos],
            )
            cur.executemany(
                "INSERT INTO tccs_orientados (professor, titulo, detalhes, coletado_em) VALUES (?,?,?,?)",
                [(nome, t, d, agora) for (t, d) in tccs],
            )
            con.commit()
            print(f"[sqlite] gravado em {DB_PATH} "
                  f"({len(cursos)} curso(s), {len(tccs)} TCC(s))", flush=True)
        finally:
            con.close()
