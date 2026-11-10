import streamlit as st
from pymongo.errors import PyMongoError
from config.database import obter_banco_mongodb
from services.prontuario_service import ProntuarioService

prontuario_service = ProntuarioService(obter_banco_mongodb())

def _exibir_consulta(consulta):
    titulo = (
        f"{consulta.get('paciente_nome', 'Paciente não encontrado')} - "
        f"{consulta.get('medico_nome', 'Médico não encontrado')}"
    )
    with st.expander(titulo):
        st.write(f"**Queixa principal:** {consulta.get('queixa_principal', '-')}")
        st.write(
            f"**Sintomas:** {', '.join(consulta.get('sintomas', [])) or '-'}"
        )
        st.write(
            f"**Diagnósticos:** {', '.join(consulta.get('diagnosticos', [])) or '-'}"
        )
        st.write(f"**Observações:** {consulta.get('observacoes') or '-'}")

def exibir_finalizacao():
    st.subheader("Finalização de Consultas")
    st.write("Consultas em andamento aguardando a emissão do laudo.")

    try:
        consultas = prontuario_service.obter_consultas_em_andamento()
    except PyMongoError:
        st.error("Não foi possível carregar as consultas em andamento.")
        return

    if not consultas:
        st.info("Sem consultas a serem finalizadas.")
        return

    st.write(f"**Consultas em andamento:** {len(consultas)}")

    for consulta in consultas:
        _exibir_consulta(consulta)

exibir_finalizacao()
