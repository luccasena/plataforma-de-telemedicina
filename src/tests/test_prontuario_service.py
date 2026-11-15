import unittest
from types import SimpleNamespace

from services.prontuario_service import ProntuarioService


class MongoRepositoryFake:
    def __init__(self, consultas):
        self.consultas = consultas
        self.pipelines = []

    def aggregate(self, collection_name, pipeline):
        self.pipelines.append(pipeline)
        return [dict(consulta) for consulta in self.consultas]

    def update_document(self, collection_name, filtro, atualizacao, **opcoes):
        self.atualizacao = atualizacao
        return SimpleNamespace(matched_count=1, acknowledged=True)


class ArquivoFake:
    name = "exame.pdf"
    type = "application/pdf"
    size = 3

    def getvalue(self):
        return b"pdf"


class UsuarioServiceFake:
    def __init__(self, nomes):
        self.nomes = nomes

    def obter_nomes(self, ids):
        return {
            usuario_id: self.nomes[usuario_id]
            for usuario_id in ids
            if usuario_id in self.nomes
        }


class ProntuarioServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = MongoRepositoryFake(
            [
                {"consulta_id": "c1", "paciente_nome": "Ana", "medico_id": 1},
                {"consulta_id": "c2", "paciente_nome": "Carlos", "medico_id": 99},
            ]
        )
        self.service = ProntuarioService(
            self.repository, UsuarioServiceFake({1: "Dr. João"})
        )

    def test_consultas_recebem_o_nome_do_medico_do_postgres(self):
        consultas = self.service.obter_consultas_agendadas()

        self.assertEqual(consultas[0]["medico_nome"], "Dr. João")

    def test_medico_nao_encontrado_fica_sem_nome(self):
        consultas = self.service.obter_consultas_em_andamento()

        self.assertNotIn("medico_nome", consultas[1])

    def test_pipeline_nao_busca_usuarios_no_mongodb(self):
        self.service.obter_consultas_agendadas()

        colecoes = [
            etapa["$lookup"]["from"]
            for etapa in self.repository.pipelines[0]
            if "$lookup" in etapa
        ]
        self.assertNotIn("usuarios", colecoes)

    def test_anexo_e_acrescentado_sem_apagar_os_anteriores(self):
        self.assertTrue(self.service.atualizar_anexo("p1", "c1", ArquivoFake()))

        atualizacao = self.repository.atualizacao
        anexo = atualizacao["$push"]["consultas.$[consulta].exames_laudos"]
        self.assertEqual(anexo["nome_arquivo"], "exame.pdf")
        self.assertEqual(anexo["arquivo"], b"pdf")
        self.assertNotIn("consultas.$[consulta].exames_laudos", atualizacao["$set"])


if __name__ == "__main__":
    unittest.main()
