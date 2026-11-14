import json
from datetime import datetime, time, timedelta, timezone

from repositories.mongo_repository import MongoRepository


TEMPO_EXPIRACAO_AGENDA = 12 * 60 * 60
FUSO_BRASILIA = timezone(timedelta(hours=-3))
PREFIXO_AGENDA = "agenda:medico:"
STATUS_AGENDA_CACHE = ["confirmado", "em_atendimento"]


class AgendaCacheService:
    """Agenda diária do médico com cache-aside no Redis.

    Cada agendamento aponta para a consulta do prontuário pelo campo
    consulta_id. A agenda lista essas consultas no intervalo horario_inicio a
    horario_fim do agendamento, com o status atual da consulta.
    """

    def __init__(
        self,
        redis_client,
        mongo_repository: MongoRepository,
        tempo_expiracao=TEMPO_EXPIRACAO_AGENDA,
    ):
        self.redis_client = redis_client
        self.mongo_repository = mongo_repository
        self.tempo_expiracao = tempo_expiracao

    def _obter_chave(self, medico_id, data):
        return f"{PREFIXO_AGENDA}{medico_id}:{data.isoformat()}"

    def obter_agenda_dia(self, medico_id, data):
        chave = self._obter_chave(medico_id, data)
        agenda_em_cache = self.redis_client.get(chave)
        if agenda_em_cache is not None:
            return json.loads(agenda_em_cache)

        inicio = datetime.combine(data, time.min, tzinfo=FUSO_BRASILIA)
        fim = inicio + timedelta(days=1)
        agenda = self.mongo_repository.aggregate(
            "agendamentos",
            [
                {
                    "$match": {
                        "medico_id": medico_id,
                        "horario_inicio": {"$gte": inicio, "$lt": fim},
                        "status": {"$in": STATUS_AGENDA_CACHE},
                    }
                },
                {
                    "$lookup": {
                        "from": "prontuarios",
                        "localField": "consulta_id",
                        "foreignField": "consultas._id",
                        "let": {"consulta_id": "$consulta_id"},
                        "pipeline": [
                            {"$unwind": "$consultas"},
                            {
                                "$match": {
                                    "$expr": {
                                        "$eq": ["$consultas._id", "$$consulta_id"]
                                    }
                                }
                            },
                            {"$replaceWith": "$consultas"},
                        ],
                        "as": "consulta",
                    }
                },
                {"$unwind": "$consulta"},
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
                        "agendamento_id": {"$toString": "$_id"},
                        "consulta_id": {"$toString": "$consulta._id"},
                        "paciente_id": {"$toString": "$paciente_id"},
                        "paciente_nome": {"$first": "$paciente.nome"},
                        "queixa_principal": "$consulta.queixa_principal",
                        "horario_inicio": 1,
                        "horario_fim": 1,
                        "status_agendamento": "$status",
                        "status_consulta": "$consulta.status",
                    }
                },
                {"$sort": {"horario_inicio": 1}},
            ],
        )
        agenda = [self._normalizar_agendamento(item) for item in agenda]
        self.redis_client.set(
            chave,
            json.dumps(agenda, ensure_ascii=False),
            ex=self.tempo_expiracao,
        )
        return agenda

    def invalidar_agenda_dia(self, medico_id, data):
        return self.redis_client.delete(self._obter_chave(medico_id, data)) > 0

    def invalidar_agendas_da_consulta(self, consulta_id):
        """Remove do cache as agendas que exibem a consulta.

        Deve ser chamado sempre que o status da consulta mudar, pois a agenda
        em cache guarda o status da consulta.
        """
        agendamentos = self.mongo_repository.find_documents(
            "agendamentos",
            {"consulta_id": consulta_id},
            {"medico_id": 1, "horario_inicio": 1},
        )
        for agendamento in agendamentos:
            self.invalidar_agenda_dia(
                agendamento["medico_id"],
                self._obter_dia_local(agendamento["horario_inicio"]),
            )

    @staticmethod
    def _obter_dia_local(horario):
        # Sem tz_aware, o PyMongo devolve o horário em UTC sem fuso.
        if horario.tzinfo is None:
            horario = horario.replace(tzinfo=timezone.utc)
        return horario.astimezone(FUSO_BRASILIA).date()

    @staticmethod
    def _normalizar_agendamento(agendamento):
        normalizado = {}
        for campo, valor in agendamento.items():
            if isinstance(valor, datetime):
                if valor.tzinfo is None:
                    valor = valor.replace(tzinfo=timezone.utc)
                normalizado[campo] = valor.isoformat()
            elif valor is None or isinstance(valor, (str, int, float, bool)):
                normalizado[campo] = valor
            else:
                normalizado[campo] = str(valor)
        return normalizado
