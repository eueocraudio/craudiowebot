# Dicionário de Dados — `resultado.sqlite` (job4)

Banco SQLite gerado pelo **job4** (`jobs/job4/run.py`, método `_salvar_sqlite`) a cada
execução do plano de teste contra **painel.exemplo.com**.

- **Arquivo:** `jobs/job4/resultado.sqlite` (mesmo diretório do job).
- **Recriação:** as tabelas são **dropadas e recriadas** a cada execução
  (`DROP TABLE IF EXISTS ...` + `CREATE TABLE ...`). O arquivo guarda sempre o
  resultado da **última** coleta, não um histórico.
- **Origem dos dados:** raspagem da UI (HTML) — admin loga, abre a edição do
  professor (cursos) e impersona o professor para ler `/professor/tccs` (TCCs).
- **Carimbo de coleta:** coluna `coletado_em` (ISO-8601, segundos) — igual em
  todas as linhas da mesma execução.

> Ao alterar o schema em `run.py`, **atualizar este arquivo** na mesma mudança.

---

## Tabela `cursos`

Cursos aos quais o professor está vinculado (seção "Vínculos com Cursos" da tela
`/admin/usuarios/{id}/edit`).

| Coluna        | Tipo    | Nulo | Descrição |
|---------------|---------|------|-----------|
| `id`          | INTEGER | não  | PK, autoincremento. |
| `professor`   | TEXT    | não  | Nome do professor (input `name` da tela de edição). |
| `curso`       | TEXT    | não  | Nome do curso + código, ex.: `Análise e Desenvolvimento de Sistemas (ADS)`. Texto da `<option selected>` de `cursos[i][id]`. |
| `papel`       | TEXT    | sim  | Papel no curso: `Comum`, `Coordenador`, `Gestor de TCC` ou `Metodologia`. Da `<option selected>` de `cursos[i][papel]`. |
| `coletado_em` | TEXT    | não  | Data/hora da coleta (ISO-8601). |

## Tabela `tccs_orientados`

TCCs orientados pelo professor (tabelas de `/professor/tccs`, acessada via
impersonação: aguardando aceitação + em andamento + encerrados).

| Coluna        | Tipo    | Nulo | Descrição |
|---------------|---------|------|-----------|
| `id`          | INTEGER | não  | PK, autoincremento. |
| `professor`   | TEXT    | não  | Nome do professor orientador (mesmo de `cursos.professor`). |
| `titulo`      | TEXT    | não  | Título do TCC (1ª célula da linha da tabela). |
| `detalhes`    | TEXT    | sim  | Demais células da linha, unidas por ` \| ` — pode conter curso, alunos, status e o rótulo de ação ("Ver"). Campo livre, não normalizado. |
| `coletado_em` | TEXT    | não  | Data/hora da coleta (ISO-8601). |

---

## Observações / limitações

- **`detalhes` não é normalizado**: as colunas variam entre as 3 tabelas de
  origem (aguardando/andamento/encerrados), então alunos, curso e status ficam
  concatenados num só campo (inclui o texto do botão "Ver").
- Não há FK entre `cursos` e `tccs_orientados`; o vínculo é apenas pelo nome em
  `professor`.
- O plano hoje coleta um único professor por execução (Fulano).
