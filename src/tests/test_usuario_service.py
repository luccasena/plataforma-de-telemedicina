import unittest
from contextlib import contextmanager

from services.usuario_service import UsuarioService


class CursorFake:
    def __init__(self, linha):
        self.linha = linha
        self.comandos = []

    def execute(self, query, params=None):
        self.comandos.append((query, params))

    def fetchone(self):
        return self.linha


class PostgresRepositoryFake:
    def __init__(self, linhas=()):
        self.linhas = list(linhas)
        self.consultas = []
        self.cursor = CursorFake(self.linhas[0] if self.linhas else None)
        self.confirmada = None

    def fetch_one(self, query, params=None):
        self.consultas.append((query, params))
        return self.linhas[0] if self.linhas else None

    def fetch_all(self, query, params=None):
        self.consultas.append((query, params))
        return [dict(linha) for linha in self.linhas]

    @contextmanager
    def transacao(self):
        try:
            yield self.cursor
        except Exception:
            self.confirmada = False
            raise
        self.confirmada = True


class MongoRepositoryFake:
    def __init__(self, documentos=(), erro=None):
        self.documentos = list(documentos)
        self.erro = erro
        self.chamadas = []

    def find_documents(self, collection_name, query, projection=None, sort=None):
        self.chamadas.append((collection_name, query))
        return [dict(documento) for documento in self.documentos]

    def find_and_update_document(self, collection_name, query, update, upsert=False):
        self.chamadas.append((collection_name, query, update))
        if self.erro:
            raise self.erro
        return {"_id": "x", **query, **update["$set"]}


class UsuarioServiceTests(unittest.TestCase):
    def test_autenticar_normaliza_o_email(self):
        repository = PostgresRepositoryFake(
            [{"id": 1, "nome": "Ana", "email": "ana@example.com", "cargo": "Medico"}]
        )
        usuario = UsuarioService(repository, MongoRepositoryFake()).autenticar(
            "  Ana@Example.COM ", "senha123"
        )

        self.assertEqual(usuario["id"], 1)
        self.assertEqual(repository.consultas[0][1], ("ana@example.com", "senha123"))

    def test_obter_nomes_devolve_nome_por_id(self):
        repository = PostgresRepositoryFake(
            [{"id": 1, "nome": "Dr. João"}, {"id": 2, "nome": "Dra. Fernanda"}]
        )

        nomes = UsuarioService(repository, MongoRepositoryFake()).obter_nomes(
            [2, 1, 2, None]
        )

        self.assertEqual(nomes, {1: "Dr. João", 2: "Dra. Fernanda"})
        self.assertEqual(repository.consultas[0][1], ([1, 2],))

    def test_obter_nomes_sem_ids_nao_consulta_o_banco(self):
        repository = PostgresRepositoryFake()

        nomes = UsuarioService(repository, MongoRepositoryFake()).obter_nomes([None])

        self.assertEqual(nomes, {})
        self.assertEqual(repository.consultas, [])

    def test_obter_medicos_junta_o_crm_da_colecao_medicos(self):
        postgres = PostgresRepositoryFake(
            [{"id": 1, "nome": "Dr. João"}, {"id": 2, "nome": "Dra. Fernanda"}]
        )
        mongo = MongoRepositoryFake(
            [{"usuario_id": 1, "crm": "123456", "uf_crm": "SP"}]
        )

        medicos = UsuarioService(postgres, mongo).obter_medicos()

        self.assertEqual(
            medicos[0], {"id": 1, "nome": "Dr. João", "crm": "123456", "uf_crm": "SP"}
        )
        self.assertNotIn("crm", medicos[1])
        self.assertEqual(
            mongo.chamadas[0], ("medicos", {"usuario_id": {"$in": [1, 2]}})
        )

    def test_obter_medicos_sem_medicos_nao_consulta_o_mongodb(self):
        mongo = MongoRepositoryFake()

        self.assertEqual(
            UsuarioService(PostgresRepositoryFake(), mongo).obter_medicos(), []
        )
        self.assertEqual(mongo.chamadas, [])

    def test_criar_medico_grava_os_detalhes_com_o_id_do_postgres(self):
        postgres = PostgresRepositoryFake([{"id": 7, "nome": "Dr. Novo"}])
        mongo = MongoRepositoryFake()

        medico = UsuarioService(postgres, mongo).criar_medico(
            "Dr. Novo", "123", "999", "MG", 5531900000000, "novo@example.com", "x"
        )

        colecao, filtro, _ = mongo.chamadas[0]
        self.assertEqual((colecao, filtro), ("medicos", {"usuario_id": 7}))
        self.assertEqual(medico["usuario_id"], 7)
        self.assertEqual(medico["crm"], "999")
        self.assertNotIn("_id", medico)
        self.assertTrue(postgres.confirmada)

    def test_criar_medico_desfaz_o_usuario_se_o_mongodb_falhar(self):
        postgres = PostgresRepositoryFake([{"id": 7, "nome": "Dr. Novo"}])
        mongo = MongoRepositoryFake(erro=RuntimeError("MongoDB fora do ar"))

        with self.assertRaises(RuntimeError):
            UsuarioService(postgres, mongo).criar_medico(
                "Dr. Novo", "123", "999", "MG", 5531900000000, "novo@example.com", "x"
            )

        self.assertFalse(postgres.confirmada)


if __name__ == "__main__":
    unittest.main()
