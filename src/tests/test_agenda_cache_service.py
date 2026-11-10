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


class CollectionFake:
    def __init__(self, documents):
        self.documents = documents
        self.aggregate_calls = 0

    def aggregate(self, pipeline):
        self.aggregate_calls += 1
        return self.documents


class DatabaseFake:
    def __init__(self, documents):
        self.collection = CollectionFake(documents)

    def get_collection(self, name):
        if name != "agendamentos":
            raise AssertionError(f"Unexpected collection: {name}")
        return self.collection


class AgendaCacheServiceTests(unittest.TestCase):
    def setUp(self):
        self.redis = RedisFake()
        self.database = DatabaseFake(
            [
                {
                    "paciente_nome": "Ana Souza",
                    "medico_nome": "Dr. João",
                    "horario_inicio": datetime(
                        2026, 11, 17, 12, 0, tzinfo=timezone.utc
                    ),
                    "status": "confirmado",
                }
            ]
        )
        self.service = AgendaCacheService(
            self.redis,
            self.database,
            tempo_expiracao=3600,
        )
        self.data = date(2026, 11, 17)

    def test_cache_miss_loads_mongo_and_sets_ttl(self):
        agenda = self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(len(agenda), 1)
        self.assertEqual(self.database.collection.aggregate_calls, 1)
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

    def test_cache_hit_skips_mongo(self):
        first_result = self.service.obter_agenda_dia("medico-1", self.data)
        second_result = self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(first_result, second_result)
        self.assertEqual(self.database.collection.aggregate_calls, 1)

    def test_expired_cache_is_reloaded_from_mongo(self):
        self.service.obter_agenda_dia("medico-1", self.data)
        self.redis.current_time += 3600

        self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(self.database.collection.aggregate_calls, 2)

    def test_invalidation_causes_next_read_to_reload_mongo(self):
        self.service.obter_agenda_dia("medico-1", self.data)

        self.assertTrue(
            self.service.invalidar_agenda_dia("medico-1", self.data)
        )
        self.service.obter_agenda_dia("medico-1", self.data)

        self.assertEqual(self.database.collection.aggregate_calls, 2)


if __name__ == "__main__":
    unittest.main()
