from datetime import date, datetime, timedelta, timezone

import streamlit as st
from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from config.database import conectar_redis, obter_banco_mongodb
from services.agenda_cache_service import AgendaCacheService
from services.fila_service import FilaService
from services.prontuario_service import ProntuarioService


FUSO_BRASILIA = timezone(timedelta(hours=-3))
banco = obter_banco_mongodb()
redis_client = conectar_redis()
fila_service = FilaService(redis_client)
agenda_cache_service = AgendaCacheService(redis_client, banco)
prontuario_service = ProntuarioService(banco)


def _carregar_medicos():
    return list(
        banco.get_collection("usuarios")
        .find(
            {"cargo": "Medico", "status": "Ativo"},
            {"nome": 1, "crm": 1},
        )
        .sort("nome", 1)
    )


def _formatar_horario(valor):
    horario = datetime.fromisoformat(valor)
    if horario.tzinfo is None:
        horario = horario.replace(tzinfo=timezone.utc)
    return horario.astimezone(FUSO_BRASILIA).strftime("%H:%M")


def _rotular_consulta(consulta):
    return (
        f"{consulta.get('paciente_nome') or 'Paciente não encontrado'}"
        f" - Queixa: {consulta.get('queixa_principal') or 'não informada'}"
        f" - Médico: {consulta.get('medico_nome') or 'não encontrado'}"
    )


def _exibir_adicionar_fila():
    ids_na_fila = fila_service.obter_ids_na_fila()
    consultas = {
        str(consulta["consulta_id"]): consulta
        for consulta in prontuario_service.obter_consultas_agendadas()
        if str(consulta["consulta_id"]) not in ids_na_fila
    }
    if not consultas:
        st.info("Não há consultas agendadas aguardando entrada na fila.")
        return

    with st.form("adicionar_fila"):
        consulta_id = st.selectbox(
            "Consulta agendada",
            list(consultas),
            format_func=lambda opcao: _rotular_consulta(consultas[opcao]),
        )
        adicionar = st.form_submit_button("Adicionar à fila")

    if adicionar:
        consulta = consultas[consulta_id]
        paciente_nome = consulta.get("paciente_nome") or "Paciente"
        fila_service.adicionar_na_fila(
            consulta_id,
            consulta["paciente_id"],
            paciente_nome,
        )
        st.toast(f"{paciente_nome} foi adicionado(a) à fila.")
        st.rerun()


def _chamar_proximo():
    proximo = fila_service.chamar_proximo()
    if proximo is None:
        st.info("Não há pacientes aguardando.")
        return

    paciente_nome = proximo.get("paciente_nome", "Paciente")
    try:
        paciente_id = ObjectId(proximo["paciente_id"])
        consulta_id = ObjectId(proximo["consulta_id"])
    except (KeyError, InvalidId):
        fila_service.descartar(proximo["consulta_id"])
        st.warning(
            f"A entrada de {paciente_nome} não corresponde a uma consulta "
            "agendada e foi removida da fila."
        )
        return

    try:
        iniciada = prontuario_service.iniciar_consulta(paciente_id, consulta_id)
    except PyMongoError:
        fila_service.devolver_a_fila(proximo["consulta_id"])
        st.error(
            "Não foi possível iniciar a consulta. O paciente voltou para a fila."
        )
        return

    if not iniciada:
        fila_service.descartar(proximo["consulta_id"])
        st.warning(
            f"A consulta de {paciente_nome} não está mais com status Agendado "
            "e foi removida da fila."
        )
        return

    try:
        agenda_cache_service.invalidar_agendas_da_consulta(consulta_id)
    except (PyMongoError, RedisError):
        st.toast("Consulta iniciada, mas não foi possível atualizar a agenda do dia.")

    st.toast(f"Paciente chamado: {paciente_nome}. Consulta em andamento.")
    st.rerun()


def _exibir_fila():
    st.subheader("Fila de espera")
    fila = fila_service.obter_fila()
    if not fila:
        st.info("Não há pacientes aguardando.")
    else:
        agora = datetime.now(timezone.utc).timestamp()
        for consulta in fila:
            chegada = float(consulta.get("chegada", agora))
            espera_minutos = max(0, int((agora - chegada) // 60))
            st.write(
                f"**{consulta['posicao']}. {consulta.get('paciente_nome', 'Paciente')}**"
                f" — aguardando há {espera_minutos} min"
            )

    if st.button("Chamar próximo paciente", type="primary"):
        _chamar_proximo()


def _exibir_agenda(medicos):
    st.subheader("Agenda do dia")
    if not medicos:
        st.info("Nenhum médico ativo cadastrado.")
        return

    usuario = st.session_state.get("usuario", {})
    medicos_visiveis = medicos
    if usuario.get("cargo") == "Medico":
        medicos_visiveis = [
            medico
            for medico in medicos
            if str(medico["_id"]) == usuario.get("usuario_id")
        ]
        if not medicos_visiveis:
            st.warning("Não foi possível localizar o médico da sessão.")
            return

    opcoes = {
        f"{medico['nome']} - CRM: {medico.get('crm', 'não informado')}": medico
        for medico in medicos_visiveis
    }
    medico = opcoes[st.selectbox("Médico", list(opcoes))]
    dia_agenda = st.date_input("Data", value=date.today(), key="agenda_data")
    try:
        agenda = agenda_cache_service.obter_agenda_dia(medico["_id"], dia_agenda)
    except (PyMongoError, RedisError):
        st.error("Não foi possível carregar a agenda do dia.")
        return

    if not agenda:
        st.info("Não há consultas agendadas para este médico nesta data.")
        return

    st.dataframe(
        [
            {
                "Horário": (
                    f"{_formatar_horario(item['horario_inicio'])}"
                    f" - {_formatar_horario(item['horario_fim'])}"
                ),
                "Paciente": item.get("paciente_nome") or "Paciente não encontrado",
                "Queixa principal": item.get("queixa_principal") or "-",
                "Status da consulta": item.get("status_consulta") or "-",
            }
            for item in agenda
        ],
        hide_index=True,
        use_container_width=True,
    )


def _exibir_consultas_ativas():
    st.subheader("Consultas em atendimento")
    consultas = fila_service.obter_consultas_ativas()
    if not consultas:
        st.info("Não há consultas em atendimento.")
        return
    for consulta in consultas:
        st.write(
            f"**{consulta.get('paciente_nome', 'Paciente')}** "
            f"— sessão {consulta['consulta_id'][:8]}"
        )


def _exibir_sessoes_ativas():
    st.subheader("Sessões de usuários ativas")
    sessoes = []
    for chave in redis_client.scan_iter(match="sessao:*"):
        usuario = redis_client.hgetall(chave)
        ttl = redis_client.ttl(chave)
        if usuario:
            sessoes.append(
                {
                    "Usuário": usuario.get("nome", "Usuário"),
                    "Perfil": usuario.get("cargo", "-"),
                    "Expira em": f"{max(ttl, 0) // 60} min",
                }
            )
    if sessoes:
        st.dataframe(sessoes, hide_index=True, use_container_width=True)
    else:
        st.info("Não há sessões de usuários ativas.")


def _exibir_painel():
    try:
        medicos = _carregar_medicos()
        _exibir_adicionar_fila()
        _exibir_fila()
        _exibir_agenda(medicos)
        _exibir_consultas_ativas()
        _exibir_sessoes_ativas()
    except PyMongoError:
        st.error("Não foi possível carregar os dados da recepção.")
    except RedisError:
        st.error("Não foi possível acessar a fila no Redis.")


st.title("Recepção Virtual")
st.caption("Fila de espera, agenda diária e sessões ativas.")

if st.button("Atualizar agora"):
    st.rerun()

st.fragment(run_every="15s")(_exibir_painel)()
