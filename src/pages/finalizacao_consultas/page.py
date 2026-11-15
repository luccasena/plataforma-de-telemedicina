import pandas as pd
import streamlit as st
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from psycopg import Error as PostgresError
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from config.database import (
    conectar_redis,
    obter_mongo_repository,
    obter_neo4j_repository,
    obter_postgres_repository,
)
from services.agenda_cache_service import AgendaCacheService
from services.fila_service import FilaService
from services.prontuario_service import ProntuarioService
from services.sessao_service import SessaoService
from services.usuario_service import UsuarioService

mongo_repository = obter_mongo_repository()
redis_client = conectar_redis()
prontuario_service = ProntuarioService(
    mongo_repository,
    UsuarioService(obter_postgres_repository(), mongo_repository),
    obter_neo4j_repository(),
)
fila_service = FilaService(redis_client)
agenda_cache_service = AgendaCacheService(redis_client, mongo_repository)
sessao_service = SessaoService(redis_client)

COLUNAS_PRESCRICAO = {
    "nome": "Medicamento",
    "dosagem": "Dosagem",
    "frequencia": "Frequência",
    "orientacoes": "Orientações",
}

def _ler_prescricoes(tabela):
    """Converte as linhas preenchidas da tabela em prescrições, sem os campos vazios."""
    prescricoes = []
    for linha in tabela.to_dict("records"):
        prescricao = {
            campo: valor.strip()
            for campo, valor in linha.items()
            if isinstance(valor, str) and valor.strip()
        }
        if prescricao:
            prescricoes.append(prescricao)
    return prescricoes

def _finalizar_consulta(paciente_id, consulta_id, arquivo, receita, prescricoes):
    if arquivo is None:
        st.error("Anexe o laudo médico para finalizar a consulta.")
        return

    limite_bytes = 16 * 1024 * 1024
    if arquivo.size > limite_bytes:
        st.error("O laudo não pode ultrapassar 16 MB.")
        return

    if any("nome" not in prescricao for prescricao in prescricoes):
        st.error("Informe o medicamento em todas as linhas da prescrição.")
        return

    avisos = []
    try:
        finalizada = prontuario_service.finalizar_consulta(
            paciente_id,
            consulta_id,
            arquivo,
            receita.strip(),
            prescricoes,
        )
    except PyMongoError:
        st.error("Não foi possível finalizar a consulta.")
        return
    except (Neo4jError, ServiceUnavailable):
        # A consulta já foi concluída no MongoDB; só o grafo ficou sem as prescrições.
        finalizada = True
        avisos.append("o Mapeamento Clínico")

    if not finalizada:
        st.error("A consulta não foi encontrada ou já foi finalizada.")
        return

    try:
        fila_service.remover_da_fila(consulta_id)
        agenda_cache_service.invalidar_agendas_da_consulta(consulta_id)
        sessao_service.revogar_sessao_consulta(consulta_id)
    except (RedisError, PyMongoError):
        avisos.append("a fila, a agenda e a sessão da consulta")

    if avisos:
        st.toast(
            "Consulta finalizada, mas não foi possível atualizar "
            f"{' e '.join(avisos)}."
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
        st.write("**Prescrições**")
        tabela_prescricoes = st.data_editor(
            pd.DataFrame(columns=list(COLUNAS_PRESCRICAO), dtype="string"),
            num_rows="dynamic",
            column_config={
                campo: st.column_config.TextColumn(rotulo)
                for campo, rotulo in COLUNAS_PRESCRICAO.items()
            },
            hide_index=True,
            use_container_width=True,
            key=f"prescricoes_{consulta['consulta_id']}",
        )
        st.caption(
            "Opcional. Um medicamento por linha. As prescrições vão para o "
            "prontuário e são verificadas pelos alertas do Mapeamento Clínico."
        )
        receita = st.text_area(
            "Receita / orientações",
            key=f"receita_{consulta['consulta_id']}",
            help="Opcional. Orientações gerais ao paciente.",
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
                _ler_prescricoes(tabela_prescricoes),
            )

def exibir_finalizacao():
    st.subheader("Finalização de Consultas")
    st.write("Consultas em andamento aguardando a emissão do laudo.")

    try:
        consultas = prontuario_service.obter_consultas_em_andamento()
    except (PyMongoError, PostgresError):
        st.error("Não foi possível carregar as consultas em andamento.")
        return

    if not consultas:
        st.info("Sem consultas a serem finalizadas.")
        return

    st.write(f"**Consultas em andamento:** {len(consultas)}")

    for consulta in consultas:
        _exibir_consulta(consulta)

exibir_finalizacao()
