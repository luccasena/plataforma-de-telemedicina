import unittest

from services.mapeamento_clinico_service import MapeamentoClinicoService


def medico(postgres_id, nome, crm, uf_crm):
    return {
        "postgres_id": postgres_id,
        "nome": nome,
        "crm": crm,
        "uf_crm": uf_crm,
        "especialidades": [],
    }


class Neo4jRepositoryFake:
    def __init__(self, linha, correlacoes=()):
        self.linha = linha
        self.correlacoes = list(correlacoes)

    def buscar_subgrafo_paciente(self, paciente_id):
        return self.linha

    def buscar_grafo_geral(self, limite):
        return {"linhas": [self.linha]}

    def buscar_diagnosticos_correlacionados_paciente(self, paciente_id, limite):
        return self.correlacoes


class MapeamentoClinicoServiceTests(unittest.TestCase):
    def setUp(self):
        # Mesmo número de CRM em UFs diferentes: só o id do PostgreSQL
        # distingue os dois médicos.
        linha = {
            "p": {"mongo_id": "p1", "nome": "Ana"},
            "atendimentos": [
                {"medico": medico(1, "Dr. João", "123456", "SP"), "rel": {}},
                {"medico": medico(2, "Dra. Fernanda", "123456", "RJ"), "rel": {}},
            ],
            "diagnosticos": [],
            "prescricoes": [],
        }
        self.service = MapeamentoClinicoService(Neo4jRepositoryFake(linha))

    def verificar_medicos(self, grafo):
        self.assertEqual(
            [no["id"] for no in grafo["nodes"] if no["type"] == "Medico"],
            ["medico_1", "medico_2"],
        )
        self.assertEqual(
            [(aresta["source"], aresta["target"]) for aresta in grafo["edges"]],
            [("medico_1", "p1"), ("medico_2", "p1")],
        )

    def test_grafo_do_paciente_identifica_medicos_pelo_id_do_postgres(self):
        self.verificar_medicos(self.service.obter_grafo_paciente("p1"))

    def test_grafo_geral_identifica_medicos_pelo_id_do_postgres(self):
        self.verificar_medicos(self.service.obter_grafo_geral())

    def test_correlacoes_do_paciente_trazem_nivel_e_cadeia(self):
        repositorio = Neo4jRepositoryFake(
            {},
            [
                {
                    "diagnostico_origem_nome": "Enxaqueca",
                    "diagnostico_origem_cid": "G43.0",
                    "diagnostico_correlacionado_nome": "Rinite alérgica",
                    "diagnostico_correlacionado_cid": "J30.4",
                    "nivel": 2,
                    "cadeia": ["Enxaqueca", "Asma", "Rinite alérgica"],
                }
            ],
        )
        servico = MapeamentoClinicoService(repositorio)

        self.assertEqual(
            servico.identificar_diagnosticos_correlacionados("p1"),
            [
                {
                    "diagnostico_origem": "Enxaqueca",
                    "cid_origem": "G43.0",
                    "diagnostico_correlacionado": "Rinite alérgica",
                    "cid_correlacionado": "J30.4",
                    "nivel": 2,
                    "cadeia": ["Enxaqueca", "Asma", "Rinite alérgica"],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
