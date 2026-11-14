"""
Seed da plataforma de telemedicina (PostgreSQL + MongoDB + Neo4j + Redis).

Apaga os dados existentes e recria tudo com os dados de exemplo:

1. PostgreSQL: tabela usuarios, com os dados de acesso de médicos e
   recepcionistas (nome, CPF, e-mail, senha, cargo e status).
2. MongoDB: coleções medicos, pacientes, prontuarios e agendamentos, com
   validação $jsonSchema e índices. A coleção medicos guarda os detalhes de
   cada médico (CRM, telefone, especialidades e data de cadastro) e o
   usuario_id, que é o id do médico na tabela usuarios. O medico_id dos
   prontuários e agendamentos também é esse id. Cada agendamento aponta para a
   consulta do prontuário pelo campo consulta_id.
3. Neo4j: grafo de histórico epidemiológico e interações clínicas, montado a
   partir do PostgreSQL e do MongoDB:

       (:Medico)-[:ATENDEU]->(:Paciente)
       (:Paciente)-[:DIAGNOSTICADO_COM]->(:Diagnostico)
       (:Paciente)-[:RECEBEU_PRESCRICAO]->(:Medicamento)

4. Redis: apaga fila, sessões e cache e carrega o cache das agendas futuras.

Uso, na raiz do projeto, com os containers no ar:
    python src/config/seed.py
"""

import re
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import redis
from bson import Binary, ObjectId
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
from psycopg.rows import dict_row
from pymongo import ASCENDING, IndexModel, MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from redis.exceptions import RedisError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repositories.mongo_repository import MongoRepository  # noqa: E402
from services.agenda_cache_service import (  # noqa: E402
    PREFIXO_AGENDA,
    STATUS_AGENDA_CACHE,
    AgendaCacheService,
)

ARQUIVO_SECRETS = Path(__file__).resolve().parents[2] / ".streamlit" / "secrets.toml"

FUSO_BRASILIA = timezone(timedelta(hours=-3), "BRT")

STATUS_CADASTRO = ["Ativo", "Inativo"]
STATUS_AGENDAMENTO = [
    "confirmado",
    "em_atendimento",
    "concluido",
    "cancelado",
    "nao_compareceu",
]
STATUS_CONSULTA = ["Agendado", "Em andamento", "Concluído"]

# --------------------------------------------------------------------------
# Tabela de usuários no PostgreSQL
# --------------------------------------------------------------------------

SQL_TABELA_USUARIOS = """
CREATE TABLE usuarios (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome TEXT NOT NULL,
    cpf VARCHAR(11) NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    senha TEXT NOT NULL,
    cargo TEXT NOT NULL CHECK (cargo IN ('Medico', 'Recepcionista')),
    status TEXT NOT NULL DEFAULT 'Ativo' CHECK (status IN ('Ativo', 'Inativo'))
)
"""

SQL_INSERIR_USUARIO = """
INSERT INTO usuarios (nome, cpf, email, senha, cargo, status)
VALUES (%(nome)s, %(cpf)s, %(email)s, %(senha)s, %(cargo)s, %(status)s)
RETURNING id
"""

# --------------------------------------------------------------------------
# Esquemas de validação do MongoDB
# --------------------------------------------------------------------------

TEXTO = {"bsonType": "string"}
DATA = {"bsonType": "date"}
INTEIRO = {"bsonType": ["int", "long"]}
ID = {"bsonType": "objectId"}
LISTA_DE_TEXTO = {"bsonType": "array", "items": TEXTO}
ID_USUARIO = {"bsonType": ["int", "long"], "minimum": 1}  # id na tabela usuarios


def lista_de(propriedades, obrigatorios=()):
    item = {"bsonType": "object", "properties": propriedades}
    if obrigatorios:
        item["required"] = list(obrigatorios)
    return {"bsonType": "array", "items": item}


def esquema(propriedades, obrigatorios=()):
    schema = {"bsonType": "object", "properties": propriedades}
    if obrigatorios:  # o MongoDB rejeita "required" vazio
        schema["required"] = list(obrigatorios)
    return {"$jsonSchema": schema}


VALIDADORES = {
    "medicos": esquema(
        {
            "usuario_id": ID_USUARIO,
            "crm": TEXTO,
            "uf_crm": {"bsonType": "string", "minLength": 2, "maxLength": 2},
            "telefone": INTEIRO,
            "especialidades": LISTA_DE_TEXTO,
            "data_cadastro": DATA,
        },
        obrigatorios=["usuario_id", "crm", "uf_crm", "telefone"],
    ),
    "pacientes": esquema(
        {
            "nome": TEXTO,
            "cpf": TEXTO,
            "data_nascimento": DATA,
            "sexo": TEXTO,
            "telefone": INTEIRO,
            "email": TEXTO,
            "endereco": TEXTO,
            "alergias": LISTA_DE_TEXTO,
            "status": {"enum": STATUS_CADASTRO},
            "data_cadastro": DATA,
        },
        obrigatorios=["nome", "cpf", "telefone", "email", "status"],
    ),
    "prontuarios": esquema(
        {
            "paciente_id": ID,
            "consultas": lista_de(
                {
                    "_id": ID,
                    "medico_id": ID_USUARIO,
                    "queixa_principal": TEXTO,
                    "sintomas": LISTA_DE_TEXTO,
                    "diagnosticos": LISTA_DE_TEXTO,
                    "observacoes": TEXTO,
                    "status": {"enum": STATUS_CONSULTA},
                    "exames_laudos": lista_de(
                        {
                            "nome_arquivo": TEXTO,
                            "tipo_arquivo": TEXTO,
                            "arquivo": {"bsonType": "binData"},
                            "tamanho_bytes": INTEIRO,
                            "data_upload": DATA,
                            "descricao": TEXTO,
                        }
                    ),
                },
                obrigatorios=["_id", "medico_id", "status"],
            ),
            "prescricoes": lista_de(
                {
                    "nome": TEXTO,
                    "dosagem": TEXTO,
                    "frequencia": TEXTO,
                    "orientacoes": TEXTO,
                }
            ),
            "data_atualizacao": DATA,
        },
        obrigatorios=["paciente_id"],
    ),
    "agendamentos": {
        "$jsonSchema": {
            "bsonType": "object",
            "properties": {
                "paciente_id": ID,
                "medico_id": ID_USUARIO,
                "consulta_id": ID,
                "horario_inicio": DATA,
                "horario_fim": DATA,
                "status": {"enum": STATUS_AGENDAMENTO},
            },
            "required": ["paciente_id", "medico_id", "horario_inicio", "horario_fim"],
            # Agendamento que ainda vai acontecer precisa apontar para a
            # consulta do prontuário. O histórico antigo pode não ter consulta.
            "anyOf": [
                {
                    "properties": {
                        "status": {"enum": ["concluido", "cancelado", "nao_compareceu"]}
                    }
                },
                {"required": ["consulta_id"]},
            ],
        }
    },
}

INDICES = {
    "medicos": [
        # Um documento por médico: o usuario_id é a ligação com o PostgreSQL.
        IndexModel([("usuario_id", ASCENDING)], unique=True),
        IndexModel([("crm", ASCENDING), ("uf_crm", ASCENDING)], unique=True),
    ],
    "pacientes": [IndexModel([("cpf", ASCENDING)], unique=True)],
    "prontuarios": [
        IndexModel([("paciente_id", ASCENDING)], unique=True),
        IndexModel([("consultas.medico_id", ASCENDING)]),
        IndexModel([("consultas._id", ASCENDING)]),
    ],
    "agendamentos": [
        IndexModel([("medico_id", ASCENDING), ("horario_inicio", ASCENDING)]),
        IndexModel([("paciente_id", ASCENDING), ("horario_inicio", ASCENDING)]),
        IndexModel([("consulta_id", ASCENDING)]),
    ],
}

# --------------------------------------------------------------------------
# Dados de exemplo
# --------------------------------------------------------------------------


def dia(ano, mes, dia_do_mes):
    """Data sem horário (o MongoDB guarda como meia-noite UTC)."""
    return datetime(ano, mes, dia_do_mes, tzinfo=timezone.utc)


def horario(ano, mes, dia_do_mes, hora, minuto=0):
    """Horário local de Brasília (o MongoDB converte para UTC)."""
    return datetime(ano, mes, dia_do_mes, hora, minuto, tzinfo=FUSO_BRASILIA)


# Cada médico tem os dados de acesso (tabela usuarios) e os detalhes (coleção
# medicos).
MEDICOS = {
    "joao": {
        "usuario": {
            "nome": "Dr. João Pedro Almeida",
            "cpf": "82634512070",
            "email": "joao.almeida@example.com",
            "senha": "senha123",
            "cargo": "Medico",
            "status": "Ativo",
        },
        "detalhes": {
            "crm": "123456",
            "uf_crm": "SP",
            "telefone": 5511976543210,
            "especialidades": ["Clínica Médica", "Neurologia"],
            "data_cadastro": dia(2025, 10, 1),
        },
    },
    "fernanda": {
        "usuario": {
            "nome": "Dra. Fernanda Ribeiro",
            "cpf": "46172839005",
            "email": "fernanda.ribeiro@example.com",
            "senha": "senha123",
            "cargo": "Medico",
            "status": "Ativo",
        },
        "detalhes": {
            "crm": "654321",
            "uf_crm": "RJ",
            "telefone": 5521987001122,
            "especialidades": ["Cardiologia"],
            "data_cadastro": dia(2025, 12, 10),
        },
    },
}

RECEPCIONISTAS = [
    {
        "nome": "Beatriz Martins",
        "cpf": "28749613004",
        "email": "beatriz.martins@example.com",
        "senha": "senha123",
        "cargo": "Recepcionista",
        "status": "Ativo",
    }
]

PACIENTES = {
    "ana": {
        "nome": "Ana Beatriz Souza",
        "cpf": "39053344705",
        "data_nascimento": dia(1990, 4, 12),
        "sexo": "Feminino",
        "telefone": 5511987654321,
        "email": "ana.souza@example.com",
        "endereco": "Rua das Flores, 120 - Pinheiros, São Paulo/SP",
        "alergias": ["Dipirona", "Látex"],
        "status": "Ativo",
        "data_cadastro": dia(2026, 1, 15),
    },
    "carlos": {
        "nome": "Carlos Eduardo Lima",
        "cpf": "71428793860",
        "data_nascimento": dia(1978, 9, 3),
        "sexo": "Masculino",
        "telefone": 5521998761234,
        "email": "carlos.lima@example.com",
        "endereco": "Av. Atlântica, 500 - Copacabana, Rio de Janeiro/RJ",
        "alergias": [],
        "status": "Ativo",
        "data_cadastro": dia(2026, 3, 2),
    },
    "mariana": {
        "nome": "Mariana Costa Ferreira",
        "cpf": "15350946056",
        "data_nascimento": dia(2001, 12, 25),
        "sexo": "Feminino",
        "telefone": 5531991112233,
        "email": "mariana.ferreira@example.com",
        "endereco": "Rua da Bahia, 900 - Centro, Belo Horizonte/MG",
        "alergias": ["Penicilina"],
        "status": "Inativo",
        "data_cadastro": dia(2025, 11, 20),
    },
}


def laudo_texto(conteudo):
    return Binary(conteudo.encode("utf-8"))


def montar_prontuarios(pacientes, medicos, consultas):
    agora = datetime.now(timezone.utc)

    return [
        {
            "paciente_id": pacientes["ana"],
            "consultas": [
                {
                    "_id": consultas["ana"],
                    "medico_id": medicos["joao"],
                    "queixa_principal": "Dor de cabeça recorrente há duas semanas",
                    "sintomas": ["Cefaleia pulsátil", "Fotofobia", "Náusea"],
                    "diagnosticos": ["Enxaqueca sem aura (CID G43.0)"],
                    "observacoes": "Paciente alérgica a dipirona. Solicitado hemograma.",
                    "status": "Agendado",
                    "exames_laudos": [
                        {
                            "nome_arquivo": "hemograma_ana.txt",
                            "tipo_arquivo": "text/plain",
                            "arquivo": laudo_texto(
                                "HEMOGRAMA COMPLETO\n"
                                "Hemoglobina: 13,5 g/dL (ref. 12,0-15,5)\n"
                                "Leucócitos: 6.800/mm3 (ref. 4.000-11.000)\n"
                                "Plaquetas: 250.000/mm3 (ref. 150.000-450.000)\n"
                                "Conclusão: sem alterações.\n"
                            ),
                            "descricao": "Hemograma completo sem alterações",
                        }
                    ],
                }
            ],
            "prescricoes": [
                {
                    "nome": "Paracetamol",
                    "dosagem": "750 mg",
                    "frequencia": "De 8 em 8 horas, se dor",
                    "orientacoes": "Não exceder 4 comprimidos por dia.",
                }
            ],
            "data_atualizacao": agora,
        },
        {
            "paciente_id": pacientes["carlos"],
            "consultas": [
                {
                    "_id": consultas["carlos_1"],
                    "medico_id": medicos["fernanda"],
                    "queixa_principal": "Palpitações e cansaço aos esforços",
                    "sintomas": ["Palpitação", "Dispneia aos esforços"],
                    "diagnosticos": ["Hipertensão arterial sistêmica (CID I10)"],
                    "observacoes": "PA 150x95 mmHg. Solicitado eletrocardiograma.",
                    "status": "Agendado",
                    "exames_laudos": [
                        {
                            "nome_arquivo": "ecg_carlos.txt",
                            "tipo_arquivo": "text/plain",
                            "arquivo": laudo_texto(
                                "ELETROCARDIOGRAMA EM REPOUSO\n"
                                "Ritmo sinusal, FC 72 bpm.\n"
                                "Eixo elétrico normal. Sem alterações de repolarização.\n"
                                "Conclusão: ECG dentro da normalidade.\n"
                            ),
                            "descricao": "Eletrocardiograma em repouso normal",
                        }
                    ],
                },
                {
                    "_id": consultas["carlos_2"],
                    "medico_id": medicos["fernanda"],
                    "queixa_principal": "Retorno para avaliação do eletrocardiograma",
                    "sintomas": [],
                    "diagnosticos": ["Hipertensão arterial sistêmica (CID I10)"],
                    "observacoes": "ECG normal. PA controlada com a medicação.",
                    "status": "Agendado",
                    "exames_laudos": [],
                },
            ],
            "prescricoes": [
                {
                    "nome": "Losartana potássica",
                    "dosagem": "50 mg",
                    "frequencia": "1 vez ao dia, pela manhã",
                    "orientacoes": "Uso contínuo. Reduzir o consumo de sal.",
                }
            ],
            "data_atualizacao": agora,
        },
    ]


def montar_agendamentos(pacientes, medicos, consultas):
    def agendamento(paciente, medico, inicio, status, consulta=None, duracao_min=30):
        documento = {
            "paciente_id": pacientes[paciente],
            "medico_id": medicos[medico],
            "horario_inicio": inicio,
            "horario_fim": inicio + timedelta(minutes=duracao_min),
            "status": status,
        }
        if consulta is not None:
            documento["consulta_id"] = consultas[consulta]
        return documento

    return [
        # Histórico anterior às consultas do prontuário: sem consulta associada.
        agendamento("ana", "joao", horario(2026, 10, 20, 9), "concluido"),
        agendamento("carlos", "fernanda", horario(2026, 10, 22, 14), "concluido"),
        agendamento("carlos", "fernanda", horario(2026, 11, 5, 14), "concluido"),
        agendamento("mariana", "joao", horario(2026, 11, 3, 10), "nao_compareceu"),
        # A consulta da Ana foi remarcada de 10/11 para 17/11.
        agendamento("ana", "joao", horario(2026, 11, 10, 9), "cancelado", "ana"),
        agendamento("ana", "joao", horario(2026, 11, 17, 9), "confirmado", "ana"),
        agendamento(
            "carlos", "fernanda", horario(2026, 11, 19, 14), "confirmado", "carlos_1"
        ),
        agendamento(
            "carlos", "fernanda", horario(2026, 12, 3, 15), "confirmado", "carlos_2"
        ),
    ]


# --------------------------------------------------------------------------
# Grafo no Neo4j
# --------------------------------------------------------------------------

ROTULOS = ["Paciente", "Medico", "Diagnostico", "Medicamento"]
RELACIONAMENTOS = ["ATENDEU", "DIAGNOSTICADO_COM", "RECEBEU_PRESCRICAO"]

RESTRICOES_NEO4J = [
    "CREATE CONSTRAINT paciente_mongo_id IF NOT EXISTS "
    "FOR (n:Paciente) REQUIRE n.mongo_id IS UNIQUE",
    "CREATE CONSTRAINT medico_postgres_id IF NOT EXISTS "
    "FOR (n:Medico) REQUIRE n.postgres_id IS UNIQUE",
    "CREATE CONSTRAINT diagnostico_codigo IF NOT EXISTS "
    "FOR (n:Diagnostico) REQUIRE n.codigo IS UNIQUE",
    "CREATE CONSTRAINT medicamento_nome IF NOT EXISTS "
    "FOR (n:Medicamento) REQUIRE n.nome IS UNIQUE",
]

CYPHER_LIMPAR_GRAFO = "MATCH (n) DETACH DELETE n"

CYPHER_PACIENTES = """
UNWIND $linhas AS linha
CREATE (p:Paciente {mongo_id: linha.mongo_id})
SET p += linha.dados
"""

CYPHER_MEDICOS = """
UNWIND $linhas AS linha
CREATE (m:Medico {postgres_id: linha.postgres_id})
SET m += linha.dados
"""

CYPHER_ATENDIMENTOS = """
UNWIND $linhas AS linha
MATCH (p:Paciente {mongo_id: linha.paciente_id})
MATCH (m:Medico {postgres_id: linha.medico_id})
CREATE (m)-[:ATENDEU {
    consulta_id: linha.consulta_id,
    queixa_principal: linha.queixa_principal,
    sintomas: linha.sintomas
}]->(p)
"""

CYPHER_DIAGNOSTICOS = """
UNWIND $linhas AS linha
MATCH (p:Paciente {mongo_id: linha.paciente_id})
MERGE (d:Diagnostico {codigo: linha.codigo})
SET d.nome = linha.nome, d.cid = linha.cid
CREATE (p)-[:DIAGNOSTICADO_COM {
    consulta_id: linha.consulta_id,
    medico_id: linha.medico_id
}]->(d)
"""

CYPHER_PRESCRICOES = """
UNWIND $linhas AS linha
MATCH (p:Paciente {mongo_id: linha.paciente_id})
MERGE (m:Medicamento {nome: linha.nome})
CREATE (p)-[:RECEBEU_PRESCRICAO {
    dosagem: linha.dosagem,
    frequencia: linha.frequencia,
    orientacoes: linha.orientacoes
}]->(m)
"""

CID_NO_FINAL = re.compile(
    r"\(\s*CID[\s:-]*(?P<cid>[A-Z]\d{2}(?:\.\d{1,2})?)\s*\)$", re.IGNORECASE
)


def separar_cid(diagnostico):
    """'Enxaqueca sem aura (CID G43.0)' -> ('Enxaqueca sem aura', 'G43.0')."""
    texto = diagnostico.strip()
    encontrado = CID_NO_FINAL.search(texto)
    if encontrado:
        return texto[: encontrado.start()].strip(), encontrado["cid"].upper()
    return texto, None


def ler_pacientes(db):
    linhas = []
    for p in db.pacientes.find():
        nascimento = p.get("data_nascimento")
        linhas.append(
            {
                "mongo_id": str(p["_id"]),
                "dados": {
                    "nome": p["nome"],
                    "sexo": p.get("sexo"),
                    "data_nascimento": nascimento.date() if nascimento else None,
                    "alergias": p.get("alergias", []),
                    "status": p["status"],
                },
            }
        )
    return linhas


def ler_medicos(db, conexao):
    """Junta o nome e o status do PostgreSQL aos detalhes da coleção medicos."""
    detalhes = {d["usuario_id"]: d for d in db.medicos.find()}
    with conexao.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id, nome, status FROM usuarios WHERE cargo = 'Medico'")
        return [
            {
                "postgres_id": m["id"],
                "dados": {
                    "nome": m["nome"],
                    "status": m["status"],
                    "crm": detalhes[m["id"]]["crm"],
                    "uf_crm": detalhes[m["id"]]["uf_crm"],
                    "especialidades": detalhes[m["id"]].get("especialidades", []),
                },
            }
            for m in cursor
        ]


def ler_historico_clinico(db):
    atendimentos, diagnosticos, prescricoes = [], [], []
    for prontuario in db.prontuarios.find():
        paciente_id = str(prontuario["paciente_id"])

        for consulta in prontuario.get("consultas", []):
            consulta_id = str(consulta["_id"])
            medico_id = consulta["medico_id"]
            atendimentos.append(
                {
                    "paciente_id": paciente_id,
                    "medico_id": medico_id,
                    "consulta_id": consulta_id,
                    "queixa_principal": consulta.get("queixa_principal"),
                    "sintomas": consulta.get("sintomas", []),
                }
            )
            for diagnostico in consulta.get("diagnosticos", []):
                nome, cid = separar_cid(diagnostico)
                diagnosticos.append(
                    {
                        "paciente_id": paciente_id,
                        "consulta_id": consulta_id,
                        "medico_id": medico_id,
                        "codigo": cid or nome,
                        "nome": nome,
                        "cid": cid,
                    }
                )

        for prescricao in prontuario.get("prescricoes", []):
            prescricoes.append(
                {
                    "paciente_id": paciente_id,
                    "nome": prescricao["nome"].strip(),
                    "dosagem": prescricao.get("dosagem"),
                    "frequencia": prescricao.get("frequencia"),
                    "orientacoes": prescricao.get("orientacoes"),
                }
            )
    return atendimentos, diagnosticos, prescricoes


def montar_grafo(db, conexao, driver, banco):
    """Apaga o grafo e o recria a partir do PostgreSQL e do MongoDB."""
    driver.execute_query(CYPHER_LIMPAR_GRAFO, database_=banco)
    for restricao in RESTRICOES_NEO4J:
        driver.execute_query(restricao, database_=banco)

    pacientes = ler_pacientes(db)
    medicos = ler_medicos(db, conexao)
    atendimentos, diagnosticos, prescricoes = ler_historico_clinico(db)

    def gravar(tx):
        tx.run(CYPHER_PACIENTES, linhas=pacientes)
        tx.run(CYPHER_MEDICOS, linhas=medicos)
        tx.run(CYPHER_ATENDIMENTOS, linhas=atendimentos)
        tx.run(CYPHER_DIAGNOSTICOS, linhas=diagnosticos)
        tx.run(CYPHER_PRESCRICOES, linhas=prescricoes)

    with driver.session(database=banco) as sessao:
        sessao.execute_write(gravar)


# --------------------------------------------------------------------------
# Conexões
# --------------------------------------------------------------------------


def carregar_secrets():
    # utf-8-sig: o secrets.toml pode ter sido salvo com BOM.
    return tomllib.loads(ARQUIVO_SECRETS.read_text(encoding="utf-8-sig"))


def conectar_postgres(config):
    try:
        return psycopg.connect(config["uri"], connect_timeout=5)
    except psycopg.OperationalError:
        raise SystemExit(
            "Não foi possível conectar ao PostgreSQL. "
            "Suba o container com: docker compose up -d postgres"
        )


def conectar_mongodb(config):
    cliente = MongoClient(config["uri"], serverSelectionTimeoutMS=5000, tz_aware=True)
    try:
        cliente.admin.command("ping")
    except ServerSelectionTimeoutError:
        cliente.close()
        raise SystemExit(
            "Não foi possível conectar ao MongoDB. "
            "Suba o container com: docker compose up -d mongodb"
        )
    return cliente


def conectar_neo4j(config):
    driver = GraphDatabase.driver(
        config["uri"], auth=(config["username"], config["password"])
    )
    try:
        driver.verify_connectivity()
    except ServiceUnavailable:
        driver.close()
        raise SystemExit(
            "Não foi possível conectar ao Neo4j. "
            "Suba o container com: docker compose up -d neo4j"
        )
    return driver


def conectar_redis(config):
    cliente = redis.Redis.from_url(
        config["url"], decode_responses=True, socket_connect_timeout=5
    )
    try:
        cliente.ping()
    except RedisError:
        cliente.close()
        raise SystemExit(
            "Não foi possível conectar ao Redis. "
            "Suba o container com: docker compose up -d redis"
        )
    return cliente


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------


def criar_tabela_usuarios(conexao):
    conexao.execute("DROP TABLE IF EXISTS usuarios")
    conexao.execute(SQL_TABELA_USUARIOS)


def criar_colecoes(db):
    for nome, validador in VALIDADORES.items():
        db.create_collection(nome, validator=validador)
        db[nome].create_indexes(INDICES[nome])


def semear(conexao, db):
    medicos = {
        chave: conexao.execute(SQL_INSERIR_USUARIO, medico["usuario"]).fetchone()[0]
        for chave, medico in MEDICOS.items()
    }
    for recepcionista in RECEPCIONISTAS:
        conexao.execute(SQL_INSERIR_USUARIO, recepcionista)
    # Os ids só vão para o MongoDB depois de gravados no PostgreSQL.
    conexao.commit()

    db.medicos.insert_many(
        [
            {"usuario_id": medicos[chave], **medico["detalhes"]}
            for chave, medico in MEDICOS.items()
        ]
    )
    pacientes = {
        chave: db.pacientes.insert_one(paciente).inserted_id
        for chave, paciente in PACIENTES.items()
    }
    consultas = {chave: ObjectId() for chave in ("ana", "carlos_1", "carlos_2")}
    db.prontuarios.insert_many(montar_prontuarios(pacientes, medicos, consultas))
    db.agendamentos.insert_many(montar_agendamentos(pacientes, medicos, consultas))


def carregar_cache_agendas(db, redis_client):
    """Guarda no Redis as agendas dos dias que ainda têm consultas."""
    cache = AgendaCacheService(redis_client, MongoRepository(db))
    agendamentos = db.agendamentos.find(
        {
            "status": {"$in": STATUS_AGENDA_CACHE},
            "horario_inicio": {"$gte": datetime.now(FUSO_BRASILIA)},
        },
        {"medico_id": 1, "horario_inicio": 1},
    )
    agendas = {
        (item["medico_id"], item["horario_inicio"].astimezone(FUSO_BRASILIA).date())
        for item in agendamentos
    }
    for medico_id, dia_agenda in sorted(agendas):
        cache.obter_agenda_dia(medico_id, dia_agenda)


def imprimir_resumo(conexao, db, driver, banco_neo4j, redis_client):
    usuarios = conexao.execute("SELECT count(*) FROM usuarios").fetchone()[0]
    print(f"PostgreSQL ({conexao.info.dbname}):")
    print(f"  usuarios: {usuarios} registro(s)")

    print(f"MongoDB ({db.name}):")
    for nome in VALIDADORES:
        print(f"  {nome}: {db[nome].count_documents({})} documento(s)")

    print(f"Neo4j ({banco_neo4j}):")
    for rotulo in ROTULOS:
        registros, _, _ = driver.execute_query(
            f"MATCH (n:{rotulo}) RETURN count(n) AS total", database_=banco_neo4j
        )
        print(f"  (:{rotulo}): {registros[0]['total']} nó(s)")
    for tipo in RELACIONAMENTOS:
        registros, _, _ = driver.execute_query(
            f"MATCH ()-[r:{tipo}]->() RETURN count(r) AS total", database_=banco_neo4j
        )
        print(f"  [:{tipo}]: {registros[0]['total']} relação(ões)")

    agendas = sum(1 for _ in redis_client.scan_iter(match=f"{PREFIXO_AGENDA}*"))
    print("Redis:")
    print(f"  agendas em cache: {agendas}")


def main():
    secrets = carregar_secrets()
    banco_mongo = secrets["mongodb"]["database"]
    banco_neo4j = secrets["neo4j"].get("database", "neo4j")

    # Conecta nos quatro bancos antes de apagar qualquer coisa.
    with (
        conectar_postgres(secrets["postgres"]) as conexao,
        conectar_mongodb(secrets["mongodb"]) as cliente,
        conectar_neo4j(secrets["neo4j"]) as driver,
        conectar_redis(secrets["redis"]) as redis_client,
    ):
        criar_tabela_usuarios(conexao)

        cliente.drop_database(banco_mongo)
        db = cliente[banco_mongo]
        criar_colecoes(db)

        semear(conexao, db)
        montar_grafo(db, conexao, driver, banco_neo4j)

        redis_client.flushdb()
        carregar_cache_agendas(db, redis_client)

        imprimir_resumo(conexao, db, driver, banco_neo4j, redis_client)


if __name__ == "__main__":
    main()
