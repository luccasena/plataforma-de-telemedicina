"""
Repositório de acesso ao Neo4j para a plataforma de telemedicina.

Responsável por centralizar e executar consultas Cypher parametrizadas para
mapeamento clínico, relacionamentos epidemiológicos e alertas de segurança.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

try:
    from neo4j import Driver, GraphDatabase
    from neo4j.exceptions import Neo4jError, ServiceUnavailable
except ImportError:  # pragma: no cover
    Driver = Any  # type: ignore
    GraphDatabase = None  # type: ignore

    class Neo4jError(Exception):
        pass

    class ServiceUnavailable(Exception):
        pass

logger = logging.getLogger(__name__)


class Neo4jRepository:
    """
    Camada de acesso a dados para o banco orientado a grafos Neo4j.
    Centraliza todas as consultas Cypher parametrizadas e o gerenciamento de sessões.
    """

    def __init__(self, driver: Optional[Driver] = None, database_name: str = "neo4j"):
        """
        Inicializa o repositório com uma instância de driver Neo4j.

        :param driver: Instância de neo4j.Driver configurada.
        :param database_name: Nome do banco de dados (padrão: "neo4j").
        """
        self.driver = driver
        self.database_name = database_name

    def verificar_conexao(self) -> Tuple[bool, str]:
        """
        Verifica se a conectividade com o Neo4j está ativa.

        :return: Tupla (conectado: bool, mensagem: str)
        """
        if not self.driver:
            return False, "Driver do Neo4j não inicializado."

        try:
            self.driver.verify_connectivity()
            return True, "Conexão com o Neo4j estabelecida com sucesso."
        except ServiceUnavailable as exc:
            logger.error("Falha de conexão com Neo4j: %s", exc)
            return False, f"Serviço Neo4j indisponível: {exc}"
        except Exception as exc:
            logger.error("Erro inesperado ao verificar conexão com Neo4j: %s", exc)
            return False, f"Erro ao conectar ao Neo4j: {exc}"

    def _executar_leitura(self, cypher: str, parametros: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Executa uma consulta Cypher parametrizada em transação de leitura.

        :param cypher: String contendo a consulta Cypher.
        :param parametros: Dicionário de parâmetros Cypher.
        :return: Lista de registros como dicionários.
        """
        if not self.driver:
            raise ServiceUnavailable("Driver do Neo4j não está disponível.")

        parametros = parametros or {}
        try:
            with self.driver.session(database=self.database_name) as session:
                resultado = session.run(cypher, parametros)
                return [dict(registro) for registro in resultado]
        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao executar consulta Cypher: %s | Query: %s", exc, cypher)
            raise

    # -------------------------------------------------------------------------
    # 1. Consultas de Pacientes
    # -------------------------------------------------------------------------

    def buscar_pacientes(self) -> List[Dict[str, Any]]:
        """
        Recupera todos os pacientes cadastrados no grafo.
        """
        cypher = """
        MATCH (p:Paciente)
        RETURN p.mongo_id AS id,
               p.nome AS nome,
               p.cpf AS cpf,
               p.sexo AS sexo,
               p.data_nascimento AS data_nascimento,
               coalesce(p.alergias, []) AS alergias,
               p.status AS status
        ORDER BY p.nome ASC
        """
        return self._executar_leitura(cypher)

    def buscar_paciente_por_id(self, paciente_id: str) -> Optional[Dict[str, Any]]:
        """
        Recupera os dados de um paciente específico pelo seu mongo_id.
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})
        RETURN p.mongo_id AS id,
               p.nome AS nome,
               p.cpf AS cpf,
               p.sexo AS sexo,
               p.data_nascimento AS data_nascimento,
               coalesce(p.alergias, []) AS alergias,
               p.status AS status
        LIMIT 1
        """
        resultados = self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})
        return resultados[0] if resultados else None

    # -------------------------------------------------------------------------
    # 2. Diagnósticos e Médicos
    # -------------------------------------------------------------------------

    def buscar_diagnosticos_paciente(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Recupera os diagnósticos associados a um paciente (:Paciente)-[:DIAGNOSTICADO_COM]->(:Diagnostico).
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})-[r:DIAGNOSTICADO_COM]->(d:Diagnostico)
        RETURN d.codigo AS codigo,
               d.nome AS nome,
               d.cid AS cid,
               r.consulta_id AS consulta_id,
               r.medico_id AS medico_id
        ORDER BY d.nome ASC
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})

    def buscar_medicos_paciente(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Recupera os médicos que atenderam o paciente (:Medico)-[:ATENDEU]->(:Paciente).
        """
        cypher = """
        MATCH (m:Medico)-[r:ATENDEU]->(p:Paciente {mongo_id: $paciente_id})
        RETURN m.mongo_id AS id,
               m.nome AS nome,
               m.crm AS crm,
               m.uf_crm AS uf_crm,
               coalesce(m.especialidades, []) AS especialidades,
               m.status AS status,
               r.consulta_id AS consulta_id,
               r.queixa_principal AS queixa_principal,
               coalesce(r.sintomas, []) AS sintomas
        ORDER BY m.nome ASC
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})

    # -------------------------------------------------------------------------
    # 3. Medicamentos Associados
    # -------------------------------------------------------------------------

    def buscar_medicamentos_paciente(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Recupera os medicamentos prescritos ao paciente (:Paciente)-[:RECEBEU_PRESCRICAO]->(:Medicamento).
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})-[r:RECEBEU_PRESCRICAO]->(m:Medicamento)
        RETURN m.nome AS nome,
               r.dosagem AS dosagem,
               r.frequencia AS frequencia,
               r.orientacoes AS orientacoes
        ORDER BY m.nome ASC
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})

    # -------------------------------------------------------------------------
    # 4. Alertas de Segurança e Incompatibilidades
    # -------------------------------------------------------------------------

    def buscar_alergias_medicamentosas_paciente(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Identifica conflito entre alergias cadastradas no nó Paciente e medicamentos
        prescritos (:Paciente)-[:RECEBEU_PRESCRICAO]->(:Medicamento).
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})-[r:RECEBEU_PRESCRICAO]->(m:Medicamento)
        UNWIND p.alergias AS alergia
        WITH p, m, r, alergia,
             toLower(trim(alergia)) AS alergia_norm,
             toLower(trim(m.nome)) AS med_norm
        WHERE alergia_norm <> '' AND (med_norm CONTAINS alergia_norm OR alergia_norm CONTAINS med_norm)
        RETURN p.mongo_id AS paciente_id,
               p.nome AS paciente_nome,
               alergia AS alergia_declarada,
               m.nome AS medicamento_prescrito,
               r.dosagem AS dosagem,
               r.frequencia AS frequencia,
               r.orientacoes AS orientacoes
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})

    def buscar_interacoes_medicamentosas_paciente(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Identifica interações ou incompatibilidades entre pares de medicamentos prescritos
        ao mesmo paciente caso haja relacionamentos :INTERAGE_COM ou :INCOMPATIVEL_COM na base.
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})-[:RECEBEU_PRESCRICAO]->(m1:Medicamento)
        MATCH (p)-[:RECEBEU_PRESCRICAO]->(m2:Medicamento)
        MATCH (m1)-[r:INTERAGE_COM|INCOMPATIVEL_COM]-(m2)
        WHERE id(m1) < id(m2)
        RETURN m1.nome AS medicamento_1,
               m2.nome AS medicamento_2,
               type(r) AS tipo_relacao,
               coalesce(r.severidade, 'alta') AS severidade,
               coalesce(r.descricao, 'Interação medicamentosa registrada no grafo.') AS descricao
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})

    def buscar_contraindicacoes_diagnostico_paciente(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Identifica contraindicações registradas no grafo entre medicamentos prescritos
        e diagnósticos atribuídos ao mesmo paciente (:Medicamento)-[:CONTRAINDICADO_PARA]->(:Diagnostico).
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})-[:DIAGNOSTICADO_COM]->(d:Diagnostico)
        MATCH (p)-[:RECEBEU_PRESCRICAO]->(m:Medicamento)
        MATCH (m)-[r:CONTRAINDICADO_PARA|CONTRAINDICADO_COM]-(d)
        RETURN m.nome AS medicamento,
               d.codigo AS diagnostico_codigo,
               d.nome AS diagnostico_nome,
               d.cid AS diagnostico_cid,
               type(r) AS tipo_relacao,
               coalesce(r.severidade, 'alta') AS severidade,
               coalesce(r.motivo, 'Medicamento contraindicado para o diagnóstico conforme registro do grafo.') AS motivo
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})

    def buscar_duplicidades_prescricao_paciente(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Identifica medicamentos prescritos mais de uma vez para o mesmo paciente.
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})-[r:RECEBEU_PRESCRICAO]->(m:Medicamento)
        WITH m.nome AS medicamento, count(r) AS total_prescricoes, collect(r) AS prescricoes
        WHERE total_prescricoes > 1
        RETURN medicamento, total_prescricoes, prescricoes
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})

    # -------------------------------------------------------------------------
    # 5. Diagnósticos Correlacionados
    # -------------------------------------------------------------------------

    def buscar_diagnosticos_correlacionados_paciente(self, paciente_id: str, limite: int = 10) -> List[Dict[str, Any]]:
        """
        Identifica diagnósticos correlacionados aos diagnósticos do paciente especificado,
        baseando-se na coocorrência epidemiológica entre outros pacientes no grafo.
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})-[:DIAGNOSTICADO_COM]->(d1:Diagnostico)
        MATCH (outro:Paciente)-[:DIAGNOSTICADO_COM]->(d1)
        MATCH (outro)-[:DIAGNOSTICADO_COM]->(d2:Diagnostico)
        WHERE d1.codigo <> d2.codigo
        WITH d1, d2, count(DISTINCT outro) AS total_pacientes_compartilhados
        RETURN d1.codigo AS diagnostico_origem_codigo,
               d1.nome AS diagnostico_origem_nome,
               d1.cid AS diagnostico_origem_cid,
               d2.codigo AS diagnostico_correlacionado_codigo,
               d2.nome AS diagnostico_correlacionado_nome,
               d2.cid AS diagnostico_correlacionado_cid,
               total_pacientes_compartilhados
        ORDER BY total_pacientes_compartilhados DESC, d2.nome ASC
        LIMIT $limite
        """
        return self._executar_leitura(cypher, {"paciente_id": str(paciente_id), "limite": limite})

    def buscar_todos_diagnosticos_correlacionados(self, limite: int = 20) -> List[Dict[str, Any]]:
        """
        Recupera as correlações diagnósticas gerais no grafo populacional:
        1. Por coocorrência entre pacientes compartilhados.
        2. Por relacionamentos explícitos :CORRELACIONADO_COM se existirem.
        """
        cypher = """
        MATCH (p:Paciente)-[:DIAGNOSTICADO_COM]->(d1:Diagnostico)
        MATCH (p)-[:DIAGNOSTICADO_COM]->(d2:Diagnostico)
        WHERE id(d1) < id(d2)
        WITH d1, d2, count(DISTINCT p) AS total_pacientes, collect(DISTINCT p.nome) AS pacientes_nomes
        RETURN d1.codigo AS codigo_1,
               d1.nome AS nome_1,
               d1.cid AS cid_1,
               d2.codigo AS codigo_2,
               d2.nome AS nome_2,
               d2.cid AS cid_2,
               total_pacientes,
               pacientes_nomes
        ORDER BY total_pacientes DESC, d1.nome ASC
        LIMIT $limite
        """
        return self._executar_leitura(cypher, {"limite": limite})

    # -------------------------------------------------------------------------
    # 6. Dados Brutos para Visualização do Grafo
    # -------------------------------------------------------------------------

    def buscar_subgrafo_paciente(self, paciente_id: str) -> Dict[str, Any]:
        """
        Recupera todos os nós e relacionamentos diretamente ligados a um paciente específico,
        incluindo médicos atendentes, diagnósticos e medicamentos prescritos.
        """
        cypher = """
        MATCH (p:Paciente {mongo_id: $paciente_id})
        OPTIONAL MATCH (m:Medico)-[r_atendeu:ATENDEU]->(p)
        OPTIONAL MATCH (p)-[r_diag:DIAGNOSTICADO_COM]->(d:Diagnostico)
        OPTIONAL MATCH (p)-[r_presc:RECEBEU_PRESCRICAO]->(med:Medicamento)
        RETURN p,
               collect(DISTINCT {medico: m, rel: r_atendeu}) AS atendimentos,
               collect(DISTINCT {diagnostico: d, rel: r_diag}) AS diagnosticos,
               collect(DISTINCT {medicamento: med, rel: r_presc}) AS prescricoes
        """
        resultados = self._executar_leitura(cypher, {"paciente_id": str(paciente_id)})
        return resultados[0] if resultados else {}

    def buscar_grafo_geral(self, limite: int = 500) -> Dict[str, Any]:
        """
        Recupera nós e arestas de toda a base clínica para visualização global.
        """
        cypher = """
        MATCH (p:Paciente)
        OPTIONAL MATCH (m:Medico)-[r_atendeu:ATENDEU]->(p)
        OPTIONAL MATCH (p)-[r_diag:DIAGNOSTICADO_COM]->(d:Diagnostico)
        OPTIONAL MATCH (p)-[r_presc:RECEBEU_PRESCRICAO]->(med:Medicamento)
        RETURN p,
               collect(DISTINCT {medico: m, rel: r_atendeu}) AS atendimentos,
               collect(DISTINCT {diagnostico: d, rel: r_diag}) AS diagnosticos,
               collect(DISTINCT {medicamento: med, rel: r_presc}) AS prescricoes
        LIMIT $limite
        """
        linhas = self._executar_leitura(cypher, {"limite": limite})
        return {"linhas": linhas}

