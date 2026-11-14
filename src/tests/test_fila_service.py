import unittest

from services.fila_service import CHAVE_FILA, PREFIXO_CONSULTA, FilaService


class PipelineFake:
    def __init__(self, redis):
        self.redis = redis
        self.comandos = []

    def __getattr__(self, nome):
        def registrar(*args, **kwargs):
            self.comandos.append((nome, args, kwargs))
            return self

        return registrar

    def execute(self):
        return [
            getattr(self.redis, nome)(*args, **kwargs)
            for nome, args, kwargs in self.comandos
        ]


class RedisFake:
    def __init__(self):
        self.sorted_sets = {}
        self.hashes = {}

    def pipeline(self):
        return PipelineFake(self)

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zrem(self, key, member):
        return int(self.sorted_sets.get(key, {}).pop(member, None) is not None)

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

    def hset(self, key, name=None, value=None, mapping=None):
        dados = self.hashes.setdefault(key, {})
        if mapping:
            dados.update({campo: str(valor) for campo, valor in mapping.items()})
        if name is not None:
            dados[name] = str(value)

    def hget(self, key, name):
        return self.hashes.get(key, {}).get(name)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def delete(self, key):
        return int(self.hashes.pop(key, None) is not None)

    def persist(self, key):
        return 1

    def scan_iter(self, match):
        prefix = match.removesuffix("*")
        return (key for key in list(self.hashes) if key.startswith(prefix))


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
                "chegada": "10",
            },
            f"{PREFIXO_CONSULTA}consulta-2": {
                "paciente_nome": "Carlos Lima",
                "status": "Aguardando",
                "chegada": "20",
            },
        }

    def test_chamar_proximo_removes_first_and_marks_em_andamento(self):
        consulta = self.service.chamar_proximo()

        self.assertEqual(consulta["consulta_id"], "consulta-1")
        self.assertEqual(consulta["status"], "Em andamento")
        self.assertEqual(
            self.service.obter_consultas_ativas(),
            [
                {
                    "consulta_id": "consulta-1",
                    "paciente_nome": "Ana Souza",
                    "status": "Em andamento",
                    "chegada": "10",
                }
            ],
        )
        self.assertEqual(self.service.obter_fila()[0]["consulta_id"], "consulta-2")

    def test_chamar_proximo_returns_none_for_empty_queue(self):
        self.redis.sorted_sets[CHAVE_FILA] = {}

        self.assertIsNone(self.service.chamar_proximo())

    def test_obter_ids_na_fila(self):
        self.assertEqual(self.service.obter_ids_na_fila(), {"consulta-1", "consulta-2"})

    def test_devolver_a_fila_restores_original_position(self):
        self.service.chamar_proximo()

        self.service.devolver_a_fila("consulta-1")

        fila = self.service.obter_fila()
        self.assertEqual(
            [item["consulta_id"] for item in fila], ["consulta-1", "consulta-2"]
        )
        self.assertEqual(fila[0]["status"], "Aguardando")
        self.assertEqual(self.service.obter_consultas_ativas(), [])

    def test_descartar_removes_queue_entry_and_hash(self):
        self.service.chamar_proximo()

        self.service.descartar("consulta-1")

        self.assertNotIn(f"{PREFIXO_CONSULTA}consulta-1", self.redis.hashes)
        self.assertEqual(self.service.obter_consultas_ativas(), [])
        self.assertEqual(self.service.obter_ids_na_fila(), {"consulta-2"})

    def test_adicionar_na_fila_goes_to_end_with_aguardando(self):
        self.service.adicionar_na_fila("consulta-3", "paciente-3", "Bruna Alves")

        fila = self.service.obter_fila()
        self.assertEqual(fila[-1]["consulta_id"], "consulta-3")
        self.assertEqual(fila[-1]["posicao"], 3)
        self.assertEqual(fila[-1]["status"], "Aguardando")


if __name__ == "__main__":
    unittest.main()
