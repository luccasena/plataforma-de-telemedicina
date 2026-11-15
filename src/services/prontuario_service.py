from datetime import datetime, timedelta, timezone

from bson import ObjectId

from repositories.mongo_repository import MongoRepository
from repositories.neo4j_repository import Neo4jRepository
from services.mapeamento_clinico_service import (
    MapeamentoClinicoService,
    normalizar_medicamento,
)
from services.usuario_service import UsuarioService

COLECAO_AGENDAMENTOS = "agendamentos"
COLECAO_PACIENTES = "pacientes"
COLECAO_PRONTUARIOS = "prontuarios"
STATUS_AGENDADO = "Agendado"
STATUS_EM_ANDAMENTO = "Em andamento"
STATUS_CONCLUIDO = "Concluído"
STATUS_AGENDAMENTO_CONFIRMADO = "confirmado"
DURACAO_CONSULTA = timedelta(minutes=30)

def _filtro_da_consulta(consulta_id):
    return [{"consulta._id": consulta_id}]

class ProntuarioService:
    def __init__(
        self, mongo_repository: MongoRepository, usuario_service: UsuarioService, neo4j_repository: Neo4jRepository
    ):
        self.mongo_repository = mongo_repository
        # O grafo do Mapeamento Clínico recebe as consultas criadas.
        self.mapeamento_clinico_service = MapeamentoClinicoService(neo4j_repository)
        self.usuario_service = usuario_service

    def atualizar_anexo(self, paciente_id, consulta_id, arquivo):
        anexo = {
            "nome_arquivo": arquivo.name,
            "tipo_arquivo": arquivo.type,
            "arquivo": arquivo.getvalue(),
            "tamanho_bytes": arquivo.size,
            "data_upload": datetime.now(timezone.utc),
        }
        resultado = self.mongo_repository.update_document(
            COLECAO_PRONTUARIOS,
            {"paciente_id": paciente_id, "consultas._id": consulta_id},
            {
                "$push": {"consultas.$[consulta].exames_laudos": anexo},
                "$set": {"data_atualizacao": datetime.now(timezone.utc)},
            },
            array_filters=_filtro_da_consulta(consulta_id),
        )
        return resultado.matched_count > 0

    def atualizar_diagnosticos(self, paciente_id, consulta_id, diagnosticos):
        filtro = {"paciente_id": paciente_id, "consultas._id": consulta_id}
        # O médico da consulta é gravado no relacionamento do grafo.
        prontuario = self.mongo_repository.find_document(
            COLECAO_PRONTUARIOS, filtro, {"consultas.$": 1}
        )
        if not prontuario:
            return False
        paciente = self.mongo_repository.find_document(
            COLECAO_PACIENTES, {"_id": paciente_id}
        )

        resultado = self.mongo_repository.update_document(
            COLECAO_PRONTUARIOS,
            filtro,
            {"$set": {"consultas.$.diagnosticos": diagnosticos}},
        )

        self.mapeamento_clinico_service.atualizar_diagnosticos(
            paciente,
            consulta_id,
            prontuario["consultas"][0]["medico_id"],
            diagnosticos,
        )
        return resultado.matched_count > 0

    def criar_consulta(
        self,
        paciente_id,
        medico_id,
        horario_inicio,
        queixa_principal,
        sintomas,
        diagnosticos,
        observacoes,
    ):
        paciente = self.mongo_repository.find_document(
            COLECAO_PACIENTES, {"_id": paciente_id}
        )
        medico = {
            "id": medico_id,
            "nome": self.usuario_service.obter_nomes([medico_id]).get(medico_id),
        }
        consulta = {
            "_id": ObjectId(),
            "medico_id": medico_id,
            "queixa_principal": queixa_principal,
            "sintomas": sintomas,
            "diagnosticos": diagnosticos,
            "observacoes": observacoes,
            "status": STATUS_AGENDADO,
            "exames_laudos": [],
        }
        resultado = self.mongo_repository.update_document(
            COLECAO_PRONTUARIOS,
            {"paciente_id": paciente_id},
            {
                "$setOnInsert": {
                    "paciente_id": paciente_id,
                    "prescricoes": [],
                },
                "$push": {"consultas": consulta},
                "$set": {"data_atualizacao": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        # O agendamento leva a consulta para a agenda do dia do médico.
        self.mongo_repository.insert_document(
            COLECAO_AGENDAMENTOS,
            {
                "paciente_id": paciente_id,
                "medico_id": medico_id,
                "consulta_id": consulta["_id"],
                "horario_inicio": horario_inicio,
                "horario_fim": horario_inicio + DURACAO_CONSULTA,
                "status": STATUS_AGENDAMENTO_CONFIRMADO,
            },
        )

        self.mapeamento_clinico_service.registrar_consulta(paciente, medico, consulta)
        return resultado.acknowledged

    def obter_consultas_agendadas(self):
        return self._obter_consultas_por_status(STATUS_AGENDADO)

    def obter_consultas_em_andamento(self):
        return self._obter_consultas_por_status(STATUS_EM_ANDAMENTO)

    def iniciar_consulta(self, paciente_id, consulta_id):
        agora = datetime.now(timezone.utc)
        resultado = self.mongo_repository.update_document(
            COLECAO_PRONTUARIOS,
            {
                "paciente_id": paciente_id,
                "consultas": {
                    "$elemMatch": {"_id": consulta_id, "status": STATUS_AGENDADO}
                },
            },
            {
                "$set": {
                    "consultas.$[consulta].status": STATUS_EM_ANDAMENTO,
                    "data_atualizacao": agora,
                }
            },
            array_filters=_filtro_da_consulta(consulta_id),
        )
        return resultado.matched_count > 0

    def _obter_consultas_por_status(self, status):
        consultas = self.mongo_repository.aggregate(
            COLECAO_PRONTUARIOS,
            [
                {"$unwind": "$consultas"},
                {"$match": {"consultas.status": status}},
                {
                    "$lookup": {
                        "from": "pacientes",
                        "localField": "paciente_id",
                        "foreignField": "_id",
                        "as": "paciente",
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "paciente_id": 1,
                        "consulta_id": "$consultas._id",
                        "medico_id": "$consultas.medico_id",
                        "queixa_principal": "$consultas.queixa_principal",
                        "sintomas": "$consultas.sintomas",
                        "diagnosticos": "$consultas.diagnosticos",
                        "observacoes": "$consultas.observacoes",
                        "paciente_nome": {"$first": "$paciente.nome"},
                        "paciente_cpf": {"$first": "$paciente.cpf"},
                    }
                },
                {"$sort": {"paciente_nome": 1}},
            ],
        )
        # Os médicos ficam no PostgreSQL, então o nome é buscado à parte.
        nomes = self.usuario_service.obter_nomes(
            consulta.get("medico_id") for consulta in consultas
        )
        for consulta in consultas:
            nome = nomes.get(consulta.get("medico_id"))
            if nome is not None:
                consulta["medico_nome"] = nome
        return consultas

    def finalizar_consulta(
        self, paciente_id, consulta_id, arquivo, receita, prescricoes=()
    ):
        """
        Conclui a consulta com o laudo e a receita e acrescenta as prescrições
        (nome, dosagem, frequência e orientações) ao prontuário. As prescrições
        também vão para o grafo, onde são verificadas pelos alertas de segurança.
        """
        laudo = {
            "nome_arquivo": arquivo.name,
            "tipo_arquivo": arquivo.type,
            "arquivo": arquivo.getvalue(),
            "tamanho_bytes": arquivo.size,
            "data_upload": datetime.now(timezone.utc),
            "descricao": "Laudo de finalização da consulta",
        }
        prescricoes = [
            {
                **prescricao,
                "nome": normalizar_medicamento(prescricao["nome"]),
                "consulta_id": consulta_id,
            }
            for prescricao in prescricoes
        ]
        paciente = self.mongo_repository.find_document(
            COLECAO_PACIENTES, {"_id": paciente_id}
        )
        resultado = self.mongo_repository.update_document(
            COLECAO_PRONTUARIOS,
            {
                "paciente_id": paciente_id,
                "consultas": {
                    "$elemMatch": {"_id": consulta_id, "status": STATUS_EM_ANDAMENTO}
                },
            },
            {
                "$set": {
                    "consultas.$[consulta].status": STATUS_CONCLUIDO,
                    "consultas.$[consulta].receita": receita,
                    "consultas.$[consulta].data_finalizacao": datetime.now(timezone.utc),
                    "data_atualizacao": datetime.now(timezone.utc),
                },
                "$push": {
                    "consultas.$[consulta].exames_laudos": laudo,
                    "prescricoes": {"$each": prescricoes},
                },
            },
            array_filters=_filtro_da_consulta(consulta_id),
        )
        if resultado.matched_count == 0:
            return False

        if prescricoes:
            self.mapeamento_clinico_service.registrar_prescricoes(
                paciente, consulta_id, prescricoes
            )
        return True

    def obter_prontuario_por_paciente(self, paciente_id):
        prontuario = self.mongo_repository.find_document(
            COLECAO_PRONTUARIOS, {"paciente_id": paciente_id}
        )
        if not prontuario:
            return None

        for consulta in prontuario.get("consultas", []):
            for anexo in consulta.get("exames_laudos", []):
                if "arquivo" in anexo:
                    anexo["arquivo_conteudo"] = anexo["arquivo"]

        return prontuario