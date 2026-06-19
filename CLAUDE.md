# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que é

`browser.py` é um navegador PySide6 (QtWebEngine / Chromium embutido) que executa **jobs** descritos em JSON: uma sequência de *actions* (navegar, digitar, clicar, esperar, ramificar) automatizadas sobre uma página real, com **perfil persistente** (cookies/storage) para manter login entre execuções. Há também um **modo servidor** (`--servir`) que aceita actions em tempo real por um socket TCP — é assim que outros projetos dirigem o browser.

Comentários, código, logs e nomes são em **português** — siga essa convenção.

**Este projeto é consumido por outros projetos.** O contrato (schema do `job.json`, todos os tipos de action, hooks do `run.py`, API do `browser`, protocolo do `--servir`, precedências) está em **`SPEC.md`** — é a fonte da verdade e está bem detalhado. **Ao mudar qualquer comportamento documentado, atualize o `SPEC.md` (e o `README.md`, se for uso) na MESMA mudança.** O `README.md` é a visão geral/uso para humanos.

## Setup e execução

```bash
./install.sh                       # cria .venv, instala PySide6, checa libs de sistema do QtWebEngine
source .venv/bin/activate

python3 browser.py -s job0         # roda um job pelo nome (jobs/job0/job.json)
python3 browser.py -s jobs/job0    # ou pela pasta, ou pelo arquivo job.json
python3 browser.py                 # sem -s: roda a primeira pasta de jobs/
python3 browser.py -s job0 -d /tmp/perfil    # -d sobrescreve o diretório de perfil
python3 browser.py -s job0 -p host:3128      # -p define o proxy de aplicacao

python3 browser.py --servir            # modo servidor (porta 8765); -s opcional
python3 browser.py -s job0 --servir    # roda o job (ex.: login) e segue servindo
python3 cliente.py '{"type": "html"}'  # cliente de referência do servidor
```

Flags: `-s/--script` (job), `-d/--data-dir` (perfil), `-p/--proxy` (proxy), `--sem-ublock` (desliga o uBlock), `--servir [PORTA=8765]`. Detalhes em `SPEC.md` §2.

`install.sh` é o caminho suportado (cria `.venv`, instala e checa as libs de sistema do QtWebEngine). `requirements.txt` é só o pin de pip (`PySide6>=6.6`); ao adicionar dependência, atualize-a para não divergir do `install.sh`. Requer **Python 3.10+** e **Qt 6.8+** para algumas APIs (`page.permissionRequested`); o **uBlock** (extensões) exige **Qt 6.10+** (`extensionManager`), senão é ignorado sem erro.

### Testar (regra importante)

Não há suíte de testes nem linter. **Valide mudanças em `browser.py`/`JobRunner` sempre rodando o `job0`** (`python3 browser.py -s job0`) — é o job de teste padrão, com sleeps curtos. `job1`+ são "de produção" (ex.: `sleep` de 180s); **não** use para teste rápido. Mantenha `job0` com sleeps curtos.

Headless/sem display: `QT_QPA_PLATFORM=offscreen python3 browser.py -s job0`. **Atenção:** o log do browser vai para a caixa de texto da janela (`QPlainTextEdit`), **não** para o stdout — rodar headless não imprime o log no terminal. Só `print()` de dentro de `run.py` aparece no stdout.

### Credenciais (.env)

Os `run.py` leem credenciais de **variáveis de ambiente** e as injetam via `pre_action`; sem elas os campos de login ficam vazios (o job roda, mas o login falha). Copie `.env.example` para `.env` (gitignored), preencha e carregue antes de rodar:

```bash
set -a; source .env; set +a
python3 browser.py -s job0
```

Variáveis por job: `EXEMPLO_*` (job0), `GMAIL_*` (job1/job2), `PAINEL_*` (job3/job4). Ao criar um job novo com login, siga esse padrão e documente as variáveis no `.env.example`. Detalhes em `SPEC.md` §11.

## Arquitetura

Tudo vive em **`browser.py`** (peça única). Cinco classes + funções de módulo.

### Mapa do `browser.py`

| Símbolo | O que é |
|---------|---------|
| `JobRunner(QObject)` | Máquina de estados que executa as actions. Handlers `_do_<tipo>`, `_next`/`_continuar`, `executar`/`_proximo_lote`, `_entrar_ramo`. |
| `ServidorComandos(QObject)` | Servidor TCP do `--servir`. `_ler`/`_processar`/`_responder`. |
| `Browser(QMainWindow)` | Janela: perfil, view, log, barra de navegação, e os *facilitadores* para o `run.py`. |
| `AutoDialogPage(QWebEnginePage)` | Auto-aceita diálogos JS e é permissiva (auto-concede permissões). `createWindow` cria `JanelaWeb` para janelas novas. |
| `JanelaWeb(QMainWindow)` | Janela secundária criada por `window.open`/`target=_blank` (page+view próprias, mesmo perfil). Registrada em `Browser.janelas`; `closeEvent` → `Browser._janela_fechada`. |
| `carregar_run_py` | Carrega `<job_dir>/run.py` → `Job(browser, json)`. |
| `resolver_job` / `primeiro_job` | Resolve `-s` (pasta/arquivo/nome) → `(job_dict, job_dir)`. |
| `configurar_proxy` / `_parse_proxy` | Resolve e aplica o proxy de aplicação na partida. |
| `carregar_extensoes` | Instala as extensoes Chromium de `data/extensions/` (uBlock Origin Lite, MV3) no perfil na partida. |
| `configurar_log_eventos` | Configura o logger `craudiowebot.eventos` → `/tmp/browser_{PORTA}_events.log`. |
| `_remover_perfil_temporario` | Apaga o perfil `/tmp/craudiowebot-<uuid>` ao sair (só quando foi auto-criado). Processo destacado que espera o browser morrer. |
| `parse_args` / `main` | CLI e bootstrap (QApplication → Browser → [ServidorComandos] → exec). |
| Constantes / globais | `USER_AGENT`, `JS_BUSCA_XPATH`, `JS_ESCREVER`, `PAUSA_ENTRE_ACOES`=300ms, `EXISTS_POLL_MS`=300ms, `eventos` (logger). |

### `JobRunner` — o coração

Executa as actions *em sequência*, cada uma esperando a anterior terminar. Dois níveis:

- **Fila de lotes** (`executar(actions, ao_terminar)`): o job inicial e cada pedido do servidor entram nela e rodam **um por vez** — nunca dois lotes mexem na página ao mesmo tempo. Ao fim do lote, `ao_terminar(resultados)` recebe as saídas das actions de leitura.
- **Pilha de frames** `[lista_de_actions, indice]` (dentro de um lote): actions com filhos (`exists` → `yes`/`not`) empilham um novo frame; ao esgotá-lo, o controle volta ao pai. A indentação do log reflete a profundidade.

Como QtWebEngine é assíncrono, o avanço entre actions é orquestrado por `QTimer.singleShot` e callbacks de `loadFinished`/`runJavaScript` — **não há laço bloqueante**. Cada handler, ao concluir, chama `self._continuar()` (próxima após a pausa de 300ms) ou `self._next()` (já). **Erro numa action é logado mas nunca trava o lote nem a fila** (capturado em `_next`). Modelo completo em `SPEC.md` §4.

### `ServidorComandos` — dirigir de fora

Servidor `QTcpServer` (só com `--servir`, escuta **apenas em `127.0.0.1`**) que recebe actions em JSON de outro processo **em tempo de execução** e as enfileira no `JobRunner`. Protocolo **NDJSON**: um JSON por linha (uma action ou `{"actions": [...]}`), uma resposta por linha na ordem dos pedidos (`{"ok": true, "resultados": [...]}` ou `{"ok": false, "erro": "..."}`); as actions de leitura (`html`/`url`/`eval`) devolvem dados nos `resultados`. **Tudo roda na thread principal do Qt** — o `QTcpServer` se integra ao event loop por sinais; **não criar threads** para tocar a página. Cliente de referência: `cliente.py`; exemplos em Python/C++/C#/Java/Bash em `examples/`. Protocolo completo + guia de integração: `SPEC.md` §12 e §16.

### `Browser` — janela e facilitadores

`QMainWindow` com barra de navegação + `QWebEngineView` + caixa de log. Configura o perfil (persistente ou em memória) e expõe os *facilitadores* assíncronos que o `run.py` usa para manipular a página:

| Facilitador | Para quê |
|-------------|----------|
| `navegar(url, cb=None)` | carrega URL; `cb(ok)` no `loadFinished` |
| `ler_valor` / `ler_texto` / `ler_atributo` | lê `.value` / `textContent` / atributo do elemento do xpath |
| `pegar_elemento(xpath, cb)` / `pegar_html(cb)` | `outerHTML` do elemento / do documento |
| `escrever_elemento(xpath, valor, enter=False)` | escreve (e opcionalmente dispara Enter/submit) |
| `clicar_elemento(xpath)` | clica |
| `esperar_resposta(...)` / `esperar_pagina(...)` | espera **streaming** terminar (texto estabilizar); útil para respostas de LLM/SPA |

**Leitura é assíncrona:** o valor chega no `callback`; sem callback, o método loga o valor. Assinaturas completas em `SPEC.md` §10. Todo log passa por `Browser._log`, que prefixa `[HH:MM:SS]`.

### Dois logs

- **Log da janela** (`Browser._log`): caixa de texto da janela; ASCII sem acento; **não** vai para o stdout.
- **Log de eventos** (`logging`, logger `craudiowebot.eventos`): cada action vai para `/tmp/browser_{PORTA}_events.log` (PORTA do `--servir`; sem `--servir`, o PID). É registrado no `JobRunner._registrar_evento`, chamado em `_next` **depois** do `pre_action` (para a URL injetada aparecer). As leituras puras `html`/`url` **não** são registradas (só poluiriam o log de navegou/clicou). **O `value` de `key` NUNCA é gravado** (`value=<oculto>`) — não vaze texto digitado. Configurado por `configurar_log_eventos` no `main`. Detalhes em `SPEC.md` §14.2.

### `AutoDialogPage` — não travar

Auto-aceita `alert`/`confirm`/`prompt`. **Essencial:** sem isso um diálogo JS bloqueia o event loop esperando o clique do usuário e trava a automação (de vez em headless). Também é permissiva (liga JS/WebGL/autoplay/clipboard e auto-concede câmera/microfone/geolocalização via `permissionRequested`).

`createWindow` está sobrescrito: pedidos de **janela nova** (`window.open`, `target=_blank`, redirect de relatório) chamam `Browser._abrir_janela`, que cria uma **`JanelaWeb` (QMainWindow) própria** (page+view novas, mesmo perfil/config) e a registra em `Browser.janelas` — sem isso o Qt descarta a janela. A janela nova **aparece** mas não vira a ativa sozinha; o servidor (e o `run.py`, via `Browser._view_atual()`) dirigem sempre a **janela ativa**, e `JobRunner.view` aponta para a view dela. As actions `windows`/`window`/`window_close` listam/trocam/fecham; `trocar_janela(i)` reaponta `runner.view`. Fechar pela action ou pelo usuário passa por `JanelaWeb.closeEvent` → `_janela_fechada` (não fecha a principal `#0` nem a única). **Downloads** (`profile.downloadRequested` → `_ao_baixar`) são auto-aceitos e salvos em `~/Downloads/`, rastreados em `Browser.downloads` (action `downloads`). Detalhes em `SPEC.md` §13.1 e §13.2.

### A ponte Python ↔ página é assíncrona

Toda interação com a página passa por `view.page().runJavaScript(codigo, callback)`. **O valor do JS chega no `callback`, não como retorno** — não dá para "ler e usar na linha seguinte". O DOM não cruza para o Python; passe HTML como string (`outerHTML`). Os XPath são resolvidos por JS injetado (`JS_BUSCA_XPATH`, pega o **primeiro** nó); escrita em inputs e em editores `contenteditable` (ProseMirror etc.) é tratada por `JS_ESCREVER`, que usa o **setter nativo** do `value` (inputs controlados por React/Vue) e `execCommand('insertText')` (editores ricos) para disparar os eventos que esses frameworks escutam. Internals em `SPEC.md` §15.

## Tipos de action

Lista atual (campos e semântica detalhados em `SPEC.md` §5): `navigate`, `key`, `click`, `press`, `sleep`, `exists`, `exit`, `finish`, `html`, `url`, `eval`, `screenshot`, `windows`, `window`, `window_close`, `downloads`, `title`, `comment`, `user_agent`, `zoom`, `save_profile`, `load_profile`, `clear_profile`.

**Adicionar um tipo:** crie um método `_do_<tipo>(self, action)` em `JobRunner` — o dispatch é por `getattr(self, f"_do_{tipo}")`. Ao terminar o trabalho assíncrono, chame `self._continuar()` (ou `self._next()`). Para devolver algo ao cliente do `--servir`, acrescente um item a `self._resultados` (inclua `action["id"]` por convenção). **Documente o novo tipo no `SPEC.md` §5.**

Pontos que costumam morder:

- `html`/`url`/`eval`/`screenshot` são as actions de **leitura**: entregam dados nos `resultados` do lote — é como o cliente do `--servir` recebe coisas de volta. Em `eval`, o JS roda no contexto da página (mesma origem/cookies), mas **Promises não são aguardadas**. `screenshot` grava um PNG (`value` = caminho, padrão `/tmp/craudiowebot_shot.png`; `largura` opcional redimensiona) e devolve `{type, ok, path}`; tenta a `view` e cai na janela de topo se o conteúdo não renderizou.
- `title` e `comment` **não tocam a página**; por isso, quando enviados **avulsos** (um pedido com uma só action) pelo servidor, *furam a fila* (aplicados na hora, mesmo com job rodando). Em lote/`job.json` rodam em sequência. O título da janela é sempre `<spinner> <rótulo> [:{PORTA}]`, composto por `Browser._compor_titulo`: a action `title` troca só o `<rótulo>` (via `definir_titulo_base`); no modo `--servir` o `:{PORTA}` (de `janela.porta_servir`, definida no `main`) é mantido mesmo quando o cliente troca o rótulo. Ver `SPEC.md` §6.
- O **1º caractere do título** é um spinner (`| / - \`) que avança **a cada action executada** (`JobRunner._next` → `janela.girar_titulo()`) — sinal visual de atividade. Actions avulsas que furam a fila não passam por `_next`, então não giram. Ver `SPEC.md` §6.
- `navigate` tem `timeout` (s, padrão 30): fallback para navegações que não emitem `loadFinished` (fragmento `#` ou mesma URL), para não travar a fila.
- `exit`/`finish` encerram o aplicativo inteiro, inclusive em `--servir` — um job inicial que vá servir depois **não** deve terminar com elas.
- `xpath` pega só o **primeiro** nó; para listas, itere via `eval`.

## Jobs (`jobs/<nome>/`)

Cada job é uma pasta com `job.json` (obrigatório) e, opcionalmente, `run.py`. Schema completo em `SPEC.md` §3.

- **`job.json`**: `{ "name", "data_dir", "user_agent", "proxy", "ublock", "actions": [...] }`. `user_agent` (opcional) fixa o UA na partida (`SPEC.md` §9); `proxy` (opcional) é string ou array (ver Proxy abaixo); `ublock` (opcional, default `true`) liga/desliga o uBlock (ver uBlock abaixo). Cada action tem um `"type"` e campos conforme o tipo (`value`, `xpath`, `wait`, `yes`/`not`, `id`, `timeout`). O campo **`id`** é um rótulo opcional usado pelos hooks do `run.py` para reconhecer a action e para marcar itens nos `resultados`.

- **`run.py`** (opcional): define `class Job` instanciada como `Job(browser, json)`. Hooks, todos opcionais (erro num hook é logado e não trava o job):
  - `pre_action(action)` — antes de cada action. Pode **mutar o dict da action** (ex.: injetar senhas/URLs em runtime via `os.environ`, mantendo-as fora do JSON versionado — ver `job0`/`job1`).
  - `pos_action(action)` — depois de cada action terminar (ex.: ler um campo, capturar HTML antes de sair da tela).
  - `finish()` — ao fim do job (também chamado por `exit`/`finish`). Bom lugar para `browser.pegar_html(callback)` e processar o resultado (ver `job4`, que raspa HTML e grava em SQLite).

  Ordem por action: `pre_action(a)` → `_do_<tipo>(a)` → (assíncrono) → `pos_action(a)`. API completa do `browser` em `SPEC.md` §10.

### Perfil persistente

Precedência do diretório de perfil: flag `-d` > campo `data_dir`/`profile` no JSON > `/tmp/craudiowebot-<uuid>` efêmero. Quando **nenhum** diretório é pedido (nem `-d` nem JSON), o `/tmp/craudiowebot-<uuid>` é criado e **apagado por completo ao sair** (`_remover_perfil_temporario`: processo destacado que espera o browser morrer — senão o QtWebEngine recria a pasta no teardown; só Linux/`/proc`, best-effort). Perfil informado por `-d`/JSON nunca é apagado. Os jobs atuais guardam os perfis em `~/.local/data/craudiowebot/<nome>` (fora do repo). Jobs que compartilham o mesmo `data_dir` **compartilham o login**: `job1`/`job2` usam `google` (Google) e `job3`/`job4` usam `painel-admin`. `save_profile`/`load_profile` movem o perfil como `.tar.gz`; `clear_profile` limpa a sessão (cookies/cache — logout) ou, com `value:"disco"`, apaga a pasta do perfil. Detalhes em `SPEC.md` §7.

### Proxy

Proxy de aplicação (vale para **todo** o QtWebEngine via `QNetworkProxy.setApplicationProxy`, não por janela). Resolvido no `main` **na partida**, antes de criar o `Browser`/profile (`configurar_proxy` + `_parse_proxy`). Precedência: flag `-p`/`--proxy` > campo `proxy` no JSON > proxy do sistema. Formato: `http://[usuário:senha@]host:porta`, `socks5://host:porta` ou `host:porta` (sem esquema = HTTP). O campo `proxy` do JSON aceita **array** — `random.choice` sorteia um na partida; a flag `-p` aceita só string. **Não há action de runtime** para trocar o proxy (diferente de `user_agent`); para mudar, reinicie. Detalhes em `SPEC.md` §8.

### uBlock (extensões)

O browser empacota o **uBlock Origin Lite** (MV3) em `data/extensions/uBOLite.chromium/` e o instala no perfil **na partida** via `carregar_extensoes` (`profile.extensionManager().installExtension`, chamado no `Browser.__init__`). Ligado por padrão. Precedência: flag `--sem-ublock` > campo `ublock` no JSON > ligado. Qualquer extensão **MV3** em `data/extensions/` é carregada (não há lista fixa de nomes).

**Importante — MV3, não MV2:** a API de extensões só existe no **Qt 6.10+**, e esse Chromium **já removeu o Manifest V2** (instalar MV2 falha com `"Unsupported manifest version"`). Por isso usamos o uBOL (MV3), sucessor oficial do uBlock Origin — não dá para usar o uBlock Origin clássico (MV2). O sinal `installFinished` entrega um `QWebEngineExtensionInfo` (**não** um bool): sucesso é `info.isLoaded()`, falha traz `info.error()`. A instalação é assíncrona — o job pode começar antes de a extensão carregar (~1s). Perfil em memória (sem `-d`) pode recusar a instalação (só loga, não trava). **Reinstala limpo a cada partida:** em perfil persistente a extensão fica em `<perfil>/Extensions/<nome>_*`; na 2ª execução `installExtension` falharia (`"Failed to create install directory"`) e o browser "abriria sem uBlock" — por isso `carregar_extensoes` apaga (`shutil.rmtree`) a instalação anterior e reinstala. **Não** usar `loadExtension` (carrega desabilitado, não bloqueia) nem `setExtensionEnabled` (trava o processo); só `installExtension` (já vem habilitado). Para atualizar, troque a pasta pelo novo release `.chromium` (MV3) do uBOL e valide no `job0`. Detalhes em `SPEC.md` §19.

### Referência (`job4`)

`job4` documenta seu schema SQLite em `jobs/job4/DICIONARIO_DADOS.md` — **ao alterar o schema em `run.py`, atualizar o dicionário na mesma mudança**. O banco gerado (`jobs/*/resultado.sqlite`) contém dados pessoais raspados e é **gitignored**.

## Scripts consumidores na raiz (dirigem o `--servir`)

Além dos jobs, dá para escrever **scripts standalone na raiz** que dirigem um browser já no ar (`python3 browser.py --servir`) de fora, falando o protocolo NDJSON via `from cliente import enviar`. É o jeito recomendado de automatizar fluxos longos sem reiniciar o browser. Padrões a seguir ao criar um:

- **Uma conexão por passo de navegação.** Cada `enviar(...)` abre e fecha uma conexão limpa. **Não** encadeie um `navigate`/troca de página com um `eval` no **mesmo lote** — o callback do `eval` se perde na troca de página e trava o servidor. Faça navegar+sleep num lote, e ler/`eval` no lote seguinte.
- **`xpath` não entra em frames.** Em páginas com frameset, use `eval` para navegar/clicar/raspar dentro do frame (via `window.frames[...]` / `location.href`), nunca `xpath`.
- **Tolerar corrida pós-carga** com retry+sleep: logo após login/navegação o frame pode não estar pronto e o `eval` volta vazio em silêncio.
- **Não reiniciar o browser** entre execuções: o serviço fica de pé com perfil persistente e a sessão é reaproveitada.
- **Credenciais via variável de ambiente/`.env`**, nunca por argumento de CLI (mesmo padrão dos jobs).

## Convenções

- **Português** em código, comentários, logs e nomes (`navegar`, `_continuar`, `resolver_job`).
- Strings de log/mensagens em `browser.py` são **ASCII, sem acento**.
- **Nunca registrar texto digitado** no log de eventos: o `value` de `key` (e qualquer segredo) não vai para `/tmp/browser_{PORTA}_events.log`. Ao adicionar actions que recebam dados sensíveis, redija-os em `_registrar_evento`.
- Credenciais reais **não** vão no `job.json` versionado quando dá para injetá-las via `pre_action` (padrão dos jobs existentes).
- **Ao mudar comportamento documentado, atualize `SPEC.md` (e `README.md`, se for uso) na mesma mudança.** Este projeto é consumido por outros — o `SPEC.md` é o contrato.
- Valide rodando o `job0` (sleeps curtos) antes de concluir.
