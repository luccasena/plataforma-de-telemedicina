import json
from datetime import date, datetime, time, timedelta, timezone


TEMPO_EXPIRACAO_AGENDA = 12 * 60 * 60
FUSO_BRASILIA = timezone(timedelta(hours=-3))
STATUS_AGENDA_CACHE = ["confirmado", "em_atendimento"]


class AgendaCacheService:
    def __init__(
        self,
        redis_client,
        banco,
        tempo_expiracao=TEMPO_EXPIRACAO_AGENDA,
    ):
        self.redis_client = redis_client
        self.banco = banco
        self.tempo_expiracao = tempo_expiracao

    def _obter_chave(self, medico_id, data):
        return f"agenda:medico:{medico_id}:{data.isoformat()}"

    def obter_agenda_dia(self, medico_id, data):
        chave = self._obter_chave(medico_id, data)
        agenda_em_cache = self.redis_client.get(chave)
        if agenda_em_cache is not None:
            return json.loads(agenda_em_cache)

        inicio = datetime.combine(data, time.min, tzinfo=FUSO_BRASILIA)
        fim = inicio + timedelta(days=1)
        agenda = list(
            self.banco.get_collection("agendamentos").aggregate(
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
                            "from": "pacientes",
                            "localField": "paciente_id",
                            "foreignField": "_id",
                            "as": "paciente",
                        }
                    },
                    {
                        "$lookup": {
                            "from": "usuarios",
                            "localField": "medico_id",
                            "foreignField": "_id",
                            "as": "medico",
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "paciente_id": {"$toString": "$paciente_id"},
                            "medico_id": {"$toString": "$medico_id"},
                            "paciente_nome": {"$first": "$paciente.nome"},
                            "medico_nome": {"$first": "$medico.nome"},
                            "horario_inicio": 1,
                            "horario_fim": 1,
                            "status": 1,
                        }
                    },
                    {"$sort": {"horario_inicio": 1}},
                ]
            )
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
