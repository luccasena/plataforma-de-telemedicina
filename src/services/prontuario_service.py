from datetime import datetime, timezone

from bson import ObjectId


class ProntuarioService:
    def __init__(self, banco):
        self.banco = banco

    def atualizar_anexo(self, paciente_id, consulta_id, arquivo):
        anexo = {
            "nome_arquivo": arquivo.name,
            "tipo_arquivo": arquivo.type,
            "arquivo": arquivo.getvalue(),
            "tamanho_bytes": arquivo.size,
            "data_upload": datetime.now(timezone.utc),
        }
        colecao = self.banco.get_collection("prontuarios")
        resultado = colecao.update_one(
            {"paciente_id": paciente_id, "consultas._id": consulta_id},
            {
                "$set": {
                    "consultas.$[consulta].exames_laudos": [anexo],
                    "data_atualizacao": datetime.now(timezone.utc),
                }
            },
            array_filters=[{"consulta._id": consulta_id}],
        )
        return resultado.matched_count > 0

    def atualizar_diagnosticos(self, paciente_id, consulta_id, diagnosticos):
        resultado = self.banco.get_collection("prontuarios").update_one(
            {"paciente_id": paciente_id, "consultas._id": consulta_id},
            {"$set": {"consultas.$.diagnosticos": diagnosticos}},
        )
        return resultado.matched_count > 0

    def obter_medicos(self):
        return list(
            self.banco.get_collection("usuarios").find(
                {"cargo": "Medico", "status": "Ativo"},
                {"nome": 1, "crm": 1},
            ).sort("nome", 1)
        )

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
            "status": "Agendado",
            "exames_laudos": [],
        }
        resultado = self.banco.get_collection("prontuarios").update_one(
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

    def obter_consultas_em_andamento(self):
        return list(
            self.banco.get_collection("prontuarios").aggregate(
                [
                    {"$unwind": "$consultas"},
                    {"$match": {"consultas.status": "Em andamento"}},
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
                            "medico_nome": {"$first": "$medico.nome"},
                        }
                    },
                    {"$sort": {"paciente_nome": 1}},
                ]
            )
        )

    def obter_prontuario_por_paciente(self, paciente_id):
        prontuario = self.banco.get_collection("prontuarios").find_one(
            {"paciente_id": paciente_id}
        )
        if not prontuario:
            return None

        for consulta in prontuario.get("consultas", []):
            for anexo in consulta.get("exames_laudos", []):
                if "arquivo" in anexo:
                    anexo["arquivo_conteudo"] = anexo["arquivo"]

        return prontuario