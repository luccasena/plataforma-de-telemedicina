# Plataforma de Telemedicina

## Infraestrutura local

Para iniciar MongoDB, Redis e Neo4j:

```bash
docker compose up -d
```

## Gerar dados iniciais

Com os containers em execução, gere as coleções, os validadores e os dados de
exemplo no MongoDB e no Neo4j:

```bash
python src/config/seed.py
```


Serviços disponíveis:

- MongoDB: `localhost:27018`
- Redis: `localhost:6379`
- Neo4j Browser: `http://localhost:7474`
- Neo4j Bolt: `bolt://localhost:7687`

As credenciais podem ser sobrescritas com as variáveis `MONGO_ROOT_USERNAME`,
`MONGO_ROOT_PASSWORD`, `REDIS_PASSWORD` e `NEO4J_AUTH`.

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

Status na fila: `Aguardando` → `Concluído`. Ao ser concluída, a consulta sai
do Sorted Set e o hash expira em 24 horas.

Métodos:

- `adicionar_na_fila(consulta_id, paciente_id, paciente_nome)`: coloca a
  consulta no fim da fila com status `Aguardando`.
- `obter_fila()`: retorna a fila em ordem de chegada.
- `remover_da_fila(consulta_id)`: tira a consulta da fila e marca como
  `Concluído`.
