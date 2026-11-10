from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import streamlit as st
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from config.database import conectar_redis, obter_banco_mongodb
from services.agenda_cache_service import AgendaCacheService
from services.fila_service import FilaService


FUSO_BRASILIA = timezone(timedelta(hours=-3))
banco = obter_banco_mongodb()
redis_client = conectar_redis()
fila_service = FilaService(redis_client)
agenda_cache_service = AgendaCacheService(redis_client, banco)


def _carregar_pacientes():
    return list(
        banco.get_collection("pacientes")
        .find({"status": "Ativo"}, {"nome": 1, "cpf": 1})
        .sort("nome", 1)
    )


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


def _adicionar_paciente(paciente):
    consulta_id = uuid4().hex
    fila_service.adicionar_na_fila(
        consulta_id,
        paciente["_id"],
        paciente["nome"],
    )
    st.success(f"{paciente['nome']} foi adicionado(a) à fila.")


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
        proximo = fila_service.chamar_proximo()
        if proximo is None:
            st.info("Não há pacientes aguardando.")
        else:
            st.success(
                f"Paciente chamado: {proximo.get('paciente_nome', 'Paciente')}."
            )
            st.rerun()


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
        st.info("Não há agendamentos confirmados para este médico nesta data.")
        return

    st.dataframe(
        [
            {
                "Horário": _formatar_horario(item["horario_inicio"]),
                "Paciente": item.get("paciente_nome") or "Paciente não encontrado",
                "Status": item["status"],
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
        pacientes = _carregar_pacientes()
        medicos = _carregar_medicos()
        if pacientes:
            opcoes = {
                f"{paciente['nome']} - CPF: {paciente.get('cpf', 'não informado')}": paciente
                for paciente in pacientes
            }
            with st.form("adicionar_fila"):
                paciente = opcoes[st.selectbox("Paciente", list(opcoes))]
                adicionar = st.form_submit_button("Adicionar à fila")
            if adicionar:
                _adicionar_paciente(paciente)
        else:
            st.info("Nenhum paciente ativo cadastrado.")

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
