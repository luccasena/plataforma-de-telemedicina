import streamlit as st
from pymongo.errors import PyMongoError
from config.database import obter_banco_mongodb
from services.prontuario_service import ProntuarioService

prontuario_service = ProntuarioService(obter_banco_mongodb())

def _finalizar_consulta(paciente_id, consulta_id, arquivo):
    try:
        finalizada = prontuario_service.finalizar_consulta(
            paciente_id,
            consulta_id,
            arquivo,
        )
    except PyMongoError:
        st.error("Não foi possível finalizar a consulta.")
        return

    if not finalizada:
        st.error("A consulta não foi encontrada ou já foi finalizada.")
        return

    st.success("Consulta finalizada com status Concluído.")
    st.rerun()

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

        arquivo = st.file_uploader(
            "Laudo médico (PDF)",
            type=["pdf"],
            key=f"laudo_{consulta['consulta_id']}",
            help="Tamanho máximo: 16 MB, conforme o limite do MongoDB.",
        )
        if st.button(
            "Finalizar consulta",
            type="primary",
            key=f"finalizar_{consulta['consulta_id']}",
        ):
            _finalizar_consulta(consulta["paciente_id"], consulta["consulta_id"], arquivo)

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
