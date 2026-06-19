# SPEC — craudiowebot

Especificação completa e normativa do **craudiowebot**: um navegador PySide6
(QtWebEngine / Chromium embutido) que automatiza páginas reais executando
*jobs* descritos em JSON, com perfil persistente (cookies/storage) para manter
login entre execuções, e um modo servidor que aceita comandos em tempo real.

Implementação de referência: **`browser.py`** (peça única, ~1100 linhas).
Este documento é o **contrato** para quem consome o projeto a partir de outro
projeto (ver §15). Ao mudar qualquer comportamento descrito aqui, atualize este
arquivo na **mesma** mudança.

**Convenção:** tudo em **português** — chaves de action, nomes de campo, nomes
de método, comentários. Strings de log em `browser.py` são ASCII, sem acento.

**Índice**

1. [Visão geral e componentes](#1-visão-geral-e-componentes)
2. [Requisitos e execução (CLI)](#2-requisitos-e-execução-cli)
3. [Job (`job.json`)](#3-job-jobjson)
4. [Modelo de execução (`JobRunner`)](#4-modelo-de-execução-jobrunner)
5. [Tipos de action](#5-tipos-de-action)
6. [Actions que "furam a fila"](#6-actions-que-furam-a-fila)
7. [Perfil persistente](#7-perfil-persistente)
8. [Proxy](#8-proxy)
9. [User-Agent](#9-user-agent)
10. [Hooks de job (`run.py`) e API do `browser`](#10-hooks-de-job-runpy-e-api-do-browser)
11. [Credenciais](#11-credenciais)
12. [Protocolo do modo servidor (`--servir`)](#12-protocolo-do-modo-servidor---servir)
13. [Diálogos JS](#13-diálogos-js)
14. [Log](#14-log)
15. [Ponte JS interna](#15-ponte-js-interna)
16. [Integração a partir de outros projetos](#16-integração-a-partir-de-outros-projetos)
17. [Limitações e gotchas](#17-limitações-e-gotchas)
18. [Referência rápida](#18-referência-rápida)
19. [Extensões (uBlock Origin Lite)](#19-extensões-ublock-origin-lite)

---

## 1. Visão geral e componentes

Tudo vive em `browser.py`, em quatro peças:

| Componente          | Tipo                | Papel |
|---------------------|---------------------|-------|
| `JobRunner`         | `QObject`           | Máquina de estados assíncrona que executa as actions **em sequência**. Fila de lotes + pilha de frames. Ver §4. |
| `ServidorComandos`  | `QObject`           | Servidor TCP (`--servir`) que recebe actions em JSON e as enfileira no `JobRunner`. Ver §12. |
| `Browser`           | `QMainWindow`       | A janela (barra de navegação + `QWebEngineView` + caixa de log). Configura o perfil e expõe os *facilitadores* para o `run.py`. Ver §10. |
| `AutoDialogPage`    | `QWebEnginePage`    | Auto-aceita `alert`/`confirm`/`prompt` para não travar o event loop. Ver §13. |

Funções de módulo: `carregar_run_py`, `resolver_job`, `primeiro_job`,
`configurar_proxy`, `_parse_proxy`, `carregar_extensoes`, `parse_args`, `main`.

Fluxo de uma execução típica:

```
main() → parse_args → resolver_job → configurar_proxy
       → QApplication → Browser(job, data_dir, job_dir)
         ├─ cria QWebEngineProfile (persistente ou em memória)
         ├─ AutoDialogPage + QWebEngineView
         ├─ carregar_run_py(job_dir) → Job(browser, json)   [se houver run.py]
         └─ JobRunner(view, log, job_obj)
       → [se --servir] ServidorComandos(runner, log, porta)
       → janela.show() → QTimer.singleShot(0, iniciar) → runner.run(job)
       → app.exec()  (event loop)
```

---

## 2. Requisitos e execução (CLI)

### Requisitos

- **Python 3.10+** (usa `str | None`; `tarfile.extractall(filter="data")` em 3.12+).
- **PySide6 ≥ 6.6** (`requirements.txt`); algumas APIs (`page.permissionRequested`)
  exigem **Qt 6.8+**. As **extensões** (uBlock, §19) exigem **Qt 6.10+**
  (`QWebEngineProfile.extensionManager`, só existe a partir do 6.10) — em versões
  anteriores o uBlock simplesmente não carrega (sem erro fatal).
- Bibliotecas de sistema do QtWebEngine (Chromium). `install.sh` cria a `.venv`,
  instala PySide6 e checa essas libs.

```bash
./install.sh
source .venv/bin/activate
```

### Sintaxe

```
python3 browser.py [-s <job>] [-d <dir>] [-p <proxy>] [--sem-ublock] [--servir [PORTA]]
```

| Flag                | Argumento | Descrição |
|---------------------|-----------|-----------|
| `-s`, `--script`    | job       | Job a executar: pasta `jobs/<nome>`, arquivo `job.json`, ou só o nome `<nome>`. Sem `-s`, roda a **primeira** pasta de `jobs/*/job.json` (ordem alfabética). Ver resolução em §3. |
| `-d`, `--data-dir`  | dir       | Diretório do perfil persistente (cache/cookies/storage). Maior precedência. Ver §7. |
| `-p`, `--proxy`     | proxy     | Proxy de aplicação. Maior precedência. Ver §8. |
| `--sem-ublock`      | —         | Não carrega o uBlock Origin Lite (extensão empacotada). Por padrão vem ligado. Maior precedência sobre o campo `ublock` do JSON. Ver §19. |
| `--servir`          | `[PORTA]` | Abre o servidor TCP em `127.0.0.1` (porta opcional, padrão **8765**). Com `--servir` o `-s` é **opcional**. Ver §12. |

### Modos

```bash
python3 browser.py -s job0                  # roda um job e segue aberto
python3 browser.py                           # roda a 1a pasta de jobs/
python3 browser.py -s job0 -d /tmp/perfil    # -d sobrescreve o perfil
python3 browser.py --servir                  # só servidor (sem job inicial)
python3 browser.py -s job0 --servir          # job inicial (ex.: login) e segue servindo
python3 browser.py -s job0 -p host:3128 --servir
```

- **Job único:** roda o job; a janela **continua aberta** ao terminar (a menos
  que o job use `exit`/`finish`).
- **Servidor:** `-s` opcional. Combinando os dois, o job inicial roda primeiro
  e o browser segue aceitando comandos pelo socket.
- **`exit`/`finish` encerram o app inteiro**, inclusive em `--servir`. Um job
  que vá servir depois **não** deve terminar com elas.

### Headless

```bash
QT_QPA_PLATFORM=offscreen python3 browser.py -s job0
```

> O log do browser vai para a **caixa de texto da janela** (`QPlainTextEdit`),
> **não** para o stdout — rodar headless não imprime o log no terminal. Apenas
> `print()` de dentro de `run.py` (ex.: `job4`) aparece no stdout.

### Código de saída

`main` termina com `sys.exit(app.exec())` — o código é o do event loop do Qt
(0 em saída normal). `resolver_job` levanta `SystemExit` (mensagem em stderr) se
o job não for encontrado.

---

## 3. Job (`job.json`)

Um job é uma **pasta** `jobs/<nome>/` com um `job.json` (obrigatório) e,
opcionalmente, um `run.py` (§10). O `job.json` é um objeto JSON:

```json
{
  "name": "rótulo do job",
  "data_dir": "~/.local/data/craudiowebot/<nome>",
  "user_agent": "Mozilla/5.0 ...",
  "proxy": "http://host:porta",
  "ublock": true,
  "actions": [ { "type": "...", ... }, ... ]
}
```

| Campo        | Tipo            | Obrigatório | Descrição |
|--------------|-----------------|-------------|-----------|
| `name`       | string          | não         | Rótulo exibido no log e no título da janela. Default: `""`. |
| `data_dir`   | string          | não         | Diretório do perfil persistente (aceita `~`). Também aceito como **`profile`** (alias). Ver §7. |
| `user_agent` | string          | não         | User-Agent anunciado pelo perfil, **na partida**. Sobrescreve o default `USER_AGENT`. Ver §9. |
| `proxy`      | string \| array | não         | Proxy de aplicação. String, ou **array** (sorteia um na partida). Ver §8. |
| `ublock`     | bool            | não         | Carrega o uBlock Origin Lite na partida. Default **`true`**. `false` desliga (a flag `--sem-ublock` também). Ver §19. |
| `actions`    | array           | sim         | Lista de actions executadas **em sequência** (cada uma só começa quando a anterior termina). |

As actions rodam na ordem. Erro numa action é logado mas **não** trava o job — a
execução segue para a próxima (§4.4).

### Resolução do `-s <arg>`

`resolver_job` tenta, nesta ordem:

1. **pasta** — `arg` é diretório → lê `arg/job.json`;
2. **arquivo** — `arg` é arquivo → usa `arg` diretamente;
3. **nome** — senão, tenta `jobs/<arg>/job.json`.

Não encontrando, levanta `SystemExit("Job nao encontrado: ...")`. A pasta
resolvida (`job_dir`) é onde se procura o `run.py`.

### Exemplo completo (extraído do `job0`)

```json
{
  "name": "Teste (job0)",
  "data_dir": "~/.local/data/craudiowebot/job0",
  "actions": [
    {"type": "navigate", "value": "", "id": "principal"},
    {"type": "sleep", "value": 5},
    {"type": "exists", "wait": 5, "xpath": "//*[@id='nv_linkentrar']",
      "yes": [
        {"type": "navigate", "value": "https://exemplo.com/painel/index.php"},
        {"type": "key", "xpath": "//*[@id='txt_email']",    "value": "", "id": "email"},
        {"type": "key", "xpath": "//*[@id='txt_password']", "value": "", "id": "password"},
        {"type": "press", "xpath": "//*[@type='password']"},
        {"type": "sleep", "value": 5}
      ],
      "not": [
        {"type": "navigate", "value": "https://exemplo.com/painel/cursos.php"}
      ]},
    {"type": "exit", "wait": 5}
  ]
}
```

> O `value` de `navigate`/`key` está vazio no JSON versionado; o `run.py` o
> preenche em runtime via `pre_action`, casando pelo `id` (§10, §11).

---

## 4. Modelo de execução (`JobRunner`)

### 4.1 Fila de lotes

Cada chamada a `executar(actions, ao_terminar=None)` enfileira um **lote** em
`self._fila`. O job inicial (`run`) e cada pedido do servidor (§12) são lotes.

- Lotes rodam **um por vez**, na ordem de chegada — **nunca** dois lotes mexem
  na página ao mesmo tempo. `_rodando` indica se há lote em andamento.
- Ao terminar o lote, `ao_terminar(resultados)` recebe a lista de saídas das
  actions de leitura (`html`/`url`/`eval`); depois `_proximo_lote` puxa o
  próximo da fila.

```python
def run(self, job):           # job inicial: 1 lote
    self.executar(job["actions"], fim)

def executar(self, actions, ao_terminar=None):
    self._fila.append((list(actions or []), ao_terminar))
    if not self._rodando:
        self._proximo_lote()
```

### 4.2 Pilha de frames

Dentro de um lote, a execução usa uma **pilha** `self.stack` de
`[lista_de_actions, índice]`. Actions com filhos (`exists` → `yes`/`not`)
empilham um novo frame via `_entrar_ramo`; ao esgotá-lo, `_next` desempilha e o
controle volta ao frame pai. A indentação no log reflete a profundidade.

### 4.3 Ponte assíncrona — sem laço bloqueante

QtWebEngine é assíncrono. O avanço entre actions é orquestrado por:

- `QTimer.singleShot(ms, cb)` — agendamento (sleeps, pausas, timeouts);
- callbacks de `view.loadFinished` — navegação;
- callbacks de `view.page().runJavaScript(js, cb)` — leitura/escrita no DOM.

**Não há `while` bloqueante.** Cada handler de action faz seu trabalho e, ao
concluir, chama:

- `self._continuar()` — agenda a próxima após `PAUSA_ENTRE_ACOES` (300 ms);
- `self._next()` — avança imediatamente (usado por `sleep`, que já esperou).

### 4.4 Tratamento de erros

- Exceção dentro de um handler de action é capturada em `_next`, logada
  (`ERRO na acao: ...`) e a execução **continua** na próxima action.
- Exceção num hook do `run.py` é capturada em `_hook`, logada
  (`[run.py <hook>] ERRO: ...`) e ignorada.
- Exceção no `ao_terminar` de um lote é logada (`[ao_terminar] ERRO: ...`).
- Tipo de action **desconhecido** é logado (`AVISO: tipo desconhecido`) e pulado.
- `key`/`click`/`press`/`html`(com xpath) cujo elemento **não exista** logam
  `AVISO: elemento (xpath) nao encontrado` (status `NAO_ENCONTRADO`) e seguem.

**Garantia:** uma action que falha nunca trava o lote nem a fila.

### 4.5 Constantes de tempo

| Constante            | Valor   | Significado |
|----------------------|---------|-------------|
| `PAUSA_ENTRE_ACOES`  | 300 ms  | Pausa entre actions (deixa a página reagir). |
| `EXISTS_POLL_MS`     | 300 ms  | Intervalo de polling do `exists`. |

---

## 5. Tipos de action

Cada action é um objeto com `"type"` e campos conforme o tipo. O dispatch é por
`getattr(self, f"_do_{tipo}")`.

### Campos comuns

- **`type`** *(string, obrigatório)* — o tipo da action.
- **`xpath`** *(string)* — XPath do elemento alvo, resolvido na página. Pega o
  **primeiro** nó (`FIRST_ORDERED_NODE_TYPE`).
- **`value`** — carga da action; o tipo (string/número) e o significado dependem
  do `type`.
- **`id`** *(string)* — rótulo opcional. **Não afeta a execução.** Serve para:
  (a) os hooks do `run.py` reconhecerem a action (`pre_action`/`pos_action`);
  (b) marcar itens nos `resultados` das actions de leitura (devolvido ao cliente
  do `--servir`).

### Tabela-resumo

| Tipo            | Campos                            | Toca a página? | Devolve resultado? |
|-----------------|-----------------------------------|:--------------:|:------------------:|
| `navigate`      | `value`, `timeout?`               | sim            | não |
| `key`           | `xpath`, `value`                  | sim            | não |
| `click`         | `xpath`                           | sim            | não |
| `press`         | `value?`, `xpath?`                | sim            | não |
| `sleep`         | `value`                           | não            | não |
| `exists`        | `xpath`, `wait?`, `yes?`, `not?`  | sim (lê)       | não |
| `exit`          | `wait?`                           | não            | não (encerra o app) |
| `finish`        | `wait?`                           | não            | não (encerra o app) |
| `html`          | `xpath?`, `id?`                   | sim (lê)       | **sim** (`html`) |
| `url`           | `id?`                             | sim (lê)       | **sim** (`url`) |
| `eval`          | `value`, `id?`                    | sim            | **sim** (`result`) |
| `screenshot`    | `value?`, `largura?`, `id?`       | sim (lê)       | **sim** (`ok`,`path`) |
| `windows`       | `id?`                             | não            | **sim** (`janelas`,`ativa`) |
| `window`        | `value`, `id?`                    | troca a janela ativa | **sim** (`ok`,`ativa`) |
| `window_close`  | `value?`, `id?`                   | fecha janela   | **sim** (`ok`,`ativa`) |
| `downloads`     | `id?`                             | não            | **sim** (`downloads`) |
| `title`         | `value`                           | não            | não (fura a fila avulso) |
| `comment`       | `value`                           | não            | não (fura a fila avulso) |
| `user_agent`    | `value`                           | não            | não |
| `zoom`          | `value?`                          | não            | não |
| `save_profile`  | `value`                           | não            | não |
| `load_profile`  | `value`                           | não            | não |
| `clear_profile` | `value?`                          | não            | não |

### Detalhamento

#### `navigate`
Carrega a URL `value` e espera `view.loadFinished`. Campo `timeout` (s, padrão
**30**): fallback para navegações que **não** emitem `loadFinished` (mudança só
de fragmento `#`, ou mesma URL) — depois do timeout, continua a fila assim
mesmo. Loga `carregada (ok|falha)` ou `navegacao sem loadFinished (timeout)`.
Garante **uma** continuação só (flag interna), nunca duas.
```json
{"type": "navigate", "value": "https://example.com", "timeout": 45}
```

#### `key`
Escreve `value` no elemento do `xpath`. Trata input/textarea (setter nativo +
eventos `input`/`change`) e `contenteditable` (`execCommand('insertText')`) —
ver §15. **Substitui** o conteúdo atual.
```json
{"type": "key", "xpath": "//input[@name='q']", "value": "texto a digitar"}
```

#### `click`
Clica no elemento do `xpath` (`el.click()`).
```json
{"type": "click", "xpath": "//button[@type='submit']"}
```

#### `press`
Dispara `keydown`/`keypress`/`keyup` da tecla `value` (padrão `"Enter"`) no
elemento do `xpath`, ou no `document.activeElement` se não houver `xpath`.
Mapeia keyCodes de `Enter`(13)/`Tab`(9)/`Escape`(27)/`Space`(32). Se a tecla for
`Enter` e o elemento tiver `form`, chama `form.requestSubmit()`.
```json
{"type": "press", "xpath": "//input[@name='q']", "value": "Enter"}
```

#### `sleep`
Espera `value` segundos (via `QTimer.singleShot`; não bloqueia o event loop).
Avança com `_next` (sem a pausa extra).
```json
{"type": "sleep", "value": 3}
```

#### `exists`
Faz **polling** por até `wait` segundos (padrão 0), a cada `EXISTS_POLL_MS`,
pelo elemento do `xpath`. Se aparecer, empilha e executa os filhos de `yes`;
esgotado o tempo, os de `not`. `yes`/`not` são listas de actions (podem aninhar
outros `exists`). Ramos ausentes = lista vazia.
```json
{"type": "exists", "xpath": "//*[@id='logado']", "wait": 5,
  "yes": [ {"type": "comment", "value": "já logado"} ],
  "not": [ {"type": "navigate", "value": "https://site/login"} ]}
```

#### `exit`
Espera `wait` s (padrão 0; também aceita `value` como alias) e **encerra o app
inteiro**: chama o hook `finish()` do `run.py` e `QApplication.quit()` (teardown
libera page/profile na ordem correta).
```json
{"type": "exit", "wait": 5}
```

#### `finish`
Chama `finish()` do `run.py` e agenda `QApplication.quit()` após `wait` s
(padrão **3**), dando tempo para trabalho assíncrono do `finish()` concluir
(ex.: `pegar_html` por callback que grava SQLite — ver `job4`).
```json
{"type": "finish", "wait": 3}
```

#### `html` — leitura
Lê o HTML e adiciona aos `resultados` do lote. Sem `xpath`:
`document.documentElement.outerHTML` (página inteira). Com `xpath`: `outerHTML`
do elemento (ou `null` se não existir). Item de resultado:
`{"type": "html", "html": "...", "id": "<id se houver>"}`.
```json
{"type": "html", "xpath": "//main", "id": "conteudo"}
```

#### `url` — leitura
Adiciona a URL atual (`window.location.href`) aos `resultados`. Útil para o
cliente checar "cheguei na página X?". Item:
`{"type": "url", "url": "...", "id": "<id se houver>"}`.
```json
{"type": "url", "id": "onde_estou"}
```

#### `eval` — leitura
Roda o JS de `value` no **contexto da página** (mesma origem/cookies) e adiciona
o retorno aos `resultados`. Serve para chamar APIs internas do site (ex.: XHR
**síncrono**). Item: `{"type": "eval", "result": <retorno>, "id": "<id>"}`.
> **Promises NÃO são aguardadas** — `runJavaScript` pega o valor de retorno
> imediato. O retorno precisa ser serializável (o Qt converte para o tipo
> Python correspondente: número, string, bool, lista, dict, `None`).
```json
{"type": "eval", "value": "document.title", "id": "titulo"}
```

#### `title`
Troca o **rótulo** do título da janela para `value`. **Não toca a página.** O
título completo é `<spinner> <rótulo> [:{PORTA}]`: `value` muda só o rótulo; o
**spinner** (1º caractere, §6) e o `:{PORTA}` no modo `--servir` (de
`janela.porta_servir`) são preservados. Avulso pelo servidor, **fura a fila**
(§6).
```json
{"type": "title", "value": "etapa 3 de 5"}
```

#### `comment`
Escreve `[COMENTARIO] value` no log. **Não toca a página.** Avulso pelo servidor,
**fura a fila** (§6).
```json
{"type": "comment", "value": "iniciando raspagem"}
```

#### `user_agent`
Troca o UA do perfil (`profile.setHttpUserAgent`). Vale para as **próximas**
requisições — navegue/recarregue para a página atual usá-lo. `value` vazio é
ignorado (logado). Ver §9.
```json
{"type": "user_agent", "value": "Mozilla/5.0 ... Chrome/120"}
```

#### `zoom`
Ajusta o nível de zoom da view (`QWebEngineView.setZoomFactor`). `value` é a
**porcentagem** (ex.: `150` = 150%); **ausente/vazio = 100%**. Aceita número ou
string (`"150"` ou `"150%"`); valor não numérico é ignorado (logado). O fator do
Qt vale de `0.25` a `5.0`, então a porcentagem é **limitada a 25%..500%**. Vale
para a página já carregada e persiste nas próximas navegações do mesmo perfil.
```json
{"type": "zoom", "value": 150}
{"type": "zoom"}
```

#### `screenshot`
Captura a página atual num PNG. `value` = caminho do arquivo (padrão
`/tmp/craudiowebot_shot.png`); `largura` (opcional) redimensiona. Tenta a `view`
(conteúdo web) e, se sair vazio, cai na janela de topo. Devolve
`{"type":"screenshot","ok":<bool>,"path":"<arquivo>"}` nos resultados.
```json
{"type": "screenshot", "value": "/tmp/plano.png", "largura": 1000}
```

#### `windows` / `window` / `window_close` (janelas)
O browser abre uma **janela nova** quando a página pede uma janela/aba
(`window.open`, `target=_blank`, redirect de relatório) — ver §13.1. Cada janela
é uma `JanelaWeb` (`QMainWindow`) com **view própria** no **mesmo perfil**
(login/cookies compartilhados); a janela nova aparece, mas **não** vira a ativa
sozinha. O servidor (e o `run.py`) dirigem sempre a **janela ativa**; estas
actions a inspecionam e trocam:

- **`windows`** — lista as janelas. Devolve `{"type":"windows","ativa":<i>,"janelas":[{"index","url","title","ativa"}]}`.
- **`window`** — torna `value` (índice) a janela **ativa** (passa a ser dirigida; é trazida para frente). Devolve `{"type":"window","ok":<bool>,"ativa":<i>}`.
- **`window_close`** — fecha a janela `value` (índice) ou, sem `value`, a **ativa**. **Não** fecha a janela principal (`#0`) nem a única. Se fechar a ativa, cai para a `#0`. Devolve `{"type":"window_close","ok":<bool>,"ativa":<i>}`.

```json
{"type": "windows"}
{"type": "window", "value": 1}
{"type": "window_close"}
```

#### `downloads`
Lista os downloads desta sessão (auto-salvos em `~/Downloads/` — ver §13.2).
Devolve `{"type":"downloads","downloads":[{"path","recebido","total","estado"}]}`,
com `estado` em `solicitado`|`baixando`|`concluido`|`cancelado`|`interrompido`.
Como o download é **assíncrono**, consulte esta action (com `sleep`/polling) para
saber quando o arquivo terminou antes de seguir.
```json
{"type": "downloads"}
```

#### `save_profile`
Empacota os arquivos do perfil atual num `.tar.gz` em `value` (aceita `~`). Cria
o diretório de destino. **Best-effort:** itens que o Chromium mexer durante a
leitura (`FileNotFoundError`/`OSError`) são pulados, sem travar. Perfil em
memória → nada a salvar (logado). Ver §7.
```json
{"type": "save_profile", "value": "~/backups/perfil.tar.gz"}
```

#### `load_profile`
Restaura o perfil de um `.tar.gz` em `value` (aceita `~`) para o diretório do
perfil. Arquivo inexistente → só avisa (sem erro). Usa `extractall(filter="data")`
(Python 3.12+) com fallback. Navegue/recarregue para ter efeito. Ver §7.
```json
{"type": "load_profile", "value": "~/backups/perfil.tar.gz"}
```

#### `clear_profile`
Exclui dados do perfil. Campo `value` (opcional, padrão `"sessao"`):
- `"sessao"` — limpa cookies + cache HTTP + links visitados via APIs do
  QtWebEngine (`cookieStore().deleteAllCookies()`, `clearHttpCache()`,
  `clearAllVisitedLinks()`). **Efeito imediato**, equivale a um logout; a pasta
  do perfil continua.
- `"disco"` — além da limpeza de sessão, **apaga a pasta** do perfil em disco
  (`shutil.rmtree`, `ignore_errors`). **Best-effort:** o Chromium ainda tem
  arquivos abertos, então alguns são recriados na hora e o wipe só tem efeito
  pleno ao **reiniciar**. Perfil em memória → nada em disco (logado).

Nunca eleva erro nem trava o lote. Ver §7.
```json
{"type": "clear_profile"}                      // limpa a sessao (logout)
{"type": "clear_profile", "value": "disco"}    // + apaga a pasta do perfil
```

### Adicionar um tipo de action

Crie um método `_do_<tipo>(self, action)` em `JobRunner`. Ao concluir o trabalho
assíncrono, chame `self._continuar()` (próxima após a pausa) ou `self._next()`
(já). Para devolver algo ao cliente do `--servir`, acrescente um item a
`self._resultados` (inclua o `action["id"]` por convenção). **Documente o novo
tipo aqui (§5).**

---

## 6. Actions que "furam a fila"

`title` e `comment` **não tocam a página** (só mexem na janela e no log). Por
isso, quando enviados **avulsos** (um pedido com **uma só** action `title`/
`comment`) pelo servidor, são aplicados **na hora** (`definir_titulo` /
`comentar`), mesmo com um job rodando — **não** entram na fila nem esperam o
lote em andamento terminar. Isso deixa o cliente rotular a janela ou anotar o
log em tempo real durante um job longo.

Dentro de um **lote com várias actions** ou de um `job.json`, `title`/`comment`
rodam **em sequência** como as demais. Só furam a fila no caminho avulso do
servidor (`len(actions) == 1`).

---

## 7. Perfil persistente

Diretório do perfil (cache, cookies, storage), em precedência:

```
flag -d  >  campo "data_dir"/"profile" no JSON  >  /tmp/craudiowebot-<uuid> (efêmero)
```

- **Com diretório:** `QWebEngineProfile("craudiowebot")` persistente em disco
  (`setPersistentStoragePath` + `setCachePath` + `ForcePersistentCookies`),
  reaproveitado entre execuções → **mantém o login**. `~` é expandido e o
  caminho é resolvido para absoluto; o diretório é criado se faltar.
- **Sem diretório (`/tmp/craudiowebot-<uuid>`):** quando **nenhum** diretório é
  pedido (nem `-d` nem campo no JSON), o browser cria um perfil persistente
  temporário em `/tmp` e o **apaga por completo ao sair** — vale para `exit`/
  `finish`, fechar a janela, qualquer saída limpa. A remoção é feita por um
  processo destacado que espera o processo do browser morrer antes de apagar
  (o QtWebEngine mantém subprocessos vivos no teardown e **recria** o diretório
  se ele for apagado cedo demais). Só Linux (usa `/proc`); best-effort, nunca
  trava a saída. Perfil informado por `-d`/JSON **nunca** é apagado.

Jobs que **compartilham o mesmo `data_dir` compartilham o login** (ex.:
`job1`/`job2` usam `google`; `job3`/`job4` usam `painel-admin`). O diretório em
runtime vem de `self.view.page().profile().persistentStoragePath()`.

`save_profile`/`load_profile` (§5) movem esse diretório como `.tar.gz` entre
máquinas/execuções; `clear_profile` (§5) limpa a sessão (logout) ou apaga a
pasta do perfil.

---

## 8. Proxy

Proxy **de aplicação** — vale para **todo** o QtWebEngine via
`QNetworkProxy.setApplicationProxy`, **não** por janela. Resolvido no `main`, na
partida, **antes** de criar o `Browser`/profile (para valer já na 1ª
navegação), em precedência:

```
flag -p/--proxy  >  campo "proxy" no JSON  >  proxy do sistema
```

**Formato** (`_parse_proxy`, via `urllib.parse.urlparse`):

| Spec                                   | Tipo |
|----------------------------------------|------|
| `host:porta`                           | HTTP (sem esquema = HTTP) |
| `http://host:porta`                    | HTTP |
| `http://usuário:senha@host:porta`      | HTTP com autenticação |
| `socks5://host:porta` (ou `socks://`)  | SOCKS5 |

No campo `proxy` do **JSON**, um **array** de specs faz com que um seja
**sorteado** (`random.choice`) na partida — útil para distribuir execuções por
uma lista de proxies. Itens vazios no array são descartados; array vazio = sem
proxy. A flag `-p` aceita **só uma string**.

`configurar_proxy(spec)` devolve uma descrição `host:porta` (sem usuário/senha)
do proxy escolhido, logada como `proxy: host:porta`. Sem spec → não mexe (usa o
proxy do sistema). **Não há action de runtime** para trocar o proxy.

```json
{ "proxy": "http://user:senha@host:8080", "actions": [ ... ] }
{ "proxy": ["host1:3128", "host2:3128", "socks5://host3:1080"], "actions": [ ... ] }
```

> **Autenticação:** as credenciais ficam na `QNetworkProxy` (`setUser`/
> `setPassword`). Proxies que exijam handshake adicional podem precisar de um
> handler de `proxyAuthenticationRequired` (não implementado).

---

## 9. User-Agent

Default (constante `USER_AGENT`): Firefox 140 no Linux
(`Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0`).

| Forma                         | Quando vale | Como |
|-------------------------------|-------------|------|
| Campo `user_agent` do `job.json` | na **partida** (antes da 1ª navegação) | `Browser.__init__` lê `job.get("user_agent")`; aplicado em `_configurar_permissivo`. |
| Action `user_agent` (§5)      | em **runtime**, próximas requisições | `profile.setHttpUserAgent`; em `job.json` ou via `--servir`. |

O UA efetivo é logado na partida (`user-agent: ...`). Em runtime, a página já
carregada só passa a anunciar o novo UA após navegar/recarregar.

---

## 10. Hooks de job (`run.py`) e API do `browser`

`jobs/<nome>/run.py` é **opcional**. Se existir e tiver uma `class Job`,
`carregar_run_py` a instancia como `Job(browser, json)` — `browser` é a janela
`Browser`, `json` é o dict do `job.json`. Hooks (todos opcionais; erro num hook
é logado e não trava o job):

| Hook                 | Quando | Uso típico |
|----------------------|--------|------------|
| `pre_action(action)` | **antes** de cada action | **Mutar o dict da action** — injeta segredos/URLs em runtime sem versioná-los, casando `action["id"]` com `os.environ`. |
| `pos_action(action)` | **depois** de cada action terminar | Inspeção/leitura (ex.: `browser.ler_valor(xpath)`); capturar HTML antes de sair da tela (ver `job4`). |
| `finish()`           | ao **fim** do job (também por `exit`/`finish`) | Processamento final — ex.: `browser.pegar_html(cb)` e gravar em SQLite. |

Ordem por action: `pre_action(a)` → handler `_do_<tipo>(a)` → (assíncrono) →
`pos_action(a)`. A `pos_action` só dispara quando a action **realmente
terminou** (`_pending_pos`).

Exemplo mínimo (`job0`):

```python
import os

class Job:
    def __init__(self, browser, json_):
        self.browser = browser
        self.json_ = json_

    def pre_action(self, action_json):
        if action_json.get("id") == "principal":
            action_json["value"] = "https://exemplo.com/"
        if action_json.get("id") == "email":
            action_json["value"] = os.environ.get("EXEMPLO_EMAIL", "")
        if action_json.get("id") == "password":
            action_json["value"] = os.environ.get("EXEMPLO_PASSWORD", "")

    def pos_action(self, action_json):
        xpath = action_json.get("xpath")
        if xpath:
            self.browser.ler_valor(xpath)   # leitura assíncrona; loga o valor

    def finish(self):
        pass
```

### API do `browser` (facilitadores)

Métodos de `Browser` para manipular a página a partir do `run.py`. **Leitura é
assíncrona:** o valor chega no `callback` (assinatura `cb(valor)`); sem
`callback`, o método **loga** o valor. O DOM não cruza para o Python — strings
de HTML/texto, sim.

| Método | Assinatura | Retorno (no callback) |
|--------|------------|-----------------------|
| `navegar`           | `(url, callback=None)`                       | `callback(ok: bool)` ao `loadFinished` |
| `ler_valor`         | `(xpath, callback=None)`                     | `el.value` (input/textarea) |
| `ler_texto`         | `(xpath, callback=None)`                     | `el.textContent` |
| `ler_atributo`      | `(xpath, attr, callback=None)`               | `el.getAttribute(attr)` |
| `pegar_elemento`    | `(xpath, callback=None)`                     | `el.outerHTML` |
| `pegar_html`        | `(callback=None)`                            | `outerHTML` do documento inteiro |
| `escrever_elemento` | `(xpath, valor, enter=False)`                | — (escreve; se `enter=True`, dispara Enter e `requestSubmit`) |
| `clicar_elemento`   | `(xpath)`                                    | — (clica) |
| `esperar_resposta`  | `(xpath, callback, concluido_xpath=None, estavel=1.5, timeout=180.0)` | texto final do **último** elemento do xpath quando para de mudar |
| `esperar_pagina`    | `(callback, concluido_xpath=None, estavel=2.0, timeout=180.0)` | `document.body.innerText` quando a página inteira estabiliza |

- `esperar_resposta`/`esperar_pagina` fazem **polling** (a cada 700 ms) até o
  texto ficar `estavel` segundos sem mudar (e `pronto`), ou estourar o
  `timeout`. Servem para esperar **streaming** terminar (respostas de
  LLM/SPA). `concluido_xpath`: se dado, só considera "pronto" quando esse
  elemento existir (ex.: botão "copiar" que só aparece no fim) — evita parar na
  fase "Thinking".
- Para leituras "cruas", `escrever_elemento`/`clicar_elemento` não esperam
  callback (disparam e seguem).

---

## 11. Credenciais

Os `run.py` leem credenciais de **variáveis de ambiente** e as injetam via
`pre_action`. **Nunca** coloque segredos no `job.json` versionado. Carregue o
`.env` (gitignored) antes de rodar:

```bash
set -a; source .env; set +a
python3 browser.py -s job0
```

Variáveis por job (ver `.env.example`):

| Prefixo         | Jobs        | Site |
|-----------------|-------------|------|
| `EXEMPLO_*` | job0        | exemplo.com |
| `GMAIL_*`       | job1, job2  | conta Google/Gmail |
| `PAINEL_*`     | job3, job4  | admin painel.exemplo.com |

Sem as variáveis, os campos de login ficam vazios (o job roda, mas o login
falha). Ao criar um job novo com login, siga o padrão e documente as variáveis
no `.env.example`.

---

## 12. Protocolo do modo servidor (`--servir`)

Com `--servir [porta]` (padrão **8765**), `ServidorComandos` abre um `QTcpServer`
que escuta **apenas em `127.0.0.1`** e enfileira no `JobRunner` actions recebidas
de outro processo, com o **mesmo formato** do `job.json`. Tudo roda na thread
principal do Qt (integrado ao event loop por sinais) — **não** há threads
tocando a página.

No modo servidor, o título mostra a porta (`... job: <nome> :8765`);
`janela.porta_servir` guarda a porta e o título é recomposto por
`Browser._compor_titulo`, que mantém o `:{PORTA}` mesmo quando o cliente troca o
rótulo via `title`.

#### Spinner no título (sinal de atividade)

O **1º caractere** do título é um spinner que **avança a cada action executada**,
ciclando `|` → `/` → `-` → `\` → `|` … (quadros em `SPINNER`). É só um sinal
visual de que algo rodou — útil para ver à distância se o job/servidor está
progredindo. O título completo é sempre `<spinner> <rótulo> [:{PORTA}]`, composto
por `Browser._compor_titulo`: `JobRunner._next` chama `janela.girar_titulo()` por
action; a action `title` (§5) troca só o `<rótulo>` via `definir_titulo_base`,
preservando spinner e porta. Actions avulsas que **furam a fila** não passam por
`_next`, então não giram o spinner.

### Framing: NDJSON (um JSON por linha)

- **Transporte:** TCP. **Encoding:** UTF-8. **Delimitador:** `\n`.
- O servidor acumula bytes por socket e processa **linha a linha**; linha vazia
  (ou só espaços) é ignorada. Buffers parciais entre `readyRead` são mantidos.
- **Um pedido por linha; uma resposta por linha, na ordem dos pedidos.** Várias
  linhas podem ir na mesma conexão; as respostas voltam na ordem de envio.

### Pedido

Uma **action avulsa**:
```json
{"type": "navigate", "value": "https://example.com"}
```
Ou um **lote** (`{"actions": [...]}`):
```json
{"actions": [
  {"type": "navigate", "value": "https://html.duckduckgo.com/html/"},
  {"type": "key",  "xpath": "//input[@name='q']", "value": "busca"},
  {"type": "press","xpath": "//input[@name='q']"},
  {"type": "sleep","value": 3},
  {"type": "html", "id": "resultado"}
]}
```

### Resposta

Sucesso — `resultados` traz as saídas das actions de leitura (`html`/`url`/
`eval`) do lote, **na ordem em que rodaram**:
```json
{"ok": true, "resultados": [ {"type": "html", "id": "resultado", "html": "..."} ]}
```

Erro de pedido inválido (JSON malformado, `actions` não-lista etc.) — a resposta
de erro sai **na ordem** dos pedidos (um lote vazio é enfileirado só para
preservar a ordem):
```json
{"ok": false, "erro": "pedido invalido: ..."}
```

Cada item de `resultados` tem `type` (`html`/`url`/`eval`), o campo de dados
correspondente (`html`/`url`/`result`) e o `id` da action, se houver.

### Regras de execução

- Pedidos entram na **mesma fila** do `JobRunner` (§4.1): rodam um por vez,
  depois do job inicial (se houver). A resposta sai quando o **lote** termina.
- `title`/`comment` **avulsos** furam a fila (§6) e respondem
  `{"ok": true, "resultados": []}` na hora.
- Se o cliente **desconectar** antes da resposta, ela é descartada
  (`_responder` checa o buffer).
- **`exit`/`finish` não produzem resposta:** encerram o app antes de o lote
  terminar, então o servidor fecha a conexão **sem** mandar a linha de resposta.
  O cliente deve tratar o **EOF** (leitura vazia) como "encerrado", não como
  erro — é o que `cliente.py` faz (devolve `{"ok": true, "encerrado": true}`).
- **Segurança:** escuta só em `127.0.0.1`. Para acesso remoto, use túnel:
  `ssh -L 8765:127.0.0.1:8765 host`. Não há autenticação — qualquer processo
  local pode comandar o browser. `eval` executa JS arbitrário na página.

### Clientes

- **Referência:** `cliente.py` (só stdlib). Aceita `-H/--host`, `-p/--porta` e
  um ou mais pedidos JSON posicionais (cada um é um pedido na mesma conexão).
- **Exemplos:** Python, Bash, C++, C#, Java em `examples/` (ver
  `examples/README.md`). Qualquer linguagem com socket + JSON serve.

```bash
python3 browser.py --servir &
python3 cliente.py '{"type": "navigate", "value": "https://example.com"}'
python3 cliente.py '{"type": "html"}'
python3 cliente.py -p 9000 '{"actions": [
  {"type": "navigate", "value": "https://example.com"},
  {"type": "html", "id": "pagina"}]}'
```

---

## 13. Diálogos JS

`AutoDialogPage` (subclasse de `QWebEnginePage`) **auto-aceita** os diálogos JS:

| Diálogo   | Comportamento |
|-----------|---------------|
| `alert`   | fecha (aceita); loga `[dialogo] alert: <msg>` |
| `confirm` | retorna `true` (sempre OK) |
| `prompt`  | retorna `(true, defaultValue)` (aceita com o padrão) |

É **essencial**: sem isso um diálogo JS bloqueia o event loop esperando o clique
do usuário e trava a automação (de vez em headless).

A page também é **permissiva** (`_configurar_permissivo`): liga JS, imagens,
WebGL, autoplay, clipboard, conteúdo inseguro etc., e **auto-concede**
permissões pedidas pela página (câmera, microfone, geolocalização…) via
`page.permissionRequested` (Qt 6.8+). As permissões são ligadas **por page**
(em `Browser._criar_page`), então valem também nas janelas novas.

### 13.1 Janelas novas (`createWindow`) e multi-janela

Quando a página pede uma **janela/aba nova** — `window.open`, link
`target=_blank`, ou um redirect de relatório (GeneXus etc.) — o QtWebEngine chama
`AutoDialogPage.createWindow`. O default do Qt descarta o pedido (a janela "não
abre"); aqui o `createWindow` chama `Browser._abrir_janela`, que cria uma
`JanelaWeb` (`QMainWindow`) nova — **page própria, view própria**, no mesmo
perfil e config permissiva — e a registra em `Browser.janelas`.

- Há sempre uma **janela principal** (`#0` = o próprio `Browser`: tem log,
  barra de navegação, `runner` e o servidor). As demais são `JanelaWeb` e
  **compartilham o perfil** (login/cookies) com a principal.
- A janela nova **aparece** (cada janela é visível e mostra a sua page o tempo
  todo — diferente do antigo modelo de "abas", em que uma view só trocava de
  page). Ela **não** vira a ativa automaticamente: a automação continua
  determinística na janela atual.
- O servidor (e os facilitadores do `run.py`) dirigem sempre a **janela ativa**.
  `JobRunner.view` aponta para a view da janela ativa; `Browser._view_atual()`
  é o que os facilitadores usam. As actions `windows` (listar), `window`
  (trocar a ativa) e `window_close` (fechar) gerenciam isso (§5).
- `Browser.trocar_janela(i)` reaponta `runner.view` para a view da janela `i` e
  a traz para frente. `fechar_janela` não fecha a `#0` nem a única; fechar pela
  action ou **pelo usuário** (no X) passa por `JanelaWeb.closeEvent` →
  `Browser._janela_fechada`, que desregistra e reajusta a ativa. No shutdown,
  `_liberar` solta as pages de **todas** as janelas antes do profile.

### 13.2 Downloads (`downloadRequested`) → `~/Downloads/`

`Browser` conecta `profile.downloadRequested` a `_ao_baixar`, que **aceita** o
download (sem `accept()` o QtWebEngine o descartaria — era a limitação antiga) e
o salva em **`~/Downloads/`** (criada se faltar), mantendo o nome sugerido. Cada
download é registrado em `Browser.downloads` (`{path, recebido, total, estado}`)
e atualizado por sinais (`receivedBytesChanged`/`stateChanged`/
`isFinishedChanged`, conforme a versão do Qt). Como é **assíncrono**, use a
action `downloads` (§5) para saber quando concluiu. Vale para qualquer janela
(o profile é compartilhado), inclusive a aberta por `window.open`/redirect de
relatório (ex.: uma página que dispara o download de um PDF/planilha), que agora **baixa** o arquivo.

---

## 14. Log

Há **dois** logs independentes:

### 14.1 Log da janela (`Browser._log`)

Toda mensagem passa por `Browser._log`, que prefixa o horário `[HH:MM:SS]`. As
mensagens em `browser.py` são **ASCII, sem acento** (convenção). Vai para a
caixa de texto da janela (`self.saida`, um `QPlainTextEdit`), **não** para o
stdout. Indentação reflete a profundidade da pilha de frames. `print()` em
`run.py` vai para o stdout normalmente.

### 14.2 Log de eventos (`/tmp/browser_{PORTA}_events.log`)

Cada action executada pelo `JobRunner` é registrada como um **evento** num
arquivo, via o módulo `logging` (logger `craudiowebot.eventos`, configurado por
`configurar_log_eventos` no `main`). Serve para um processo externo
**acompanhar** o que o browser está fazendo, separado da caixa de log da janela.

- **Caminho:** `/tmp/browser_{PORTA}_events.log`, onde `PORTA` é a porta do
  `--servir`. **Sem `--servir`**, usa o **PID** do processo (para não colidir
  entre execuções).
- **Modo:** *append* (acumula entre execuções); cada execução começa com uma
  linha `=== inicio | job=<nome> | porta=<porta> ===`.
- **Formato de linha:** `YYYY-MM-DD HH:MM:SS <descrição do evento>`.
- **Descrição por tipo:**
  - `navigate` → `navigate url=<URL>` (URL já resolvida, **após** `pre_action`);
  - `key` → `key xpath=<xpath> value=<oculto>` — **o texto digitado NUNCA é
    registrado** (privacidade);
  - demais → `<tipo>` + `xpath=<xpath>` (se houver) + `value=<valor>` (truncado
    a 200 chars).

O caminho efetivo é ecoado no log da janela na partida (`eventos: <caminho>`).
O evento é registrado **depois** do `pre_action` (logo a URL injetada em runtime
aparece) e **antes** do handler executar. Actions `title`/`comment` avulsas que
furam a fila (§6) **não** passam pelo `JobRunner` e, portanto, não entram neste
log.

> **Contrato:** o nome do arquivo (`/tmp/browser_{PORTA}_events.log`), o formato
> de linha e a regra de **nunca** registrar o `value` de `key` são estáveis.

---

## 15. Ponte JS interna

Toda interação com a página passa por `view.page().runJavaScript(codigo,
callback)` — **assíncrona** (valor no callback). Helpers JS injetados:

- **`JS_BUSCA_XPATH`** — `__byXPath(xp)`: resolve um XPath e devolve o primeiro
  nó (`document.evaluate(... FIRST_ORDERED_NODE_TYPE ...)`).
- **`JS_ESCREVER`** — `__escrever(el, val)`: escreve lidando com dois casos:
  - **input/textarea:** usa o **setter nativo** do `value` no protótipo
    (`HTMLInputElement`/`HTMLTextAreaElement`) e dispara `input`/`change`. O
    setter nativo é necessário para inputs controlados por **React/Vue**:
    atribuir `el.value` direto não atualiza o estado do framework, que
    re-renderiza o campo vazio.
  - **`contenteditable`** (ProseMirror, editores ricos): `selectAllChildren` +
    `execCommand('insertText', ...)`, que dispara `beforeinput`/`input` —
    eventos que esses editores escutam (fallback para `textContent` + `InputEvent`).

Convenção de status para escrita/clique: o JS retorna `"OK"` ou
`"NAO_ENCONTRADO"`; `_apos_js` loga o aviso quando não encontra.

---

## 16. Integração a partir de outros projetos

O craudiowebot foi pensado para ser **dirigido por outro processo** via
`--servir`. Padrão recomendado:

1. **Subir o browser** com perfil persistente (e, se preciso, um job inicial de
   login que **não** termine com `exit`/`finish`):
   ```bash
   python3 browser.py -s login --servir 8765 \
     -d ~/.local/data/craudiowebot/minha-conta &
   ```
   Em CI/headless: prefixe `QT_QPA_PLATFORM=offscreen`.

2. **Esperar a porta** abrir (o servidor escuta em `127.0.0.1:<porta>`). Não há
   handshake; tente conectar com retry.

3. **Comandar** mandando NDJSON e lendo uma resposta por pedido (§12). Para
   obter dados de volta, use actions de leitura (`html`/`url`/`eval`) com `id`:
   ```json
   {"actions": [
     {"type": "navigate", "value": "https://site/area"},
     {"type": "eval", "value": "JSON.stringify(window.__STATE__)", "id": "estado"},
     {"type": "html", "xpath": "//table", "id": "tabela"}
   ]}
   ```
   A resposta traz `resultados[i].id` para casar cada saída.

4. **Lifecycle:** uma conexão pode mandar vários pedidos; respostas vêm na
   ordem. Pedidos longos seguram a fila (rodam um por vez). Para anotar/rotular
   sem esperar, mande `title`/`comment` avulsos (furam a fila, §6).

5. **Monitorar** o que o browser está fazendo, em paralelo aos pedidos: faça
   *tail* do **log de eventos** `/tmp/browser_{PORTA}_events.log` (§14.2). Como
   `PORTA` é a porta do `--servir`, o caminho é determinístico para quem subiu o
   browser. Ex.: `tail -F /tmp/browser_8765_events.log`. O texto digitado
   (`value` de `key`) **não** aparece ali — é seguro coletar/encaminhar.

6. **Encerrar:** mande `{"type": "exit"}` (fecha o app) ou mate o processo.

**Contratos estáveis** (o que outro projeto pode depender):

- Schema do `job.json` (§3) e dos tipos de action (§5).
- Framing NDJSON e shape de resposta `{"ok", "resultados"|"erro"}` (§12).
- Shape dos itens de `resultados`: `{type, html|url|result, id?}`.
- Precedência de perfil (§7), proxy (§8) e UA (§9).
- Log de eventos: caminho `/tmp/browser_{PORTA}_events.log`, formato de linha
  `YYYY-MM-DD HH:MM:SS <evento>`, e a regra de **nunca** registrar o `value` de
  `key` (§14.2).

**Não** dependa de: mensagens de log (texto livre), layout da janela, nomes de
métodos privados (`_do_*`, `_next`…) — esses podem mudar.

**Embutir como biblioteca** (mesmo processo Python) também é possível: importe
`browser`, crie a `QApplication`, instancie `Browser(job, data_dir, job_dir)` e
use `janela.runner.executar(actions, ao_terminar)` ou os facilitadores
(`janela.pegar_html(cb)` etc.). Como o GUI exige o event loop do Qt, isso só faz
sentido dentro de uma app Qt.

---

## 17. Limitações e gotchas

- **`eval` não aguarda Promises.** Para esperar async no site, faça polling com
  `eval` repetidos ou use `esperar_pagina`/`esperar_resposta` no `run.py`.
- **`xpath` pega só o primeiro nó.** Para listas, itere via `eval`.
- **`exit`/`finish` fecham o app inteiro** — inclusive em `--servir`.
- **Sem timeout global de job.** Um `sleep` longo (ex.: 180 s no `job1`) segura a
  fila; jobs de teste devem usar sleeps curtos (`job0`).
- **Headless não imprime o log** no terminal (vai para o `QPlainTextEdit`). Para
  acompanhar de fora, use o log de eventos (§14.2).
- **Log de eventos acumula** (modo *append*) e não tem rotação — limpe/rotacione
  `/tmp/browser_*_events.log` por fora se crescer. Sem `--servir`, o nome usa o
  PID, então cada execução gera um arquivo novo.
- **Servidor sem autenticação**, só `127.0.0.1`. `eval` = execução de JS
  arbitrário. Trate a porta como acesso total ao browser/perfil.
- **Detecção de automação:** alguns sites detectam headless e desviam (ex.:
  `duckduckgo.com` → use `html.duckduckgo.com`). Trocar UA (§9) ajuda em parte.
- **Não há suíte de testes nem linter.** Valide mudanças rodando o `job0`.

---

## 18. Referência rápida

### Flags CLI
`-s/--script <job>` · `-d/--data-dir <dir>` · `-p/--proxy <proxy>` ·
`--servir [PORTA=8765]`

### Campos do job
`name` · `data_dir`(=`profile`) · `user_agent` · `proxy`(string|array) ·
`actions[]`

### Tipos de action
`navigate` · `key` · `click` · `press` · `sleep` · `exists` · `exit` ·
`finish` · `html` · `url` · `eval` · `title` · `comment` · `user_agent` ·
`zoom` · `save_profile` · `load_profile` · `clear_profile`

### Item de resultado (leitura)
`{"type": "html",  "html": "...",  "id"?}` ·
`{"type": "url",   "url": "...",   "id"?}` ·
`{"type": "eval",  "result": ...,  "id"?}`

### Resposta do servidor
`{"ok": true, "resultados": [...]}` · `{"ok": false, "erro": "..."}`

### Log de eventos
`/tmp/browser_{PORTA}_events.log` (PORTA do `--servir`; senão PID) — uma linha
por action; `key` grava `value=<oculto>` (nunca o texto digitado). Ver §14.2.

### Constantes (`browser.py`)
`USER_AGENT` · `PAUSA_ENTRE_ACOES`=300ms · `EXISTS_POLL_MS`=300ms ·
porta servidor padrão **8765** · `navigate.timeout` padrão **30s** ·
`finish.wait` padrão **3s**

### Precedências
Perfil: `-d` > `data_dir`/`profile` > `/tmp/<uuid>`
Proxy: `-p` > `proxy` (JSON) > sistema
UA: action `user_agent` (runtime) sobre campo `user_agent` (partida) sobre `USER_AGENT`
uBlock: `--sem-ublock` > campo `ublock` (JSON) > default (ligado)

---

## 19. Extensões (uBlock Origin Lite)

O browser empacota o **uBlock Origin Lite** (uBOL, Manifest **V3**) em
`data/extensions/uBOLite.chromium/` e o instala no perfil **na partida**, para
bloquear anúncios e rastreadores durante a automação. É carregado por
`carregar_extensoes(profile, log, habilitado)`, chamada no `Browser.__init__`
logo após o perfil e a caixa de log existirem.

### Por que uBOL (MV3) e não o uBlock Origin (MV2)

A API de extensões do QtWebEngine — `QWebEngineProfile.extensionManager()` —
**só existe a partir do Qt 6.10**. E o Chromium embutido no Qt 6.10+ **já
removeu o Manifest V2**: tentar instalar uma extensão MV2 (como o uBlock Origin
clássico) falha com `error() == "Unsupported manifest version"`. Não há versão
do Qt em que a API de extensões e o MV2 coexistam — por isso usamos o uBOL, que
é MV3 e é o sucessor oficial do uBlock Origin para esse cenário.

### Mecânica

- Diretório varrido: `EXTENSOES_DIR = <browser.py>/data/extensions/`. Cada
  subpasta com um `manifest.json` é uma extensão candidata.
- Instala **uma de cada vez**: `installExtension(path)` e, no sinal
  `installFinished(info)`, dispara a próxima. (`installFinished` **não** entrega
  um bool — entrega um `QWebEngineExtensionInfo`; sucesso é `info.isLoaded()`,
  e a falha traz `info.error()`.) Assim o nome logado sempre bate com o
  resultado, independente da ordem de conclusão do Chromium.
- **Reinstala limpo a cada partida.** Em perfil persistente, a extensão fica
  instalada em `<perfil>/Extensions/<nome>_*`. Numa 2ª execução,
  `installExtension()` **falharia** com `"Failed to create install directory"`
  (o diretório já existe) e a extensão **não carregaria** — era o bug em que o
  browser "abria sem uBlock" da 2ª execução em diante. Por isso
  `carregar_extensoes` **apaga a instalação anterior** (`shutil.rmtree` daquela
  subpasta) e reinstala do zero toda vez. No `__init__` o QtWebEngine ainda não
  carregou as extensões do disco, então apagar ali é seguro.
- **Não usamos `loadExtension()` nem `setExtensionEnabled()`** nesta versão do
  QtWebEngine: `loadExtension()` carrega a extensão já instalada porém
  **desabilitada** (não bloqueia nada), e `setExtensionEnabled()` **trava o
  processo** (segfault). Reinstalar via `installExtension()` já vem habilitado e
  bloqueando — por isso é o caminho escolhido.
- **Assíncrono:** o job já pode começar a navegar antes de a extensão terminar
  de carregar; ela passa a valer assim que carrega (tipicamente ~1 s).
- **Perfil em memória** (sem `-d`): a instalação pode ser recusada; nesse caso
  apenas logamos a falha — não trava. Para uBlock efetivo, use perfil
  persistente (`-d` ou `data_dir`).

### Liga/desliga

| Origem                         | Efeito |
|--------------------------------|--------|
| (default)                      | uBOL **ligado**. |
| campo `"ublock": false` no JSON | desliga. |
| flag `--sem-ublock`            | desliga (precedência máxima). |

Precedência: `--sem-ublock` > campo `ublock` (JSON) > default (ligado). Não há
action de runtime para ligar/desligar; é decidido na partida (como o proxy).

### Atualização / troca da extensão

A extensão é versionada no repositório (`data/extensions/uBOLite.chromium/`).
Para atualizar, substitua a pasta pelo novo release **`.chromium` (MV3)** do
uBOL e valide com o `job0` (deve logar `extensoes: carregada: uBlock Origin
Lite`). Qualquer outra extensão **MV3** colocada em `data/extensions/` também é
carregada automaticamente (não há lista fixa de nomes).
