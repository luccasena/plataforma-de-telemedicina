
import streamlit as st
import streamlit.components.v1 as components

from pyvis.network import Network

from services.mapeamento_clinico_service import MapeamentoClinicoService


@st.cache_resource
def obter_servico():
    return MapeamentoClinicoService()


def incluir_alergias_no_grafo(dados_grafo):
    """
    Adiciona alergias como nós apenas para visualização.
    Não modifica os dados armazenados no Neo4j.
    """

    nos = list(dados_grafo.get("nodes", []))
    arestas = list(dados_grafo.get("edges", []))

    for no in dados_grafo.get("nodes", []):
        if no.get("type") != "Paciente":
            continue

        paciente_id = str(no.get("id", ""))
        propriedades = no.get("properties") or {}
        alergias = propriedades.get("alergias") or []

        for alergia in alergias:
            nome_alergia = str(alergia).strip()

            if not nome_alergia:
                continue

            alergia_id = (
                f"alergia_{paciente_id}_{nome_alergia.casefold()}"
            )

            existe_no = any(
                item.get("id") == alergia_id
                for item in nos
            )

            if not existe_no:
                nos.append({
                    "id": alergia_id,
                    "label": nome_alergia,
                    "type": "Alergia",
                    "properties": {
                        "nome": nome_alergia
                    }
                })

            existe_aresta = any(
                aresta.get("source") == paciente_id
                and aresta.get("target") == alergia_id
                and aresta.get("relationship") == "POSSUI_ALERGIA"
                for aresta in arestas
            )

            if not existe_aresta:
                arestas.append({
                    "source": paciente_id,
                    "target": alergia_id,
                    "relationship": "POSSUI_ALERGIA",
                    "properties": {}
                })

    return {
        "nodes": nos,
        "edges": arestas
    }


def exibir_grafo(dados_grafo):
    nos = dados_grafo.get("nodes", [])
    arestas = dados_grafo.get("edges", [])

    if not nos:
        st.info("Não há dados suficientes para montar o grafo.")
        return

    grafo = Network(
        height="600px",
        width="100%",
        directed=True,
        bgcolor="#FFFFFF",
        font_color="#222222",
        cdn_resources="in_line"
    )

    cores = {
        "Paciente": "#2563EB",
        "Medico": "#16A34A",
        "Diagnostico": "#D97706",
        "Medicamento": "#9333EA",
        "Alergia": "#DC2626"
    }

    for no in nos:
        propriedades = no.get("properties", {})

        detalhes = "\n".join(
            f"{chave}: {valor}"
            for chave, valor in propriedades.items()
            if valor is not None
        )

        grafo.add_node(
            no["id"],
            label=no.get("label", "Sem nome"),
            title=detalhes or no.get("label", ""),
            color=cores.get(no.get("type"), "#64748B"),
            shape="diamond" if no.get("type") == "Alergia" else "dot",
            size=25
        )

    for aresta in arestas:
        grafo.add_edge(
            aresta["source"],
            aresta["target"],
            label=aresta.get("relationship", ""),
            title=aresta.get("relationship", ""),
            arrows="to"
        )

    grafo.set_options("""
    {
        "physics": {
            "enabled": true,
            "stabilization": {
                "iterations": 150
            }
        },
        "interaction": {
            "hover": true,
            "navigationButtons": true,
            "keyboard": true
        },
        "edges": {
            "smooth": true,
            "font": {
                "size": 12,
                "align": "middle"
            }
        }
    }
    """)

    html = grafo.generate_html()

    components.html(
        html,
        height=620,
        scrolling=True
    )


st.subheader("Mapeamento Clínico")

st.write(
    "Visualização de pacientes, históricos clínicos e relações médicas."
)

servico = obter_servico()


# Verificação da conexão
st.markdown("### Conexão com o Neo4j")

try:
    status = servico.verificar_status_conexao()

    if status.get("conectado"):
        st.success("Conexão com o Neo4j estabelecida.")
    else:
        st.error(
            status.get("mensagem", "Não foi possível conectar ao Neo4j.")
        )

except Exception as erro:
    st.error(f"Erro ao verificar conexão: {erro}")


st.divider()


# Listagem de pacientes
st.markdown("### Pacientes cadastrados")

try:
    pacientes = servico.listar_pacientes()

    if pacientes:
        st.write(f"Total de pacientes encontrados: {len(pacientes)}")

        paciente_selecionado = st.selectbox(
            "Selecione um paciente:",
            pacientes,
            format_func=lambda paciente: paciente.get(
                "nome", "Paciente sem nome"
            )
        )

        if paciente_selecionado:

            # Dados do paciente
            st.markdown("#### Dados do paciente")

            col1, col2 = st.columns(2)

            with col1:
                st.write(
                    "**Nome:**",
                    paciente_selecionado.get("nome", "Não informado")
                )

                st.write(
                    "**Sexo:**",
                    paciente_selecionado.get("sexo", "Não informado")
                )

            with col2:
                st.write(
                    "**Data de nascimento:**",
                    paciente_selecionado.get(
                        "data_nascimento", "Não informada"
                    )
                )

                st.write(
                    "**Status:**",
                    paciente_selecionado.get("status", "Não informado")
                )

            st.divider()

            # Histórico clínico
            st.markdown("### Histórico Clínico")

            historico = servico.obter_historico_clinico_paciente(
                paciente_selecionado.get("id")
            )

            if historico.get("erro"):
                st.error(
                    f"Erro ao carregar histórico: {historico['erro']}"
                )

            elif historico.get("paciente") is None:
                st.warning(
                    "Não foi possível localizar o histórico deste paciente."
                )

            else:
                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric(
                        "Médicos",
                        len(historico.get("medicos", []))
                    )

                with col2:
                    st.metric(
                        "Diagnósticos",
                        len(historico.get("diagnosticos", []))
                    )

                with col3:
                    st.metric(
                        "Medicamentos",
                        len(historico.get("medicamentos", []))
                    )

                # Médicos
                st.markdown("#### Médicos")

                if historico.get("medicos"):
                    for medico in historico["medicos"]:
                        with st.expander("Ver atendimento médico"):
                            st.json(medico)
                else:
                    st.info("Nenhum médico encontrado no histórico.")

                # Diagnósticos
                st.markdown("#### Diagnósticos")

                if historico.get("diagnosticos"):
                    for diagnostico in historico["diagnosticos"]:
                        with st.expander("Ver diagnóstico"):
                            st.json(diagnostico)
                else:
                    st.info("Nenhum diagnóstico encontrado.")

                # Medicamentos
                st.markdown("#### Medicamentos")

                if historico.get("medicamentos"):
                    for medicamento in historico["medicamentos"]:
                        with st.expander("Ver medicamento"):
                            st.json(medicamento)
                else:
                    st.info("Nenhum medicamento encontrado.")

                # Alergias
                st.markdown("#### Alergias registradas")

                alergias = historico["paciente"].get("alergias", [])

                if alergias:
                    for alergia in alergias:
                        st.warning(str(alergia))
                else:
                    st.info("Nenhuma alergia registrada no cadastro.")

                st.divider()

                # Alertas de segurança
                st.markdown("### Alertas de Segurança")

                resultado_alertas = servico.obter_alertas_seguranca(
                    paciente_selecionado.get("id")
                )

                if resultado_alertas.get("erro"):
                    st.error(
                        f"Erro ao carregar alertas: "
                        f"{resultado_alertas['erro']}"
                    )

                else:
                    alertas = resultado_alertas.get("alerts", [])

                    if alertas:
                        st.write(
                            f"Total de alertas encontrados: {len(alertas)}"
                        )

                        for alerta in alertas:
                            gravidade = alerta.get(
                                "severity", ""
                            ).lower()

                            titulo = alerta.get(
                                "title", "Alerta de segurança"
                            )

                            descricao = alerta.get(
                                "description", "Sem descrição."
                            )

                            if gravidade == "alta":
                                st.error(
                                    f"**{titulo}**\n\n{descricao}"
                                )

                            elif gravidade == "media":
                                st.warning(
                                    f"**{titulo}**\n\n{descricao}"
                                )

                            else:
                                st.info(
                                    f"**{titulo}**\n\n{descricao}"
                                )

                    else:
                        st.info(
                            "Nenhum alerta de segurança foi retornado "
                            "pelo serviço para este paciente."
                        )

                st.divider()

                # Diagnósticos correlacionados
                st.markdown("### Diagnósticos Correlacionados")

                correlacoes = servico.identificar_diagnosticos_correlacionados(
                    paciente_id=paciente_selecionado.get("id"),
                    limite=10
                )

                if correlacoes:
                    st.write(
                        f"Total de correlações encontradas: {len(correlacoes)}"
                    )

                    for correlacao in correlacoes:
                        origem = correlacao.get(
                            "diagnostico_origem", "Não informado"
                        )

                        cid_origem = correlacao.get(
                            "cid_origem", "Não informado"
                        )

                        relacionado = correlacao.get(
                            "diagnostico_correlacionado", "Não informado"
                        )

                        cid_relacionado = correlacao.get(
                            "cid_correlacionado", "Não informado"
                        )

                        total = correlacao.get(
                            "total_pacientes_compartilhados", 0
                        )

                        with st.expander(
                            f"{origem} ↔ {relacionado}"
                        ):
                            st.write(
                                f"**Diagnóstico de origem:** {origem}"
                            )

                            st.write(
                                f"**CID de origem:** {cid_origem}"
                            )

                            st.write(
                                f"**Diagnóstico correlacionado:** {relacionado}"
                            )

                            st.write(
                                f"**CID correlacionado:** {cid_relacionado}"
                            )

                            st.metric(
                                "Pacientes que compartilham os diagnósticos",
                                total
                            )

                    st.caption(
                        "As correlações representam coocorrências "
                        "identificadas na base de dados. "
                        "Não significam necessariamente relação de causa "
                        "e efeito."
                    )

                else:
                    st.info(
                        "Nenhum diagnóstico correlacionado foi retornado "
                        "para este paciente."
                    )

                st.divider()

                # Visualização do grafo clínico
                st.markdown("### Grafo de Relações Clínicas")

                st.write(
                    "Visualização interativa das relações entre o paciente, "
                    "médicos, diagnósticos, medicamentos e alergias."
                )

                dados_grafo = servico.obter_grafo_paciente(
                    paciente_selecionado.get("id")
                )

                # Inclusão das alergias somente na camada visual
                dados_grafo = incluir_alergias_no_grafo(dados_grafo)

                if dados_grafo.get("nodes"):

                    # Filtros do grafo
                    st.markdown("#### Filtros do grafo")

                    tipos_disponiveis = {
                        "Paciente": "🔵 Paciente",
                        "Medico": "🟢 Médico",
                        "Diagnostico": "🟠 Diagnóstico",
                        "Medicamento": "🟣 Medicamento",
                        "Alergia": "🔴 Alergia"
                    }

                    tipos_selecionados = st.multiselect(
                        "Selecione os elementos que deseja visualizar:",
                        options=list(tipos_disponiveis.keys()),
                        default=list(tipos_disponiveis.keys()),
                        format_func=lambda tipo: tipos_disponiveis[tipo],
                        key=f"filtro_grafo_{paciente_selecionado.get('id')}"
                    )

                    # Filtrar os nós
                    nos_filtrados = [
                        no for no in dados_grafo["nodes"]
                        if no.get("type") in tipos_selecionados
                    ]

                    # Identificar os IDs dos nós visíveis
                    ids_nos = {
                        no["id"] for no in nos_filtrados
                    }

                    # Manter somente relações entre nós visíveis
                    arestas_filtradas = [
                        aresta for aresta in dados_grafo.get("edges", [])
                        if aresta.get("source") in ids_nos
                        and aresta.get("target") in ids_nos
                    ]

                    grafo_filtrado = {
                        "nodes": nos_filtrados,
                        "edges": arestas_filtradas
                    }

                    if nos_filtrados:
                        st.write(
                            f"Elementos exibidos: {len(nos_filtrados)}"
                        )

                        st.write(
                            f"Relações exibidas: {len(arestas_filtradas)}"
                        )

                        # Legenda
                        col1, col2, col3, col4, col5 = st.columns(5)

                        with col1:
                            st.markdown("🔵 Paciente")

                        with col2:
                            st.markdown("🟢 Médico")

                        with col3:
                            st.markdown("🟠 Diagnóstico")

                        with col4:
                            st.markdown("🟣 Medicamento")

                        with col5:
                            st.markdown("🔴 Alergia")

                        exibir_grafo(grafo_filtrado)

                        st.caption(
                            "Utilize o mouse para movimentar os elementos, "
                            "aproximar e afastar a visualização. "
                            "Passe o cursor sobre os nós para consultar detalhes."
                        )

                        st.caption(
                            "As alergias são lidas das propriedades do paciente "
                            "e representadas como nós apenas nesta visualização. "
                            "A ligação POSSUI_ALERGIA é criada pela interface "
                            "e não representa uma relação armazenada no Neo4j."
                        )

                    else:
                        st.info(
                            "Selecione pelo menos um tipo de elemento."
                        )

                else:
                    st.info(
                        "Não foram encontradas relações para montar "
                        "o grafo deste paciente."
                    )

    else:
        st.info("Nenhum paciente encontrado.")

except Exception as erro:
    st.error(f"Erro ao carregar pacientes: {erro}")