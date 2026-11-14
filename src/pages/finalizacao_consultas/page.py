import streamlit as st
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from config.database import conectar_redis, obter_mongo_repository
from services.agenda_cache_service import AgendaCacheService
from services.fila_service import FilaService
from services.prontuario_service import ProntuarioService

mongo_repository = obter_mongo_repository()
redis_client = conectar_redis()
prontuario_service = ProntuarioService(mongo_repository)
fila_service = FilaService(redis_client)
agenda_cache_service = AgendaCacheService(redis_client, mongo_repository)

def _finalizar_consulta(paciente_id, consulta_id, arquivo, receita):
    if arquivo is None:
        st.error("Anexe o laudo médico para finalizar a consulta.")
        return

    limite_bytes = 16 * 1024 * 1024
    if arquivo.size > limite_bytes:
        st.error("O laudo não pode ultrapassar 16 MB.")
        return

    try:
        finalizada = prontuario_service.finalizar_consulta(
            paciente_id,
            consulta_id,
            arquivo,
            receita.strip(),
        )
    except PyMongoError:
        st.error("Não foi possível finalizar a consulta.")
        return

    if not finalizada:
        st.error("A consulta não foi encontrada ou já foi finalizada.")
        return

    try:
        fila_service.remover_da_fila(consulta_id)
        agenda_cache_service.invalidar_agendas_da_consulta(consulta_id)
    except (RedisError, PyMongoError):
        st.toast(
            "Consulta finalizada, mas não foi possível atualizar a fila e a agenda."
        )
    else:
        st.toast("Consulta finalizada com status Concluído.")

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
        receita = st.text_area(
            "Receita / orientações",
            key=f"receita_{consulta['consulta_id']}",
            help="Opcional. Ex.: medicamento, dosagem, frequência e orientações.",
        )
        if st.button(
            "Finalizar consulta",
            type="primary",
            key=f"finalizar_{consulta['consulta_id']}",
        ):
            _finalizar_consulta(
                consulta["paciente_id"],
                consulta["consulta_id"],
                arquivo,
                receita,
            )

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
