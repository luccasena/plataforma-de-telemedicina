# Plataforma de Telemedicina

## Infraestrutura local

Para iniciar MongoDB, Redis e Neo4j:

```bash
docker compose up -d
```

## Gerar dados iniciais

Com os containers em execução, gere as coleções, os validadores e os dados de
exemplo no MongoDB e no Neo4j. Depois, aqueça o cache das agendas futuras no
Redis:

```bash
python src/config/seed.py
python src/config/seed_agendas.py
```

## Executar localmente (Windows PowerShell)

Na raiz do projeto, crie o ambiente Python, instale as dependências e prepare
os arquivos locais de configuração:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env-example .env }
if (-not (Test-Path .streamlit\secrets.toml)) {
    Copy-Item .streamlit\secrets-example.toml .streamlit\secrets.toml
}
```

Confirme que as credenciais de `.env` correspondem às URLs e credenciais em
`.streamlit\secrets.toml`. Suba os bancos, gere os dados e inicie o Streamlit:

```powershell
docker compose up -d
python src/config/seed.py
python src/config/seed_agendas.py
$env:PYTHONPATH = "src"
streamlit run src/app.py
```

Para executar os testes do cache de agendas, em outro terminal com o mesmo
ambiente virtual ativado:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s src/tests -v
```

Serviços disponíveis:

- MongoDB: `localhost:27018`
- Redis: `localhost:6379`
- Neo4j Browser: `http://localhost:7474`
- Neo4j Bolt: `bolt://localhost:7687`

As credenciais podem ser sobrescritas com as variáveis `MONGO_ROOT_USER`,
`MONGO_ROOT_PASSWORD`, `REDIS_PASSWORD` e `NEO4J_AUTH`.

## Recepção Virtual: fila e agenda

A tela **Recepção Virtual** apresenta a fila ordenada por chegada, posição e
tempo de espera. A recepção só pode adicionar à fila consultas com status
`Agendado` no prontuário; **Chamar próximo paciente** retira o primeiro da fila,
muda a consulta para `Em andamento` e a exibe em **Consultas em atendimento**. A tela lista também as sessões Redis ainda
válidas e atualiza os dados automaticamente a cada 15 segundos (ou pelo botão
**Atualizar agora**).

A agenda do médico e do dia selecionados usa cache-aside: consulta primeiro
`agenda:medico:<medico_id>:<AAAA-MM-DD>` no Redis e, em caso de ausência,
consulta os agendamentos confirmados/em atendimento no MongoDB e guarda o
resultado por 12 horas. `AgendaCacheService.invalidar_agenda_dia(medico_id, data)`
remove o cache daquele médico e dia; qualquer fluxo que altere um agendamento
deve chamar esse método após persistir a alteração no MongoDB.
`seed_agendas.py` preaquece as agendas futuras já existentes no MongoDB.

## Finalização de Consultas

A tela lista as consultas com status `Em andamento` e permite que o médico
finalize cada uma anexando o laudo em PDF e, opcionalmente, a receita ou
orientações. Quando não há consultas, exibe "Sem consultas a serem finalizadas".

Ao finalizar uma consulta:

1. **MongoDB** (`prontuarios`): o laudo é adicionado em
   `consultas.exames_laudos` (`$push`), a receita é salva em
   `consultas.receita` e o status muda para `Concluído`.
2. **Redis**: a consulta é removida da fila de espera e o status na fila passa
   para `Concluído`.

Métodos usados (`ProntuarioService`):

- `obter_consultas_em_andamento()`: aggregate com `$unwind`, `$match` e
  `$lookup` para trazer as consultas em andamento com paciente e médico.
- `finalizar_consulta(paciente_id, consulta_id, arquivo, receita)`: grava o
  laudo e conclui a consulta. Retorna `False` se a consulta não estiver mais em
  andamento.

## Fila de espera no Redis

A fila da recepção virtual é compartilhada entre as telas de Recepção Virtual
e Finalização de Consultas através do `FilaService`
(`src/services/fila_service.py`).

| Chave | Tipo | Conteúdo |
|---|---|---|
| `fila:espera` | Sorted Set | membro = id da consulta, score = horário de chegada (timestamp) |
| `fila:consulta:<id>` | Hash | `paciente_id`, `paciente_nome`, `status`, `chegada` |

Status na fila: `Aguardando` → `Em andamento` → `Concluído`. Ao ser concluída, a consulta sai
do Sorted Set e o hash expira em 24 horas.

Métodos:

- `adicionar_na_fila(consulta_id, paciente_id, paciente_nome)`: coloca a
  consulta no fim da fila com status `Aguardando`.
- `obter_fila()`: retorna a fila em ordem de chegada.
- `remover_da_fila(consulta_id)`: tira a consulta da fila e marca como
  `Concluído`.

## Sessões de usuários no Redis

Após o login validado no MongoDB, o sistema cria um token aleatório e armazena
no Redis somente o hash desse token. A sessão usa a chave
`sessao:<hash_do_token>`, contém os dados mínimos do usuário e expira após 8
horas. Cada validação renova o TTL; o botão `Sair` revoga a sessão removendo a
chave do Redis.

Essa implementação está no `SessaoService`
(`src/services/sessao_service.py`), enquanto o token permanece apenas no
`st.session_state` da sessão ativa do Streamlit.
