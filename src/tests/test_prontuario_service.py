import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from neo4j.exceptions import ServiceUnavailable

from services.prontuario_service import ProntuarioService


class MongoRepositoryFake:
    def __init__(self, consultas):
        self.consultas = consultas
        self.pipelines = []

    def aggregate(self, collection_name, pipeline):
        self.pipelines.append(pipeline)
        return [dict(consulta) for consulta in self.consultas]

    def find_document(self, collection_name, query, projection=None):
        return self.documentos.get(collection_name)

    def insert_document(self, collection_name, document):
        self.inseridos.append((collection_name, document))

    def update_document(self, collection_name, filtro, atualizacao, **opcoes):
        self.atualizacao = atualizacao
        return SimpleNamespace(matched_count=1, acknowledged=True)


class Neo4jRepositoryFake:
    def __init__(self, erro=None):
        self.erro = erro
        self.chamadas = []

    def criar_paciente(self, paciente_id, dados):
        if self.erro:
            raise self.erro
        self.chamadas.append(("criar_paciente", paciente_id, dados))

    def criar_medico(self, medico_id, dados):
        self.chamadas.append(("criar_medico", medico_id, dados))

    def criar_atendimento(self, medico_id, paciente_id, consulta_id, queixa, sintomas):
        self.chamadas.append(
            ("criar_atendimento", medico_id, paciente_id, consulta_id, queixa, sintomas)
        )

    def criar_diagnostico(self, paciente_id, consulta_id, medico_id, codigo, nome, cid):
        self.chamadas.append(
            ("criar_diagnostico", paciente_id, consulta_id, medico_id, codigo, nome, cid)
        )

    def remover_diagnosticos_consulta(self, paciente_id, consulta_id):
        self.chamadas.append(("remover_diagnosticos_consulta", paciente_id, consulta_id))

    def criar_prescricao(self, paciente_id, consulta_id, nome, dosagem, frequencia, orientacoes):
        self.chamadas.append(
            ("criar_prescricao", paciente_id, consulta_id, nome, dosagem, frequencia, orientacoes)
        )


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
        self.paciente = {
            "_id": "p1",
            "nome": "Ana",
            "alergias": ["Dipirona"],
            "status": "Ativo",
        }
        self.repository.documentos = {
            "pacientes": self.paciente,
            "prontuarios": {"consultas": [{"_id": "c1", "medico_id": 1}]},
        }
        self.repository.inseridos = []
        self.horario = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        self.neo4j = Neo4jRepositoryFake()
        self.service = ProntuarioService(
            self.repository, UsuarioServiceFake({1: "Dr. João"}), self.neo4j
        )

    def criar_consulta(self):
        return self.service.criar_consulta(
            "p1",
            1,
            self.horario,
            "Dor de cabeça",
            ["Náusea"],
            ["Enxaqueca sem aura (CID G43.0)", "Insônia"],
            "",
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

    def test_consulta_criada_tambem_e_registrada_no_grafo(self):
        self.assertTrue(self.criar_consulta())

        consulta_id = str(self.repository.atualizacao["$push"]["consultas"]["_id"])
        self.assertEqual(
            self.neo4j.chamadas,
            [
                (
                    "criar_paciente",
                    "p1",
                    {
                        "nome": "Ana",
                        "sexo": None,
                        "data_nascimento": None,
                        "alergias": ["Dipirona"],
                        "status": "Ativo",
                    },
                ),
                ("criar_medico", 1, {"nome": "Dr. João"}),
                ("criar_atendimento", 1, "p1", consulta_id, "Dor de cabeça", ["Náusea"]),
                # Com CID, o código do diagnóstico é o CID; sem CID, é o nome.
                ("criar_diagnostico", "p1", consulta_id, 1, "G43.0", "Enxaqueca sem aura", "G43.0"),
                ("criar_diagnostico", "p1", consulta_id, 1, "Insônia", "Insônia", None),
            ],
        )

    def test_erro_no_neo4j_e_repassado_depois_de_gravar_no_mongodb(self):
        self.neo4j.erro = ServiceUnavailable("Neo4j fora do ar")

        with self.assertRaises(ServiceUnavailable):
            self.criar_consulta()

        self.assertIn("consultas", self.repository.atualizacao["$push"])

    def test_consulta_criada_gera_agendamento_confirmado(self):
        self.criar_consulta()

        consulta_id = self.repository.atualizacao["$push"]["consultas"]["_id"]
        self.assertEqual(
            self.repository.inseridos,
            [
                (
                    "agendamentos",
                    {
                        "paciente_id": "p1",
                        "medico_id": 1,
                        "consulta_id": consulta_id,
                        "horario_inicio": self.horario,
                        "horario_fim": self.horario + timedelta(minutes=30),
                        "status": "confirmado",
                    },
                )
            ],
        )

    def test_diagnosticos_editados_substituem_os_do_grafo(self):
        self.assertTrue(
            self.service.atualizar_diagnosticos("p1", "c1", ["Asma (CID J45.9)"])
        )

        self.assertEqual(
            self.repository.atualizacao,
            {"$set": {"consultas.$.diagnosticos": ["Asma (CID J45.9)"]}},
        )
        self.assertEqual(
            self.neo4j.chamadas[1:],
            [
                ("remover_diagnosticos_consulta", "p1", "c1"),
                ("criar_diagnostico", "p1", "c1", 1, "J45.9", "Asma", "J45.9"),
            ],
        )

    def test_diagnosticos_de_consulta_inexistente_nao_alteram_nada(self):
        self.repository.documentos["prontuarios"] = None

        self.assertFalse(self.service.atualizar_diagnosticos("p1", "c9", ["Asma"]))
        self.assertEqual(self.neo4j.chamadas, [])
        self.assertFalse(hasattr(self.repository, "atualizacao"))

    def test_finalizacao_grava_prescricoes_e_registra_no_grafo(self):
        finalizada = self.service.finalizar_consulta(
            "p1",
            "c1",
            ArquivoFake(),
            "Repouso",
            [{"nome": "  ibuprofeno ", "dosagem": "400 mg"}],
        )

        self.assertTrue(finalizada)
        self.assertEqual(
            self.repository.atualizacao["$push"]["prescricoes"],
            {"$each": [{"nome": "Ibuprofeno", "dosagem": "400 mg", "consulta_id": "c1"}]},
        )
        self.assertEqual(
            self.neo4j.chamadas[1:],
            [("criar_prescricao", "p1", "c1", "Ibuprofeno", "400 mg", None, None)],
        )

    def test_finalizacao_sem_prescricoes_nao_acessa_o_grafo(self):
        self.assertTrue(
            self.service.finalizar_consulta("p1", "c1", ArquivoFake(), "", [])
        )
        self.assertEqual(self.neo4j.chamadas, [])


if __name__ == "__main__":
    unittest.main()
