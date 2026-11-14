"""
Serviço de Mapeamento Clínico e Alertas de Segurança (Grafo).

Centraliza a lógica de negócio da Funcionalidade 3:
- Consulta e estruturação do grafo clínico via Neo4j
- Identificação de relações epidemiológicas e comorbidades
- Alertas de segurança clínica e incompatibilidades medicamentosas
- Geração de contratos de dados estruturados para o frontend Streamlit
"""

import logging
from typing import Any, Dict, List, Optional

from repositories.neo4j_repository import Neo4jRepository, Neo4jError, ServiceUnavailable

logger = logging.getLogger(__name__)


class MapeamentoClinicoService:
    """
    Camada de serviço responsável por fornecer os dados e contratos estruturados
    para a interface Streamlit da funcionalidade de Mapeamento Clínico e Alertas.
    """

    def __init__(self, neo4j_repository: Optional[Neo4jRepository] = None):
        """
        Inicializa o serviço com uma instância do Neo4jRepository.
        Se não fornecido, tenta inicializar automaticamente usando a conexão padrão.
        """
        if neo4j_repository is not None:
            self.repository = neo4j_repository
        else:
            try:
                from config.database import conectar_neo4j
                driver = conectar_neo4j()
                self.repository = Neo4jRepository(driver)
            except Exception as exc:
                logger.warning(
                    "Não foi possível inicializar a conexão padrão do Neo4j automaticamente: %s", exc
                )
                self.repository = Neo4jRepository(None)

    def verificar_status_conexao(self) -> Dict[str, Any]:
        """
        Verifica a saúde da conexão com o banco Neo4j.

        :return: Dicionário no formato:
            {"conectado": bool, "mensagem": str}
        """
        try:
            conectado, mensagem = self.repository.verificar_conexao()
            return {"conectado": conectado, "mensagem": mensagem}
        except Exception as exc:
            logger.error("Erro ao verificar conectividade: %s", exc)
            return {"conectado": False, "mensagem": str(exc)}

    # -------------------------------------------------------------------------
    # Listagem de Pacientes
    # -------------------------------------------------------------------------

    def listar_pacientes(self) -> List[Dict[str, Any]]:
        """
        Recupera a lista de pacientes disponíveis no grafo para seleção na interface.

        :return: Lista de dicionários contendo os dados essenciais do paciente.
        """
        try:
            pacientes = self.repository.buscar_pacientes()
            resultado = []
            for p in pacientes:
                resultado.append(
                    {
                        "id": str(p.get("id")),
                        "nome": p.get("nome", "Sem nome"),
                        "cpf": p.get("cpf", ""),
                        "sexo": p.get("sexo", ""),
                        "data_nascimento": str(p.get("data_nascimento")) if p.get("data_nascimento") else None,
                        "alergias": p.get("alergias") or [],
                        "status": p.get("status", "Ativo"),
                    }
                )
            return resultado
        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao listar pacientes no Neo4j: %s", exc)
            return []

    # -------------------------------------------------------------------------
    # Histórico Clínico do Paciente
    # -------------------------------------------------------------------------

    def obter_historico_clinico_paciente(self, paciente_id: str) -> Dict[str, Any]:
        """
        Recupera o histórico clínico completo de um paciente:
        - Dados cadastrais do paciente
        - Médicos que realizaram atendimento e detalhes das consultas
        - Diagnósticos atribuídos
        - Medicamentos prescritos com posologia

        :param paciente_id: mongo_id do paciente.
        :return: Dicionário com histórico detalhado.
        """
        if not paciente_id:
            return {"paciente": None, "medicos": [], "diagnosticos": [], "medicamentos": []}

        try:
            paciente = self.repository.buscar_paciente_por_id(paciente_id)
            if not paciente:
                return {"paciente": None, "medicos": [], "diagnosticos": [], "medicamentos": []}

            diagnosticos = self.repository.buscar_diagnosticos_paciente(paciente_id)
            medicos = self.repository.buscar_medicos_paciente(paciente_id)
            medicamentos = self.repository.buscar_medicamentos_paciente(paciente_id)

            return {
                "paciente": {
                    "id": str(paciente.get("id")),
                    "nome": paciente.get("nome"),
                    "cpf": paciente.get("cpf"),
                    "sexo": paciente.get("sexo"),
                    "data_nascimento": str(paciente.get("data_nascimento")) if paciente.get("data_nascimento") else None,
                    "alergias": paciente.get("alergias") or [],
                    "status": paciente.get("status"),
                },
                "medicos": medicos,
                "diagnosticos": diagnosticos,
                "medicamentos": medicamentos,
            }
        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao obter histórico clínico do paciente %s: %s", paciente_id, exc)
            return {"paciente": None, "medicos": [], "diagnosticos": [], "medicamentos": [], "erro": str(exc)}

    # -------------------------------------------------------------------------
    # Contrato Visual do Grafo (Nodes e Edges)
    # -------------------------------------------------------------------------

    def obter_grafo_paciente(self, paciente_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Gera a estrutura de nós e arestas do subgrafo centrado no paciente selecionado.
        Atende ao contrato de integração para visualização (Graph Network).

        Contrato de saída:
        {
            "nodes": [
                {"id": "...", "label": "...", "type": "Paciente"|"Medico"|"Diagnostico"|"Medicamento"}
            ],
            "edges": [
                {"source": "...", "target": "...", "relationship": "ATENDEU"|"DIAGNOSTICADO_COM"|"RECEBEU_PRESCRICAO"}
            ]
        }
        """
        if not paciente_id:
            return {"nodes": [], "edges": []}

        try:
            dados = self.repository.buscar_subgrafo_paciente(paciente_id)
            if not dados or not dados.get("p"):
                return {"nodes": [], "edges": []}

            nos_map: Dict[str, Dict[str, Any]] = {}
            arestas_set = set()
            arestas: List[Dict[str, Any]] = []

            # 1. Nó do Paciente
            paciente = dados["p"]
            p_id = str(paciente.get("mongo_id") or paciente.get("id"))
            p_label = paciente.get("nome", "Paciente")
            nos_map[p_id] = {
                "id": p_id,
                "label": p_label,
                "type": "Paciente",
                "properties": {
                    "cpf": paciente.get("cpf"),
                    "status": paciente.get("status"),
                    "alergias": paciente.get("alergias", []),
                },
            }

            # 2. Atendimentos Médicos (:Medico)-[:ATENDEU]->(:Paciente)
            for item in dados.get("atendimentos", []):
                medico = item.get("medico")
                rel = item.get("rel")
                if not medico:
                    continue

                m_raw_id = medico.get("mongo_id") or medico.get("id")
                m_id = f"medico_{m_raw_id}" if m_raw_id else f"medico_{medico.get('crm', 'desconhecido')}"
                if m_id not in nos_map:
                    nos_map[m_id] = {
                        "id": m_id,
                        "label": medico.get("nome", "Médico"),
                        "type": "Medico",
                        "properties": {
                            "crm": medico.get("crm"),
                            "uf_crm": medico.get("uf_crm"),
                            "especialidades": medico.get("especialidades", []),
                        },
                    }

                chave_aresta = (m_id, p_id, "ATENDEU")
                if chave_aresta not in arestas_set:
                    arestas_set.add(chave_aresta)
                    props_aresta = dict(rel) if rel else {}
                    arestas.append(
                        {
                            "source": m_id,
                            "target": p_id,
                            "relationship": "ATENDEU",
                            "properties": props_aresta,
                        }
                    )

            # 3. Diagnósticos (:Paciente)-[:DIAGNOSTICADO_COM]->(:Diagnostico)
            for item in dados.get("diagnosticos", []):
                diag = item.get("diagnostico")
                rel = item.get("rel")
                if not diag:
                    continue

                d_codigo = diag.get("codigo") or diag.get("cid") or diag.get("nome")
                d_id = f"diag_{d_codigo}"
                rotulo_diag = f"{diag.get('nome')} ({diag.get('cid')})" if diag.get("cid") else diag.get("nome", "Diagnóstico")

                if d_id not in nos_map:
                    nos_map[d_id] = {
                        "id": d_id,
                        "label": rotulo_diag,
                        "type": "Diagnostico",
                        "properties": {
                            "codigo": diag.get("codigo"),
                            "cid": diag.get("cid"),
                            "nome": diag.get("nome"),
                        },
                    }

                chave_aresta = (p_id, d_id, "DIAGNOSTICADO_COM")
                if chave_aresta not in arestas_set:
                    arestas_set.add(chave_aresta)
                    props_aresta = dict(rel) if rel else {}
                    arestas.append(
                        {
                            "source": p_id,
                            "target": d_id,
                            "relationship": "DIAGNOSTICADO_COM",
                            "properties": props_aresta,
                        }
                    )

            # 4. Medicamentos (:Paciente)-[:RECEBEU_PRESCRICAO]->(:Medicamento)
            lista_prescricoes = dados.get("prescricoes") or dados.get("medicamentos") or []
            for item in lista_prescricoes:
                medicamento = item.get("medicamento")
                rel = item.get("rel")
                if not medicamento:
                    continue

                m_nome = medicamento.get("nome")
                med_id = f"med_{m_nome}"

                if med_id not in nos_map:
                    nos_map[med_id] = {
                        "id": med_id,
                        "label": m_nome,
                        "type": "Medicamento",
                        "properties": {
                            "nome": m_nome,
                        },
                    }

                chave_aresta = (p_id, med_id, "RECEBEU_PRESCRICAO")
                if chave_aresta not in arestas_set:
                    arestas_set.add(chave_aresta)
                    props_aresta = dict(rel) if rel else {}
                    arestas.append(
                        {
                            "source": p_id,
                            "target": med_id,
                            "relationship": "RECEBEU_PRESCRICAO",
                            "properties": props_aresta,
                        }
                    )

            return {
                "nodes": list(nos_map.values()),
                "edges": arestas,
            }

        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao montar subgrafo do paciente %s: %s", paciente_id, exc)
            return {"nodes": [], "edges": []}

    def obter_grafo_geral(self, limite: int = 300) -> Dict[str, List[Dict[str, Any]]]:
        """
        Gera a estrutura completa de nós e arestas de toda a clínica (visão global).
        Permite ao frontend renderizar o grafo populacional e epidemiológico.
        """
        try:
            resposta = self.repository.buscar_grafo_geral(limite=limite)
            linhas = resposta.get("linhas", [])

            nos_map: Dict[str, Dict[str, Any]] = {}
            arestas_set = set()
            arestas: List[Dict[str, Any]] = []

            for dados in linhas:
                paciente = dados.get("p")
                if not paciente:
                    continue

                p_id = str(paciente.get("mongo_id") or paciente.get("id"))
                if p_id not in nos_map:
                    nos_map[p_id] = {
                        "id": p_id,
                        "label": paciente.get("nome", "Paciente"),
                        "type": "Paciente",
                        "properties": {
                            "status": paciente.get("status"),
                            "alergias": paciente.get("alergias", []),
                        },
                    }

                for item in dados.get("atendimentos", []):
                    medico = item.get("medico")
                    rel = item.get("rel")
                    if not medico:
                        continue
                    m_raw_id = medico.get("mongo_id") or medico.get("id")
                    m_id = f"medico_{m_raw_id}" if m_raw_id else f"medico_{medico.get('crm', 'desconhecido')}"
                    if m_id not in nos_map:
                        nos_map[m_id] = {
                            "id": m_id,
                            "label": medico.get("nome", "Médico"),
                            "type": "Medico",
                            "properties": {
                                "crm": medico.get("crm"),
                                "uf_crm": medico.get("uf_crm"),
                            },
                        }
                    chave = (m_id, p_id, "ATENDEU")
                    if chave not in arestas_set:
                        arestas_set.add(chave)
                        arestas.append({"source": m_id, "target": p_id, "relationship": "ATENDEU"})

                for item in dados.get("diagnosticos", []):
                    diag = item.get("diagnostico")
                    if not diag:
                        continue
                    d_codigo = diag.get("codigo") or diag.get("cid") or diag.get("nome")
                    d_id = f"diag_{d_codigo}"
                    if d_id not in nos_map:
                        rotulo = f"{diag.get('nome')} ({diag.get('cid')})" if diag.get("cid") else diag.get("nome", "Diagnóstico")
                        nos_map[d_id] = {
                            "id": d_id,
                            "label": rotulo,
                            "type": "Diagnostico",
                            "properties": {"cid": diag.get("cid")},
                        }
                    chave = (p_id, d_id, "DIAGNOSTICADO_COM")
                    if chave not in arestas_set:
                        arestas_set.add(chave)
                        arestas.append({"source": p_id, "target": d_id, "relationship": "DIAGNOSTICADO_COM"})

                lista_prescricoes = dados.get("prescricoes") or dados.get("medicamentos") or []
                for item in lista_prescricoes:
                    med = item.get("medicamento")
                    if not med:
                        continue
                    m_nome = med.get("nome")
                    med_id = f"med_{m_nome}"
                    if med_id not in nos_map:
                        nos_map[med_id] = {
                            "id": med_id,
                            "label": m_nome,
                            "type": "Medicamento",
                            "properties": {"nome": m_nome},
                        }
                    chave = (p_id, med_id, "RECEBEU_PRESCRICAO")
                    if chave not in arestas_set:
                        arestas_set.add(chave)
                        arestas.append({"source": p_id, "target": med_id, "relationship": "RECEBEU_PRESCRICAO"})

            return {
                "nodes": list(nos_map.values()),
                "edges": arestas,
            }

        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao montar grafo geral: %s", exc)
            return {"nodes": [], "edges": []}

    # -------------------------------------------------------------------------
    # Interações Medicamentosas
    # -------------------------------------------------------------------------

    def identificar_interacoes_medicamentosas(self, paciente_id: str) -> List[Dict[str, Any]]:
        """
        Identifica potenciais interações ou incompatibilidades entre os medicamentos prescritos
        ao paciente selecionado, com base nas relações registradas no grafo.

        :param paciente_id: mongo_id do paciente.
        :return: Lista de interações encontradas.
        """
        if not paciente_id:
            return []

        try:
            interacoes = self.repository.buscar_interacoes_medicamentosas_paciente(paciente_id)
            resultado = []
            for item in interacoes:
                resultado.append(
                    {
                        "medicamento_1": item.get("medicamento_1"),
                        "medicamento_2": item.get("medicamento_2"),
                        "tipo_relacao": item.get("tipo_relacao", "INTERAGE_COM"),
                        "severidade": item.get("severidade", "alta"),
                        "descricao": item.get("descricao", "Interação medicamentosa registrada na base."),
                    }
                )
            return resultado
        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao identificar interações medicamentosas do paciente %s: %s", paciente_id, exc)
            return []

    # -------------------------------------------------------------------------
    # Diagnósticos Correlacionados
    # -------------------------------------------------------------------------

    def identificar_diagnosticos_correlacionados(
        self, paciente_id: Optional[str] = None, limite: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Identifica diagnósticos correlacionados no grafo com base na coocorrência epidemiológica
        entre os pacientes da base. Se um paciente_id for informado, busca correlações específicas
        aos diagnósticos daquele paciente. Caso contrário, retorna os diagnósticos mais correlacionados
        da base geral.

        :param paciente_id: Opcional. mongo_id do paciente de referência.
        :param limite: Número máximo de correlações a retornar.
        :return: Lista de dicionários com as correlações diagnósticas identificadas.
        """
        try:
            if paciente_id:
                correlacoes = self.repository.buscar_diagnosticos_correlacionados_paciente(paciente_id, limite=limite)
                resultado = []
                for item in correlacoes:
                    resultado.append(
                        {
                            "diagnostico_origem": item.get("diagnostico_origem_nome"),
                            "cid_origem": item.get("diagnostico_origem_cid"),
                            "diagnostico_correlacionado": item.get("diagnostico_correlacionado_nome"),
                            "cid_correlacionado": item.get("diagnostico_correlacionado_cid"),
                            "total_pacientes_compartilhados": item.get("total_pacientes_compartilhados", 0),
                        }
                    )
                return resultado
            else:
                correlacoes = self.repository.buscar_todos_diagnosticos_correlacionados(limite=limite)
                resultado = []
                for item in correlacoes:
                    resultado.append(
                        {
                            "diagnostico_1": item.get("nome_1"),
                            "cid_1": item.get("cid_1"),
                            "diagnostico_2": item.get("nome_2"),
                            "cid_2": item.get("cid_2"),
                            "total_pacientes_compartilhados": item.get("total_pacientes", 0),
                            "pacientes_nomes": item.get("pacientes_nomes", []),
                        }
                    )
                return resultado
        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao identificar diagnósticos correlacionados: %s", exc)
            return []

    # -------------------------------------------------------------------------
    # Alertas de Segurança Clínicos (Contrato Padronizado)
    # -------------------------------------------------------------------------

    def obter_alertas_seguranca(self, paciente_id: str) -> Dict[str, Any]:
        """
        Consolida todos os alertas de segurança clínica para o paciente selecionado,
        respeitando estritamente os dados e relações presentes no projeto:

        1. Alergia declarada vs Medicamento prescrito
        2. Interações medicamentosas explícitas no grafo
        3. Contraindicações entre medicamento prescrito e diagnóstico atribuído
        4. Duplicidade / redundância de prescrições ativas

        Contrato de saída:
        {
            "patient_id": "identificador",
            "alerts": [
                {
                    "type": "alergia_medicamentosa"|"interacao_medicamentosa"|"contraindicacao"|"duplicidade_prescricao",
                    "severity": "alta"|"media"|"baixa",
                    "title": "Título do alerta",
                    "description": "Descrição fundamentada nos dados do grafo"
                }
            ]
        }
        """
        if not paciente_id:
            return {"patient_id": "", "alerts": []}

        try:
            alertas: List[Dict[str, Any]] = []

            # 1. Alergias vs Medicamentos prescritos
            alergias_conflitantes = self.repository.buscar_alergias_medicamentosas_paciente(paciente_id)
            for item in alergias_conflitantes:
                med = item.get("medicamento_prescrito")
                alergia = item.get("alergia_declarada")
                alertas.append(
                    {
                        "type": "alergia_medicamentosa",
                        "severity": "alta",
                        "title": f"Alergia Medicamentosa Detectada: {med}",
                        "description": (
                            f"O paciente possui alergia declarada a '{alergia}' "
                            f"e recebeu prescrição para o medicamento '{med}'."
                        ),
                    }
                )

            # 2. Interações medicamentosas explícitas no grafo
            interacoes = self.repository.buscar_interacoes_medicamentosas_paciente(paciente_id)
            for item in interacoes:
                m1 = item.get("medicamento_1")
                m2 = item.get("medicamento_2")
                desc = item.get("descricao")
                severidade = item.get("severidade", "alta")
                alertas.append(
                    {
                        "type": "interacao_medicamentosa",
                        "severity": severidade,
                        "title": f"Interação Medicamentosa: {m1} e {m2}",
                        "description": desc or f"Incompatibilidade clínica registrada no grafo entre {m1} e {m2}.",
                    }
                )

            # 3. Contraindicações medicamento vs diagnóstico no grafo
            contraindicacoes = self.repository.buscar_contraindicacoes_diagnostico_paciente(paciente_id)
            for item in contraindicacoes:
                med = item.get("medicamento")
                diag = item.get("diagnostico_nome")
                cid = item.get("diagnostico_cid")
                motivo = item.get("motivo")
                severidade = item.get("severidade", "alta")
                rotulo_diag = f"{diag} (CID: {cid})" if cid else diag
                alertas.append(
                    {
                        "type": "contraindicacao",
                        "severity": severidade,
                        "title": f"Contraindicação Clínica: {med} para {diag}",
                        "description": motivo or (
                            f"O medicamento '{med}' possui contraindicação registrada no grafo "
                            f"para o diagnóstico de '{rotulo_diag}'."
                        ),
                    }
                )

            # 4. Duplicidade / redundância de prescrições
            duplicidades = self.repository.buscar_duplicidades_prescricao_paciente(paciente_id)
            for item in duplicidades:
                med = item.get("medicamento")
                total = item.get("total_prescricoes", 2)
                alertas.append(
                    {
                        "type": "duplicidade_prescricao",
                        "severity": "media",
                        "title": f"Prescrição Duplicada: {med}",
                        "description": (
                            f"O paciente possui {total} prescrições registradas para o "
                            f"medicamento '{med}'. Recomenda-se conferir a posologia e vigência."
                        ),
                    }
                )

            return {
                "patient_id": str(paciente_id),
                "alerts": alertas,
            }

        except (Neo4jError, ServiceUnavailable) as exc:
            logger.error("Erro ao consolidar alertas de segurança para paciente %s: %s", paciente_id, exc)
            return {"patient_id": str(paciente_id), "alerts": [], "erro": str(exc)}
