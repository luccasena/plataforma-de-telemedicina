import unittest
from datetime import date, datetime, timezone

from services.agenda_cache_service import AgendaCacheService


class RedisFake:
    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.current_time = 0

    def get(self, key):
        if self.expirations.get(key, float("inf")) <= self.current_time:
            self.values.pop(key, None)
            self.expirations.pop(key, None)
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.expirations[key] = self.current_time + ex

    def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return int(existed)


class MongoRepositoryFake:
    def __init__(self, documents, agendamentos=()):
        self.documents = documents
        self.agendamentos = list(agendamentos)
        self.aggregate_calls = 0

    def aggregate(self, collection_name, pipeline):
        if collection_name != "agendamentos":
            raise AssertionError(f"Unexpected collection: {collection_name}")
        self.aggregate_calls += 1
        return self.documents

    def find_documents(self, collection_name, query, projection=None, sort=None):
        if collection_name != "agendamentos":
            raise AssertionError(f"Unexpected collection: {collection_name}")
        return [
            agendamento
            for agendamento in self.agendamentos
            if agendamento["consulta_id"] == query["consulta_id"]
        ]


class AgendaCacheServiceTests(unittest.TestCase):
    def setUp(self):
        self.redis = RedisFake()
        self.repository = MongoRepositoryFake(
            [
                {
                    "agendamento_id": "agendamento-1",
                    "consulta_id": "consulta-1",
                    "paciente_nome": "Ana Souza",
                    "queixa_principal": "Dor de cabeça",
                    "horario_inicio": datetime(
                        2026, 11, 17, 12, 0, tzinfo=timezone.utc
                    ),
                    "horario_fim": datetime(
                        2026, 11, 17, 12, 30, tzinfo=timezone.utc
                    ),
                    "status_agendamento": "confirmado",
                    "status_consulta": "Agendado",
                }
            ]
        )
        self.service = AgendaCacheService(
            self.redis,
            self.repository,
            tempo_expiracao=3600,
        )
        self.data = date(2026, 11, 17)

    def test_cache_miss_loads_mongo_and_sets_ttl(self):
        agenda = self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(len(agenda), 1)
        self.assertEqual(self.repository.aggregate_calls, 1)
        self.assertEqual(
            self.redis.expirations[
                "agenda:medico:medico-1:2026-11-17"
            ],
            self.redis.current_time + 3600,
        )
        self.assertEqual(
            agenda[0]["horario_inicio"],
            "2026-11-17T12:00:00+00:00",
        )
        self.assertEqual(agenda[0]["horario_fim"], "2026-11-17T12:30:00+00:00")
        self.assertEqual(agenda[0]["status_consulta"], "Agendado")

    def test_cache_hit_skips_mongo(self):
        first_result = self.service.obter_agenda_dia("medico-1", self.data)
        second_result = self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(first_result, second_result)
        self.assertEqual(self.repository.aggregate_calls, 1)

    def test_expired_cache_is_reloaded_from_mongo(self):
        self.service.obter_agenda_dia("medico-1", self.data)
        self.redis.current_time += 3600

        self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(self.repository.aggregate_calls, 2)

    def test_invalidation_causes_next_read_to_reload_mongo(self):
        self.service.obter_agenda_dia("medico-1", self.data)

        self.assertTrue(
            self.service.invalidar_agenda_dia("medico-1", self.data)
        )
        self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(self.repository.aggregate_calls, 2)

    def test_invalidar_agendas_da_consulta_removes_each_day_cache(self):
        # 01:00 UTC de 18/11 ainda é 17/11 no horário de Brasília; o PyMongo
        # sem tz_aware devolve o horário sem fuso.
        self.repository.agendamentos = [
            {
                "consulta_id": "consulta-1",
                "medico_id": "medico-1",
                "horario_inicio": datetime(2026, 11, 18, 1, 0),
            },
            {
                "consulta_id": "consulta-1",
                "medico_id": "medico-1",
                "horario_inicio": datetime(2026, 11, 20, 12, 0),
            },
        ]
        outra_data = date(2026, 11, 20)
        self.service.obter_agenda_dia("medico-1", self.data)
        self.service.obter_agenda_dia("medico-1", outra_data)
        self.service.obter_agenda_dia("medico-2", self.data)

        self.service.invalidar_agendas_da_consulta("consulta-1")

        self.assertNotIn("agenda:medico:medico-1:2026-11-17", self.redis.values)
        self.assertNotIn("agenda:medico:medico-1:2026-11-20", self.redis.values)
        self.assertIn("agenda:medico:medico-2:2026-11-17", self.redis.values)

    def test_invalidar_agendas_da_consulta_without_agendamento(self):
        self.service.obter_agenda_dia("medico-1", self.data)

        self.service.invalidar_agendas_da_consulta("consulta-sem-agenda")

        self.assertIn("agenda:medico:medico-1:2026-11-17", self.redis.values)


if __name__ == "__main__":
    unittest.main()
