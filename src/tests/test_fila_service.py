import unittest

from services.fila_service import CHAVE_FILA, PREFIXO_CONSULTA, FilaService


class RedisFake:
    def __init__(self):
        self.sorted_sets = {}
        self.hashes = {}

    def zrange(self, key, start, end):
        ordered = sorted(
            self.sorted_sets.get(key, {}).items(),
            key=lambda item: (item[1], item[0]),
        )
        stop = len(ordered) if end == -1 else end + 1
        return [member for member, _ in ordered[start:stop]]

    def zpopmin(self, key, count):
        ordered = sorted(
            self.sorted_sets.get(key, {}).items(),
            key=lambda item: (item[1], item[0]),
        )[:count]
        for member, _ in ordered:
            del self.sorted_sets[key][member]
        return ordered

    def hset(self, key, name, value):
        self.hashes.setdefault(key, {})[name] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def scan_iter(self, match):
        prefix = match.removesuffix("*")
        return (key for key in self.hashes if key.startswith(prefix))


class FilaServiceTests(unittest.TestCase):
    def setUp(self):
        self.redis = RedisFake()
        self.service = FilaService(self.redis)
        self.redis.sorted_sets[CHAVE_FILA] = {
            "consulta-1": 10,
            "consulta-2": 20,
        }
        self.redis.hashes = {
            f"{PREFIXO_CONSULTA}consulta-1": {
                "paciente_nome": "Ana Souza",
                "status": "Aguardando",
            },
            f"{PREFIXO_CONSULTA}consulta-2": {
                "paciente_nome": "Carlos Lima",
                "status": "Aguardando",
            },
        }

    def test_chamar_proximo_removes_first_and_marks_active(self):
        consulta = self.service.chamar_proximo()

        self.assertEqual(consulta["consulta_id"], "consulta-1")
        self.assertEqual(consulta["status"], "Em atendimento")
        self.assertEqual(
            self.service.obter_consultas_ativas(),
            [
                {
                    "consulta_id": "consulta-1",
                    "paciente_nome": "Ana Souza",
                    "status": "Em atendimento",
                }
            ],
        )
        self.assertEqual(self.service.obter_fila()[0]["consulta_id"], "consulta-2")

    def test_chamar_proximo_returns_none_for_empty_queue(self):
        self.redis.sorted_sets[CHAVE_FILA] = {}

        self.assertIsNone(self.service.chamar_proximo())


if __name__ == "__main__":
    unittest.main()
