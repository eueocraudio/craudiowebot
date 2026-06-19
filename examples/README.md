# Exemplos de clientes do modo `--servir`

Todos fazem a mesma coisa, cada um numa linguagem: conectam no servidor de
comandos do browser, navegam até o DuckDuckGo, pesquisam
**"Claude Code é o melhor do mundo"** e imprimem o HTML da página de
resultados no output.

O protocolo é só **JSON por linha num socket TCP** (`127.0.0.1:8765` por
padrão) — qualquer linguagem com socket e JSON serve. O pedido enviado é o
mesmo em todos:

```json
{"actions": [
  {"type": "navigate", "value": "https://html.duckduckgo.com/html/"},
  {"type": "key", "xpath": "//input[@name='q']", "value": "Claude Code é o melhor do mundo"},
  {"type": "press", "xpath": "//input[@name='q']"},
  {"type": "sleep", "value": 3},
  {"type": "html", "id": "resultado"}
]}
```

> Os exemplos usam a **versão HTML** do DuckDuckGo (`html.duckduckgo.com`):
> a versão principal (`duckduckgo.com`) detecta automação/headless e
> redireciona para uma página intermediária em vez dos resultados.

E a resposta é uma linha `{"ok": true, "resultados": [{"type": "html",
"id": "resultado", "html": "..."}]}`.

## Antes de rodar qualquer exemplo

```bash
source .venv/bin/activate
python3 browser.py --servir          # browser aberto servindo na porta 8765
```

Cada exemplo aceita a porta como primeiro argumento (padrão 8765). As
instruções de compilação/execução estão no diretório de cada linguagem:

| Linguagem | Diretório   | Dependências do cliente                     |
|-----------|-------------|---------------------------------------------|
| Python    | `python/`   | nenhuma (stdlib)                            |
| Bash      | `bash/`     | `jq` (e o `/dev/tcp` do próprio bash)       |
| C++       | `cpp/`      | `nlohmann-json` (header-only) + g++         |
| C#        | `csharp/`   | .NET SDK 8+ (sem pacotes externos)          |
| Java      | `java/`     | JDK 11+ e o jar do `org.json`               |
