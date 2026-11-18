from datetime import datetime

import streamlit as st
from psycopg import Error as PostgresError
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from config.database import (
    conectar_redis,
    obter_mongo_repository,
    obter_neo4j_repository,
    obter_postgres_repository,
)
from services.agenda_cache_service import FUSO_BRASILIA
from services.fila_service import FilaService
from services.prontuario_service import ProntuarioService
from services.usuario_service import UsuarioService

if st.session_state.get("usuario") is None:
    st.error("Usuário não autenticado. Por favor, faça login.")
    st.stop()

mongo_repository = obter_mongo_repository()
fila_service = FilaService(conectar_redis())
usuario_service = UsuarioService(obter_postgres_repository(), mongo_repository)
prontuario_service = ProntuarioService(
    mongo_repository, usuario_service, obter_neo4j_repository()
)

DIAS_DA_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
CARGOS = {
    "Medico": ("Médico", ":material/stethoscope:"),
    "Recepcionista": ("Recepcionista", ":material/support_agent:"),
}

MODULOS = (
    {
        "titulo": "Atendimento Médico",
        "icone": ":material/stethoscope:",
        "pagina": "pages/atendimento_medico/page.py",
        "descricao": (
            "Crie consultas com data e horário, consulte o histórico do "
            "paciente, edite diagnósticos e anexe documentos em PDF."
        )
    },
    {
        "titulo": "Consultas",
        "icone": ":material/assignment_turned_in:",
        "pagina": "pages/finalizacao_consultas/page.py",
        "descricao": (
            "Finalize as consultas em andamento emitindo o laudo, a receita "
            "e as prescrições do paciente."
        )
    },
    {
        "titulo": "Mapeamento Clínico",
        "icone": ":material/hub:",
        "pagina": "pages/mapeamento_clinico/page.py",
        "descricao": (
            "Veja alertas de segurança, diagnósticos correlacionados e o "
            "grafo de relações clínicas de cada paciente."
        )
    },
    {
        "titulo": "Recepção Virtual",
        "icone": ":material/support_agent:",
        "pagina": "pages/recepcao_virtual/page.py",
        "descricao": (
            "Organize a fila de espera, chame o próximo paciente e acompanhe "
            "a agenda do dia e as sessões ativas."
        ),
    },
)


def _saudacao(agora):
    if agora.hour < 12:
        return "Bom dia"
    if agora.hour < 18:
        return "Boa tarde"
    return "Boa noite"


def _formatar_data(agora):
    return (
        f"{DIAS_DA_SEMANA[agora.weekday()]}, {agora.day} de "
        f"{MESES[agora.month - 1]} de {agora.year}"
    )


def _nome_curto(nome):
    partes = nome.split()
    if not partes:
        return "usuário"
    if len(partes) > 1 and partes[0].rstrip(".").casefold() in {"dr", "dra"}:
        return " ".join(partes[:2])
    return partes[0]


def _contar(obter_registros):
    try:
        return len(obter_registros())
    except (PyMongoError, PostgresError, RedisError):
        return None


def _exibir_cabecalho(usuario):
    agora = datetime.now(FUSO_BRASILIA)
    cargo, icone_cargo = CARGOS.get(
        usuario.get("cargo"), (usuario.get("cargo") or "Usuário", None)
    )

    st.markdown(":primary[:material/health_and_safety:] **Plataforma de Telemedicina**")
    st.title(f"{_saudacao(agora)}, {_nome_curto(usuario.get('nome', ''))}!")
    with st.container(horizontal=True, vertical_alignment="center"):
        st.badge(cargo, icon=icone_cargo, color="primary")
        st.caption(_formatar_data(agora).capitalize())
    st.write(
        "Acompanhe o movimento da clínica e acesse os módulos da plataforma "
        "pelos cards abaixo ou pela barra de navegação."
    )


def _exibir_panorama():
    st.subheader("Panorama da clínica", divider="gray")
    indicadores = (
        (
            ":material/hourglass_top: Pacientes na fila",
            _contar(fila_service.obter_ids_na_fila),
            "Pacientes aguardando atendimento na fila do Redis.",
        ),
        (
            ":material/event: Consultas agendadas",
            _contar(prontuario_service.obter_consultas_agendadas),
            "Consultas com status Agendado nos prontuários do MongoDB.",
        ),
        (
            ":material/videocam: Em atendimento",
            _contar(prontuario_service.obter_consultas_em_andamento),
            "Consultas em andamento aguardando a finalização.",
        ),
    )

    for coluna, (rotulo, valor, ajuda) in zip(
        st.columns(len(indicadores)), indicadores
    ):
        with coluna:
            st.metric(rotulo, "—" if valor is None else valor, help=ajuda, border=True)

    if any(valor is None for _, valor, _ in indicadores):
        st.caption(
            ":material/error: Alguns indicadores não puderam ser carregados. "
            "Verifique a conexão com os bancos de dados."
        )


def _exibir_card(modulo):
    with st.container(border=True, height="stretch"):
        with st.container(height="stretch"):
            st.markdown(f"#### :primary[{modulo['icone']}] {modulo['titulo']}")
            st.write(modulo["descricao"])
        if st.button(
            f"Acessar {modulo['titulo']}",
            key=f"acessar_{modulo['pagina']}",
            icon=":material/arrow_forward:",
            icon_position="right",
            width="stretch",
        ):
            st.switch_page(modulo["pagina"])


def _exibir_modulos():
    st.subheader("Módulos", divider="gray")
    for inicio in range(0, len(MODULOS), 2):
        for coluna, modulo in zip(st.columns(2), MODULOS[inicio : inicio + 2]):
            with coluna:
                _exibir_card(modulo)


_exibir_cabecalho(st.session_state["usuario"])
_exibir_panorama()
_exibir_modulos()

st.caption(
    "Os dados ficam distribuídos entre PostgreSQL, MongoDB, Redis e Neo4j, "
    "cada banco escolhido pelo tipo de dado que guarda."
)
