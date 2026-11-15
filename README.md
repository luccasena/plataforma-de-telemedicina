# Plataforma de Telemedicina

## Infraestrutura local

Para iniciar PostgreSQL, MongoDB, Redis e Neo4j:

```bash
docker compose up -d
```

## Gerar dados iniciais

Com os containers em execução, gere a tabela de usuários no PostgreSQL, as
coleções (incluindo a de detalhes dos médicos), os validadores e os dados de
exemplo no MongoDB e o grafo no Neo4j. O mesmo script também carrega no Redis
o cache das agendas futuras:

```bash
python src/config/seed.py
```

O script apaga os dados existentes e recria tudo a cada execução. Como ele
também zera o Redis (fila, sessões e cache), é preciso entrar de novo no app.

Se você tem os bancos de uma versão anterior do projeto, apague os volumes e
suba os containers de novo antes de rodar o script:

```bash
docker compose down -v
docker compose up -d
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
$env:PYTHONPATH = "src"
streamlit run src/app.py
```

Para executar os testes, em outro terminal com o mesmo
ambiente virtual ativado:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s src/tests -v
```

Serviços disponíveis:

- PostgreSQL: `localhost:5433` (a porta 5432 fica livre para um PostgreSQL
  instalado na máquina)
- MongoDB: `localhost:27018`
- Redis: `localhost:6379`
- Neo4j Browser: `http://localhost:7474`
- Neo4j Bolt: `bolt://localhost:7687`

As credenciais podem ser sobrescritas com as variáveis `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`, `MONGO_ROOT_USER`, `MONGO_ROOT_PASSWORD`,
`REDIS_PASSWORD` e `NEO4J_AUTH`.

## Usuários no PostgreSQL e detalhes dos médicos no MongoDB

A tabela `usuarios` do PostgreSQL, criada pelo `seed.py`, guarda só os dados
usados no acesso à aplicação, de médicos e recepcionistas: `nome`, `cpf`,
`email`, `senha`, `cargo` e `status`. CPF e e-mail são únicos. O `id` (`BIGINT`
gerado pelo banco) é a referência usada pelos outros bancos:

- MongoDB: `usuario_id` na coleção `medicos` e `medico_id` em
  `prontuarios.consultas` e em `agendamentos`;
- Redis: `usuario_id` da sessão e a chave `agenda:medico:<id>:<AAAA-MM-DD>`;
- Neo4j: propriedade `postgres_id` dos nós `(:Medico)`, usada pela tela
  **Mapeamento Clínico** para identificar os médicos.

Os detalhes de cada médico ficam na coleção `medicos` do MongoDB:

| Campo | Tipo | Obrigatório |
|---|---|---|
| `usuario_id` | inteiro: o `id` do médico na tabela `usuarios` | sim |
| `crm` | texto | sim |
| `uf_crm` | texto com 2 letras | sim |
| `telefone` | inteiro | sim |
| `especialidades` | lista de textos | não |
| `data_cadastro` | data | não |

O validador da coleção recusa documentos sem `usuario_id` (ou com valor que não
seja inteiro positivo). O índice único em `usuario_id` garante um documento por
médico, e o índice único em (`crm`, `uf_crm`) impede CRM repetido na mesma UF.

O `UsuarioService` faz o login e busca os nomes dos médicos exibidos nas
consultas no PostgreSQL, já que o MongoDB não consegue fazer `$lookup` em uma
tabela do PostgreSQL. Para listar os médicos, junta os da tabela com o CRM da
coleção `medicos`. O `criar_medico` grava o usuário e os detalhes na mesma
transação do PostgreSQL: se a gravação no MongoDB falhar, o usuário não é
criado.

Quem já tinha o `.streamlit/secrets.toml` precisa acrescentar a ele a seção
`[postgres]` do `.streamlit/secrets-example.toml`.

## Recepção Virtual: fila e agenda

A tela **Recepção Virtual** apresenta a fila ordenada por chegada, posição e
tempo de espera. A recepção só pode adicionar à fila consultas com status
`Agendado` no prontuário; **Chamar próximo paciente** retira o primeiro da fila,
muda a consulta para `Em andamento`, cria o token de sessão da consulta e a
exibe em **Consultas em atendimento**, com o token e o tempo até expirar. A
tela lista também as sessões Redis ainda válidas e atualiza os dados
automaticamente a cada 15 segundos (ou pelo botão **Atualizar agora**).

Cada documento de `agendamentos` aponta para a consulta do prontuário pelo
campo `consulta_id` (obrigatório nos agendamentos `confirmado` e
`em_atendimento`). A **Agenda do dia** lista, para o médico e a data
selecionados, as consultas referenciadas pelos agendamentos confirmados/em
atendimento, com o intervalo `horario_inicio` - `horario_fim`, o paciente, a
queixa principal e o status atual da consulta.

Ao criar uma consulta na tela **Atendimento Médico**, o médico informa a data e
o horário. O `ProntuarioService.criar_consulta` grava, além da consulta no
prontuário, um agendamento `confirmado` de 30 minutos apontando para ela, e a
tela descarta o cache daquele médico e dia. Assim, a consulta aparece na
**Agenda do dia**.

A agenda usa cache-aside: consulta primeiro
`agenda:medico:<medico_id>:<AAAA-MM-DD>` no Redis e, em caso de ausência,
monta a agenda no MongoDB e guarda o resultado por 12 horas. Como o cache
guarda o status da consulta, ele precisa ser descartado sempre que um
agendamento ou o status de uma consulta mudar:

- `AgendaCacheService.invalidar_agenda_dia(medico_id, data)`: remove o cache
  daquele médico e dia. É chamado ao criar uma consulta.
- `AgendaCacheService.invalidar_agendas_da_consulta(consulta_id)`: remove o
  cache de todos os dias em que a consulta está agendada. É chamado ao chamar o
  próximo paciente e ao finalizar a consulta.

O `seed.py` zera o Redis e carrega o cache das agendas futuras.

## Finalização de Consultas

A tela lista as consultas com status `Em andamento` e permite que o médico
finalize cada uma anexando o laudo em PDF e, opcionalmente, as prescrições (uma
linha por medicamento, com dosagem, frequência e orientações) e a receita ou
orientações gerais. Quando não há consultas, exibe "Sem consultas a serem
finalizadas".

Ao finalizar uma consulta:

1. **MongoDB** (`prontuarios`): o laudo é adicionado em
   `consultas.exames_laudos` (`$push`), as prescrições são acrescentadas em
   `prescricoes` com o `consulta_id`, a receita é salva em `consultas.receita`
   e o status muda para `Concluído`.
2. **Neo4j**: cada prescrição vira
   `(:Paciente)-[:RECEBEU_PRESCRICAO]->(:Medicamento)`, que os alertas de
   segurança passam a verificar.
3. **Redis**: a consulta é removida da fila de espera, o status na fila passa
   para `Concluído` e o token de sessão da consulta é revogado.

O histórico da tela **Atendimento Médico** mostra a receita de cada consulta e
a lista de prescrições do paciente.

Métodos usados (`ProntuarioService`):

- `obter_consultas_em_andamento()`: aggregate com `$unwind`, `$match` e
  `$lookup` para trazer as consultas em andamento com o paciente; o nome do
  médico vem da tabela `usuarios` do PostgreSQL.
- `finalizar_consulta(paciente_id, consulta_id, arquivo, receita, prescricoes)`:
  grava o laudo e as prescrições e conclui a consulta. Retorna `False` se a
  consulta não estiver mais em andamento.

## Mapeamento Clínico e alertas de segurança (Neo4j)

A tela lê o grafo que o `seed.py` monta a partir do PostgreSQL e do MongoDB:

- `(:Medico)-[:ATENDEU]->(:Paciente)`
- `(:Paciente)-[:DIAGNOSTICADO_COM]->(:Diagnostico)`
- `(:Paciente)-[:RECEBEU_PRESCRICAO]->(:Medicamento)`

O `seed.py` grava também um catálogo clínico (`INTERACOES_MEDICAMENTOSAS` e
`CONTRAINDICACOES`), que liga os medicamentos pelo nome usado nas prescrições e
os diagnósticos pelo CID:

- `(:Medicamento)-[:INTERAGE_COM {severidade, descricao}]->(:Medicamento)`
- `(:Medicamento)-[:CONTRAINDICADO_PARA {severidade, motivo}]->(:Diagnostico)`

Para o paciente selecionado, a tela mostra:

- **Alertas de segurança**: alergia a um medicamento prescrito, interação entre
  medicamentos prescritos, medicamento contraindicado para um diagnóstico do
  paciente e prescrição duplicada.
- **Diagnósticos correlacionados em cadeia**: dois diagnósticos se
  correlacionam quando algum paciente tem os dois. A partir dos diagnósticos do
  paciente, a busca segue essas ligações com `shortestPath` por até 3 níveis e
  mostra o nível e a cadeia, por exemplo
  `Enxaqueca sem aura → Asma → Rinite alérgica`.
- **Grafo de relações clínicas** do paciente.

No fim da tela, a **Teia de Diagnósticos da Clínica** mostra o grafo de todos
os pacientes.

Com os dados de exemplo:

| Paciente | Diagnósticos | Alertas |
|---|---|---|
| Ana | Enxaqueca, asma | Propranolol contraindicado para asma; interação entre propranolol e salbutamol |
| Carlos | Hipertensão, enxaqueca | Interação entre losartana e ibuprofeno |
| Mariana | Asma, rinite alérgica | Nenhum |

### Atualização do grafo pela aplicação

O `ProntuarioService` grava no MongoDB e, em seguida, atualiza o grafo pelo
`MapeamentoClinicoService`. Ao criar uma consulta (`registrar_consulta`):

1. cria ou atualiza os nós `(:Paciente)` (dados da coleção `pacientes`) e
   `(:Medico)` (id e nome da tabela `usuarios`);
2. cria `(:Medico)-[:ATENDEU {consulta_id, queixa_principal, sintomas}]->(:Paciente)`;
3. para cada diagnóstico, cria o nó `(:Diagnostico)` se ainda não existir e
   `(:Paciente)-[:DIAGNOSTICADO_COM {consulta_id, medico_id}]->(:Diagnostico)`.

O diagnóstico é identificado pelo CID escrito no fim do texto, como em
`Asma (CID J45.9)`; sem CID, o próprio nome vira o código. A mesma regra
(`separar_cid`) é usada pelo `seed.py`. Os métodos de escrita do
`Neo4jRepository` usam `MERGE`, então os nós que já existem são reaproveitados.

Ao editar os diagnósticos de uma consulta no Atendimento
(`atualizar_diagnosticos`), os relacionamentos `DIAGNOSTICADO_COM` daquela
consulta são removidos e criados de novo com a lista editada.

Ao finalizar uma consulta com prescrições (`registrar_prescricoes`), cada
medicamento vira `(:Paciente)-[:RECEBEU_PRESCRICAO {consulta_id, dosagem,
frequencia, orientacoes}]->(:Medicamento)`. O nome é padronizado com a primeira
letra maiúscula (`ibuprofeno` → `Ibuprofeno`), como no catálogo clínico, para
que as interações e contraindicações sejam encontradas.

Se o Neo4j falhar, os dados continuam gravados no MongoDB e a tela avisa que
não foi possível atualizar o Mapeamento Clínico.

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

Após o login validado no PostgreSQL, o sistema cria um token aleatório e armazena
no Redis somente o hash desse token. A sessão usa a chave
`sessao:<hash_do_token>`, contém os dados mínimos do usuário e expira após 8
horas. Cada validação renova o TTL; o botão `Sair` revoga a sessão removendo a
chave do Redis.

Essa implementação está no `SessaoService`
(`src/services/sessao_service.py`), enquanto o token permanece apenas no
`st.session_state` da sessão ativa do Streamlit.

### Sessões das consultas ativas

Quando a recepção chama o próximo paciente e a consulta passa para
`Em andamento`, o `SessaoService.criar_sessao_consulta` cria um token aleatório
para a sala da teleconsulta e o guarda em `sessao_consulta:<consulta_id>`, com
expiração de 2 horas. A chave fica fora do prefixo `sessao:`, que lista apenas
as sessões de usuários. Diferente da sessão de usuário, o token fica guardado
no Redis para que a recepção possa repassá-lo ao paciente; ele aparece em
**Consultas em atendimento** e é revogado
(`SessaoService.revogar_sessao_consulta`) ao finalizar a consulta.
