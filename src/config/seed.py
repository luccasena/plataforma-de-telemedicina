"""
Seed da plataforma de telemedicina (MongoDB + Neo4j).

1. MongoDB: cria (ou atualiza) as coleções pacientes, usuarios, prontuarios e
   agendamentos com validação $jsonSchema, cria os índices e insere dados de
   exemplo. A coleção usuarios reúne médicos e recepcionistas.
2. Neo4j: lê o que está no MongoDB (a fonte da verdade) e monta o grafo de
   histórico epidemiológico e interações clínicas:

       (:Medico)-[:ATENDEU]->(:Paciente)
       (:Paciente)-[:DIAGNOSTICADO_COM]->(:Diagnostico)
       (:Paciente)-[:RECEBEU_PRESCRICAO]->(:Medicamento)

Pode ser executado várias vezes sem duplicar nada: no MongoDB cada documento
é substituído pela sua chave natural (CPF, CRM, paciente, horário) e no Neo4j
o histórico de cada paciente é reconstruído a partir do prontuário.

Uso, na raiz do projeto, com os containers do MongoDB e do Neo4j no ar:
    python src/config/seed.py
    python src/config/seed.py --reset    # apaga coleções e grafo antes de recriar
"""

import argparse
import re
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import Binary, ObjectId
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
from pymongo import ASCENDING, IndexModel, MongoClient, ReturnDocument
from pymongo.errors import ServerSelectionTimeoutError

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
# Esquemas de validação
# --------------------------------------------------------------------------

TEXTO = {"bsonType": "string"}
DATA = {"bsonType": "date"}
INTEIRO = {"bsonType": ["int", "long"]}
ID = {"bsonType": "objectId"}
LISTA_DE_TEXTO = {"bsonType": "array", "items": TEXTO}


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
    "usuarios": {
        "$jsonSchema": {
            "bsonType": "object",
            "properties": {
                "nome": TEXTO,
                "cpf": TEXTO,
                "cargo": {"enum": ["Medico", "Recepcionista"]},
                "crm": TEXTO,
                "uf_crm": TEXTO,
                "telefone": INTEIRO,
                "email": TEXTO,
                "senha": TEXTO,
                "especialidades": LISTA_DE_TEXTO,
                "status": {"enum": STATUS_CADASTRO},
                "data_cadastro": DATA,
            },
            "required": [
                "nome",
                "cpf",
                "cargo",
                "telefone",
                "email",
                "senha",
                "status",
            ],
            "oneOf": [
                {"properties": {"cargo": {"enum": ["Medico"]}}, "required": ["crm"]},
                {
                    "properties": {"cargo": {"enum": ["Recepcionista"]}},
                },
            ],
        }
    },
    "prontuarios": esquema(
        {
            "paciente_id": ID,
            "consultas": lista_de(
                {
                    "_id": ID,
                    "medico_id": ID,
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
    "agendamentos": esquema(
        {
            "paciente_id": ID,
            "medico_id": ID,
            "horario_inicio": DATA,
            "horario_fim": DATA,
            "status": {"enum": STATUS_AGENDAMENTO},
        }
    ),
}

INDICES = {
    "pacientes": [IndexModel([("cpf", ASCENDING)], unique=True)],
    "usuarios": [
        IndexModel([("cpf", ASCENDING)], unique=True),
        IndexModel([("email", ASCENDING)], unique=True),
        IndexModel(
            [("crm", ASCENDING), ("uf_crm", ASCENDING)],
            unique=True,
            partialFilterExpression={"cargo": "Medico"},
        ),
    ],
    "prontuarios": [
        IndexModel([("paciente_id", ASCENDING)], unique=True),
        IndexModel([("consultas.medico_id", ASCENDING)]),
    ],
    "agendamentos": [
        IndexModel([("medico_id", ASCENDING), ("horario_inicio", ASCENDING)]),
        IndexModel([("paciente_id", ASCENDING), ("horario_inicio", ASCENDING)]),
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

MEDICOS = {
    "joao": {
        "nome": "Dr. João Pedro Almeida",
        "cpf": "82634512070",
        "cargo": "Medico",
        "crm": "123456",
        "uf_crm": "SP",
        "telefone": 5511976543210,
        "email": "joao.almeida@example.com",
        "senha": "senha123",
        "especialidades": ["Clínica Médica", "Neurologia"],
        "status": "Ativo",
        "data_cadastro": dia(2025, 10, 1),
    },
    "fernanda": {
        "nome": "Dra. Fernanda Ribeiro",
        "cpf": "46172839005",
        "cargo": "Medico",
        "crm": "654321",
        "uf_crm": "RJ",
        "telefone": 5521987001122,
        "email": "fernanda.ribeiro@example.com",
        "senha": "senha123",
        "especialidades": ["Cardiologia"],
        "status": "Ativo",
        "data_cadastro": dia(2025, 12, 10),
    },
}

RECEPCIONISTAS = {
    "beatriz": {
        "nome": "Beatriz Martins",
        "cpf": "28749613004",
        "cargo": "Recepcionista",
        "telefone": 5511987654321,
        "email": "beatriz.martins@example.com",
        "senha": "senha123",
        "status": "Ativo",
        "data_cadastro": dia(2026, 1, 5),
    }
}


def laudo_texto(conteudo):
    return Binary(conteudo.encode("utf-8"))


def montar_prontuarios(pacientes, medicos):
    consulta_ana = ObjectId()
    consulta_carlos_1 = ObjectId()
    consulta_carlos_2 = ObjectId()
    agora = datetime.now(timezone.utc)

    return [
        {
            "paciente_id": pacientes["ana"],
            "consultas": [
                {
                    "_id": consulta_ana,
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
                    "_id": consulta_carlos_1,
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
                    "_id": consulta_carlos_2,
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


def montar_agendamentos(pacientes, medicos):
    def agendamento(paciente, medico, inicio, status, duracao_min=30):
        return {
            "paciente_id": pacientes[paciente],
            "medico_id": medicos[medico],
            "horario_inicio": inicio,
            "horario_fim": inicio + timedelta(minutes=duracao_min),
            "status": status,
        }

    return [
        agendamento("ana", "joao", horario(2026, 10, 20, 9), "concluido"),
        agendamento("carlos", "fernanda", horario(2026, 10, 22, 14), "concluido"),
        agendamento("carlos", "fernanda", horario(2026, 11, 5, 14), "concluido"),
        agendamento("mariana", "joao", horario(2026, 11, 3, 10), "nao_compareceu"),
        agendamento("ana", "joao", horario(2026, 11, 10, 9), "cancelado"),
        agendamento("ana", "joao", horario(2026, 11, 17, 9), "confirmado"),
        agendamento("carlos", "fernanda", horario(2026, 12, 3, 15), "confirmado"),
    ]


# --------------------------------------------------------------------------
# Grafo no Neo4j
# --------------------------------------------------------------------------

ROTULOS = ["Paciente", "Medico", "Diagnostico", "Medicamento"]
RELACIONAMENTOS = ["ATENDEU", "DIAGNOSTICADO_COM", "RECEBEU_PRESCRICAO"]

RESTRICOES_NEO4J = [
    "CREATE CONSTRAINT paciente_mongo_id IF NOT EXISTS "
    "FOR (n:Paciente) REQUIRE n.mongo_id IS UNIQUE",
    "CREATE CONSTRAINT medico_mongo_id IF NOT EXISTS "
    "FOR (n:Medico) REQUIRE n.mongo_id IS UNIQUE",
    "CREATE CONSTRAINT diagnostico_codigo IF NOT EXISTS "
    "FOR (n:Diagnostico) REQUIRE n.codigo IS UNIQUE",
    "CREATE CONSTRAINT medicamento_nome IF NOT EXISTS "
    "FOR (n:Medicamento) REQUIRE n.nome IS UNIQUE",
]

CYPHER_LIMPAR_GRAFO = """
MATCH (n:Paciente|Medico|Diagnostico|Medicamento)
DETACH DELETE n
"""

CYPHER_PACIENTES = """
UNWIND $linhas AS linha
MERGE (p:Paciente {mongo_id: linha.mongo_id})
SET p += linha.dados
"""

CYPHER_MEDICOS = """
UNWIND $linhas AS linha
MERGE (m:Medico {mongo_id: linha.mongo_id})
SET m += linha.dados
"""

# O histórico clínico é sempre reconstruído a partir do prontuário: apaga as
# relações do paciente e recria, refletindo inclusive o que saiu do MongoDB.
CYPHER_LIMPAR_HISTORICO = """
UNWIND $pacientes AS paciente_id
MATCH (:Paciente {mongo_id: paciente_id})-[r:ATENDEU|DIAGNOSTICADO_COM|RECEBEU_PRESCRICAO]-()
DELETE r
"""

CYPHER_ATENDIMENTOS = """
UNWIND $linhas AS linha
MATCH (p:Paciente {mongo_id: linha.paciente_id})
MATCH (m:Medico {mongo_id: linha.medico_id})
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


def texto_id(valor):
    return str(valor) if valor is not None else None


def ler_pacientes(db):
    linhas = []
    for p in db.pacientes.find(
        {}, {"nome": 1, "sexo": 1, "data_nascimento": 1, "alergias": 1, "status": 1}
    ):
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


def ler_medicos(db):
    return [
        {
            "mongo_id": str(m["_id"]),
            "dados": {
                "nome": m["nome"],
                "crm": m["crm"],
                "uf_crm": m.get("uf_crm"),
                "especialidades": m.get("especialidades", []),
                "status": m["status"],
            },
        }
        for m in db.usuarios.find(
            {"cargo": "Medico"},
            {"nome": 1, "crm": 1, "uf_crm": 1, "especialidades": 1, "status": 1}
        )
    ]


def ler_historico_clinico(db):
    atendimentos, diagnosticos, prescricoes = [], [], []
    for prontuario in db.prontuarios.find({}):
        paciente_id = str(prontuario["paciente_id"])

        for consulta in prontuario.get("consultas", []):
            consulta_id = texto_id(consulta.get("_id"))
            medico_id = texto_id(consulta.get("medico_id"))
            if medico_id:
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
            if prescricao.get("nome"):
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


def sincronizar_neo4j(db, driver, banco):
    """Espelha no Neo4j os pacientes, médicos e prontuários do MongoDB."""
    for restricao in RESTRICOES_NEO4J:
        driver.execute_query(restricao, database_=banco)

    pacientes = ler_pacientes(db)
    medicos = ler_medicos(db)
    atendimentos, diagnosticos, prescricoes = ler_historico_clinico(db)

    def gravar(tx):
        tx.run(CYPHER_PACIENTES, linhas=pacientes)
        tx.run(CYPHER_MEDICOS, linhas=medicos)
        tx.run(CYPHER_LIMPAR_HISTORICO, pacientes=[p["mongo_id"] for p in pacientes])
        tx.run(CYPHER_ATENDIMENTOS, linhas=atendimentos)
        tx.run(CYPHER_DIAGNOSTICOS, linhas=diagnosticos)
        tx.run(CYPHER_PRESCRICOES, linhas=prescricoes)

    with driver.session(database=banco) as sessao:
        sessao.execute_write(gravar)


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------


def carregar_secrets():
    # utf-8-sig: o secrets.toml pode ter sido salvo com BOM.
    return tomllib.loads(ARQUIVO_SECRETS.read_text(encoding="utf-8-sig"))


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


def preparar_colecao(db, nome):
    if nome in db.list_collection_names():
        db.command("collMod", nome, validator=VALIDADORES[nome])
    else:
        db.create_collection(nome, validator=VALIDADORES[nome])
    db[nome].create_indexes(INDICES[nome])


def salvar(colecao, filtro, documento):
    """Insere ou substitui o documento que casa com o filtro e devolve o _id."""
    salvo = colecao.find_one_and_replace(
        filtro,
        documento,
        upsert=True,
        projection={"_id": True},
        return_document=ReturnDocument.AFTER,
    )
    return salvo["_id"]


def semear(db):
    pacientes = {
        chave: salvar(db.pacientes, {"cpf": doc["cpf"]}, doc)
        for chave, doc in PACIENTES.items()
    }
    medicos = {
        chave: salvar(
            db.usuarios, {"crm": doc["crm"], "uf_crm": doc["uf_crm"]}, doc
        )
        for chave, doc in MEDICOS.items()
    }
    for doc in RECEPCIONISTAS.values():
        salvar(db.usuarios, {"cpf": doc["cpf"]}, doc)
    for prontuario in montar_prontuarios(pacientes, medicos):
        salvar(db.prontuarios, {"paciente_id": prontuario["paciente_id"]}, prontuario)
    for agendamento in montar_agendamentos(pacientes, medicos):
        filtro = {
            campo: agendamento[campo]
            for campo in ("paciente_id", "medico_id", "horario_inicio")
        }
        salvar(db.agendamentos, filtro, agendamento)


def imprimir_resumo(db, driver, banco_neo4j):
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


def main():
    parser = argparse.ArgumentParser(
        description="Popula o MongoDB com dados de exemplo e sincroniza o grafo no Neo4j."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="apaga as coleções e o grafo antes de recriá-los",
    )
    args = parser.parse_args()

    secrets = carregar_secrets()
    banco_neo4j = secrets["neo4j"].get("database", "neo4j")

    # Conecta nos dois bancos antes de gravar qualquer coisa.
    with conectar_mongodb(secrets["mongodb"]) as cliente, conectar_neo4j(
        secrets["neo4j"]
    ) as driver:
        db = cliente[secrets["mongodb"]["database"]]
        if args.reset:
            for nome in VALIDADORES:
                db.drop_collection(nome)
            driver.execute_query(CYPHER_LIMPAR_GRAFO, database_=banco_neo4j)

        for nome in VALIDADORES:
            preparar_colecao(db, nome)
        semear(db)
        sincronizar_neo4j(db, driver, banco_neo4j)

        imprimir_resumo(db, driver, banco_neo4j)


if __name__ == "__main__":
    main()
