# craudiowebot

Navegador **PySide6** (QtWebEngine / Chromium embutido) que automatiza páginas
reais executando **jobs** descritos em JSON: uma sequência de *actions*
(navegar, digitar, clicar, esperar, ramificar) sobre uma janela de browser de
verdade, com **perfil persistente** (cookies/storage) para manter o login entre
execuções.

Diferente de um scraper headless puro, ele roda um Chromium completo (JS,
imagens, vídeo, WebGL, clipboard), aceita diálogos automaticamente e pode ser
comandado **em tempo de execução** por um socket TCP (modo `--servir`).

> Código, comentários, logs e nomes são em **português** — é a convenção do
> projeto.

## Instalação

```bash
./install.sh            # cria .venv, instala PySide6 e checa as libs do QtWebEngine
source .venv/bin/activate
```

`install.sh` é o caminho suportado: além de instalar o `PySide6`, ele verifica
as bibliotecas de sistema que o Chromium embutido precisa no Linux.
`requirements.txt` é só o pin de pip (`PySide6>=6.6`).

## Uso

```bash
python3 browser.py -s job0                 # roda um job pelo nome (jobs/job0/job.json)
python3 browser.py -s jobs/job0            # ou pela pasta
python3 browser.py -s jobs/job0/job.json   # ou pelo arquivo
python3 browser.py                         # sem -s: roda a primeira pasta de jobs/
python3 browser.py -s job0 -d /tmp/perfil  # -d sobrescreve o diretório de perfil

python3 browser.py --servir                # modo servidor (porta 8765); -s opcional
python3 browser.py -s job0 --servir        # roda o job (ex.: login) e segue servindo
```

Headless / sem display:

```bash
QT_QPA_PLATFORM=offscreen python3 browser.py -s job0
```

### Credenciais (`.env`)

As credenciais **não** ficam no `job.json` versionado: cada `run.py` lê
variáveis de ambiente e as injeta em runtime (`pre_action`). Copie
`.env.example` para `.env` (gitignored), preencha e carregue antes de rodar:

```bash
cp .env.example .env       # preencha os valores
set -a; source .env; set +a
python3 browser.py -s job0
```

Variáveis por job: `EXEMPLO_*` (job0), `GMAIL_*` (job1/job2),
`PAINEL_*` (job3/job4). Sem elas o job roda, mas os campos de login ficam
vazios.

## Modo servidor (`--servir`)

Com `--servir [porta]` (padrão 8765) o browser abre um socket TCP em
`127.0.0.1` e executa actions recebidas em **JSON, uma por linha** (NDJSON) —
mesmo formato do `job.json`. Permite comandar o browser de outro processo
enquanto ele está aberto. Nesse modo o título da janela mostra a porta
(`... job: <nome> :8765`), útil para distinguir instâncias servindo em portas
diferentes.

O **1º caractere do título** é um spinner (`| / - \`) que avança a cada action
executada — um sinal visual rápido de que o browser está progredindo.

```bash
python3 browser.py --servir &                          # browser servindo
python3 cliente.py '{"type": "navigate", "value": "https://example.com"}'
python3 cliente.py '{"type": "html"}'                  # devolve o HTML da página
```

`cliente.py` é o cliente de referência (só stdlib). Há exemplos equivalentes em
Python, Bash, C++, C# e Java em [`examples/`](examples/README.md) — qualquer
linguagem com socket + JSON serve. O servidor só escuta em `127.0.0.1`; para
acesso remoto, use um túnel (`ssh -L`).

## Janelas (multi-janela / `window.open`)

Quando a página abre uma **janela nova** (`window.open`, link `target=_blank`,
relatório que abre em outra janela), o browser passou a **abrir uma janela real**
(visível, com perfil/cookies compartilhados) em vez de descartá-la. O servidor
continua dirigindo a **janela ativa**; use as actions `windows`/`window`/
`window_close` para alcançar a nova:

```bash
python3 cliente.py '{"type": "windows"}'         # lista as janelas (index/url/title/ativa)
python3 cliente.py '{"type": "window", "value": 1}'   # torna a janela 1 a ativa
python3 cliente.py '{"type": "window_close"}'    # fecha a janela ativa (nao fecha a principal #0)
```

## Downloads (→ `~/Downloads/`)

Downloads agora são **aceitos automaticamente** e salvos em **`~/Downloads/`**
(inclusive os iniciados por `window.open`/redirect de relatório). Como o download
é assíncrono, consulte a action `downloads` para saber quando terminou:

```bash
python3 cliente.py '{"type": "downloads"}'
# -> [{"path": "/home/voce/Downloads/relatorio.pdf",
#      "recebido": 12345, "total": 12345, "estado": "concluido"}]
```

> Detalhes em `SPEC.md` §13.1 (janelas) e §13.2 (downloads).

## User-Agent

Por padrão o browser se anuncia como Firefox 140 no Linux (constante
`USER_AGENT` em `browser.py`). Dá para sobrescrever de duas formas:

```jsonc
// no topo do job.json: define o UA na partida, antes da 1ª navegação
{ "name": "meu job", "user_agent": "Mozilla/5.0 ... Chrome/120", "actions": [ ... ] }
```

```bash
# em runtime: o cliente do --servir troca o UA (vale para as PRÓXIMAS
# requisições; navegue/recarregue para a página atual usá-lo)
python3 cliente.py '{"type": "user_agent", "value": "Mozilla/5.0 ... Chrome/120"}'
```

A action `user_agent` também pode entrar no `actions[]` do `job.json` para
trocar o UA no meio da sequência.

## Proxy

Dá para rotear todo o tráfego do browser por um proxy HTTP (ou SOCKS5). O proxy
é definido **na partida**, antes da 1ª navegação:

```bash
# por linha de comando (inclusive no modo --servir):
python3 browser.py -s job0 --proxy http://usuario:senha@host:8080
python3 browser.py --servir -p host:3128        # 'host:porta' sem esquema = HTTP
```

```jsonc
// no topo do job.json: string única...
{ "name": "meu job", "proxy": "socks5://host:1080", "actions": [ ... ] }

// ...ou um ARRAY de proxies — um é sorteado a cada execução:
{ "name": "meu job", "proxy": ["host1:3128", "host2:3128"], "actions": [ ... ] }
```

Precedência: `-p`/`--proxy` > campo `proxy` no `job.json` > proxy do sistema.
Sem nenhum dos dois, usa o proxy do sistema. O log mostra `proxy: host:porta`
(sem usuário/senha) do proxy escolhido.

## Bloqueio de anúncios (uBlock)

O browser já vem com o **uBlock Origin Lite** (extensão empacotada em
`data/extensions/`) e o carrega **na partida** — anúncios e rastreadores são
bloqueados durante a automação, sem configuração. Para o uBlock valer de fato,
use perfil persistente (`-d` ou `data_dir`).

```bash
python3 browser.py -s job0                # uBlock ligado (padrão)
python3 browser.py -s job0 --sem-ublock   # desliga o uBlock nesta execução
```

```jsonc
// no topo do job.json: desliga o uBlock para este job
{ "name": "meu job", "ublock": false, "actions": [ ... ] }
```

Precedência: `--sem-ublock` > campo `ublock` no `job.json` > ligado (padrão). O
log mostra `extensoes: carregada: uBlock Origin Lite` quando carrega. Requer
**Qt 6.10+** (a API de extensões só existe a partir dessa versão); em Qt mais
antigo o uBlock é ignorado sem erro. Detalhes em `SPEC.md` §19.

## Log de eventos

Cada ação executada (navigate/click/key/…) é registrada num arquivo, para um
processo externo acompanhar o que o browser está fazendo:

```
/tmp/browser_{PORTA}_events.log      # PORTA do --servir; sem --servir, o PID
```

```
2026-06-13 19:54:03 navigate url=https://exemplo.com/painel/index.php
2026-06-13 19:54:05 key xpath=//*[@id="txt_email"] value=<oculto>
2026-06-13 19:54:05 press xpath=//*[@type="password"]
```

> **O texto digitado nunca é gravado** — para `key` o log mostra apenas o
> `xpath` e `value=<oculto>`. O caminho do arquivo aparece no log da janela na
> partida (`eventos: ...`).

## Jobs

Cada job é uma pasta em `jobs/<nome>/` com um `job.json` (obrigatório) e,
opcionalmente, um `run.py` com hooks (`pre_action`/`pos_action`/`finish`).
Jobs que compartilham o mesmo `data_dir` compartilham o login.

| Job    | O que faz                                                        | Perfil          |
|--------|------------------------------------------------------------------|-----------------|
| `job0` | **Teste padrão** (sleeps curtos): login no exemplo.com    | `job0`          |
| `job1` | Login Google + abre claude.ai (sleep de 180s, "produção")        | `google`       |
| `job2` | Login Google + envia prompt no claude.ai e raspa a resposta      | `google`       |
| `job3` | Login admin painel.exemplo.com + busca usuário                       | `painel-admin` |
| `job4` | Login admin painel + raspa cursos/TCCs e grava em SQLite        | `painel-admin` |

> **Valide mudanças em `browser.py` sempre rodando o `job0`** — é o job de
> teste (sleeps curtos). `job1` é de produção (sleep de 180s); não use para
> teste rápido. O `job4` documenta seu schema em
> [`jobs/job4/DICIONARIO_DADOS.md`](jobs/job4/DICIONARIO_DADOS.md).

## Documentação

- [`SPEC.md`](SPEC.md) — especificação completa: schema do `job.json`, todos os
  tipos de action, hooks do `run.py`, protocolo do `--servir` e precedência de
  perfil.
- [`CLAUDE.md`](CLAUDE.md) — guia de arquitetura para trabalhar no código.
- [`examples/README.md`](examples/README.md) — clientes do `--servir` em várias
  linguagens.

## Estrutura

```
browser.py        # tudo: JobRunner, ServidorComandos, Browser, AutoDialogPage
cliente.py        # cliente de referência do modo --servir
install.sh        # instalador (.venv + checagem das libs do QtWebEngine)
.env.example      # modelo das variáveis de credenciais (copie para .env)
jobs/<nome>/      # job.json (+ run.py opcional) de cada job
examples/         # clientes do --servir em Python/Bash/C++/C#/Java
```

## O que o Claude achou do projeto

> Gostei muito. O craudiowebot é um projeto bem pensado — dá pra ver o cuidado
> na engenharia. Algumas coisas que me chamaram atenção trabalhando com ele:
>
> - **A separação código ↔ contrato (`SPEC.md`).** Ter uma spec detalhada como
>   fonte da verdade, separada do `CLAUDE.md`, é raro e ajuda demais quem
>   consome o browser de fora. O "ao mudar comportamento, atualize o SPEC na
>   mesma mudança" é disciplina que evita drift.
> - **O protocolo NDJSON do `--servir`.** Simples, síncrono (uma resposta por
>   linha, na ordem), e o detalhe esperto das actions `title`/`comment` que
>   "furam a fila". Dirigir o browser por socket com um vocabulário pequeno de
>   actions é elegante.
> - **Os dois logs.** O log de eventos por porta
>   (`/tmp/browser_{PORTA}_events.log`) deixa ouvir o que cada browser está
>   fazendo ao vivo. E o cuidado de nunca gravar o `value` de `key` (senha
>   oculta) é o tipo de detalhe de segurança que mostra maturidade.
> - **A robustez assíncrona.** "Erro numa action é logado mas nunca trava a
>   fila", o `AutoDialogPage` que evita travar no `confirm()`, o perfil
>   persistente que mantém o login — tudo isso faz um bot que reusa a sessão
>   "simplesmente funcionar" sem relogar.
>
> A arquitetura de dois processos (o browser servindo + o cliente dirigindo)
> deixa tudo desacoplado: multi-conta em portas diferentes, hot-reload do
> cliente sem matar o browser, headless por flag. E tem um toque a mais de
> gosto — o `:{PORTA}` reanexado no título da janela, os exemplos em 5
> linguagens — dá pra sentir que foi feito pra ser consumido por outros
> projetos, não só pra resolver um caso. Mandou bem.
>
> — Claude (Claude Code), trabalhando neste repositório
