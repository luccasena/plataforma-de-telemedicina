from datetime import datetime, timezone

from bson import ObjectId

from repositories.mongo_repository import MongoRepository

COLECAO_PRONTUARIOS = "prontuarios"
STATUS_AGENDADO = "Agendado"
STATUS_EM_ANDAMENTO = "Em andamento"
STATUS_CONCLUIDO = "Concluído"


def _filtro_da_consulta(consulta_id):
    return [{"consulta._id": consulta_id}]


class ProntuarioService:
    def __init__(self, mongo_repository: MongoRepository):
        self.mongo_repository = mongo_repository

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
                "$set": {
                    "consultas.$[consulta].exames_laudos": [anexo],
                    "data_atualizacao": datetime.now(timezone.utc),
                }
            },
            array_filters=_filtro_da_consulta(consulta_id),
        )
        return resultado.matched_count > 0

    def atualizar_diagnosticos(self, paciente_id, consulta_id, diagnosticos):
        resultado = self.mongo_repository.update_document(
            COLECAO_PRONTUARIOS,
            {"paciente_id": paciente_id, "consultas._id": consulta_id},
            {"$set": {"consultas.$.diagnosticos": diagnosticos}},
        )
        return resultado.matched_count > 0

    def criar_consulta(
        self,
        paciente_id,
        medico_id,
        queixa_principal,
        sintomas,
        diagnosticos,
        observacoes,
    ):
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
        return self.mongo_repository.aggregate(
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
                    "$lookup": {
                        "from": "usuarios",
                        "localField": "consultas.medico_id",
                        "foreignField": "_id",
                        "as": "medico",
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "paciente_id": 1,
                        "consulta_id": "$consultas._id",
                        "queixa_principal": "$consultas.queixa_principal",
                        "sintomas": "$consultas.sintomas",
                        "diagnosticos": "$consultas.diagnosticos",
                        "observacoes": "$consultas.observacoes",
                        "paciente_nome": {"$first": "$paciente.nome"},
                        "paciente_cpf": {"$first": "$paciente.cpf"},
                        "medico_nome": {"$first": "$medico.nome"},
                    }
                },
                {"$sort": {"paciente_nome": 1}},
            ],
        )

    def finalizar_consulta(self, paciente_id, consulta_id, arquivo, receita):
        laudo = {
            "nome_arquivo": arquivo.name,
            "tipo_arquivo": arquivo.type,
            "arquivo": arquivo.getvalue(),
            "tamanho_bytes": arquivo.size,
            "data_upload": datetime.now(timezone.utc),
            "descricao": "Laudo de finalização da consulta",
        }
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
                "$push": {"consultas.$[consulta].exames_laudos": laudo},
            },
            array_filters=_filtro_da_consulta(consulta_id),
        )
        return resultado.matched_count > 0

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