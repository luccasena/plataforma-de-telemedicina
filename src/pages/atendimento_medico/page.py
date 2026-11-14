import streamlit as st
from pymongo.errors import PyMongoError
from services.paciente_service import PacienteService
from config.database import obter_mongo_repository
from services.prontuario_service import ProntuarioService
from services.usuario_service import UsuarioService

mongo_repository = obter_mongo_repository()
paciente_service = PacienteService(mongo_repository)
prontuario_service = ProntuarioService(mongo_repository)
usuario_service = UsuarioService(mongo_repository)

@st.dialog("Criar consulta")
def _criar_consulta_modal():
    try:
        medicos = usuario_service.obter_medicos()
        pacientes = paciente_service.obter_pacientes()
    except PyMongoError:
        st.error("Não foi possível carregar os usuários.")
        return

    origem_paciente = st.radio(
        "Paciente",
        ["Paciente existente", "Criar novo paciente"],
        horizontal=True,
    )
    if origem_paciente == "Paciente existente":
        if not pacientes:
            st.warning("Nenhum paciente cadastrado. Crie um novo paciente.")
            return
        opcoes_pacientes = {
            f"{paciente['nome']} - CPF: {paciente.get('cpf', 'não informado')}": paciente[
                "_id"
            ]
            for paciente in pacientes
        }
        paciente_id = opcoes_pacientes[st.selectbox("Paciente", list(opcoes_pacientes))]
    else:
        nome = st.text_input("Nome do paciente")
        cpf = st.text_input("CPF")
        telefone = st.number_input("Telefone", min_value=1, step=1)
        email = st.text_input("E-mail")
        paciente_id = None

    if not medicos:
        st.warning("Nenhum médico ativo cadastrado.")
        return
    opcoes_medicos = {
        f"{medico['nome']} - CRM: {medico.get('crm', 'não informado')}": medico["_id"]
        for medico in medicos
    }
    medico_id = opcoes_medicos[st.selectbox("Médico responsável", list(opcoes_medicos))]
    queixa_principal = st.text_input("Queixa principal")
    sintomas = st.text_area("Sintomas", help="Informe um sintoma por linha.")
    diagnosticos = st.text_area(
        "Diagnósticos",
        help="Opcional. Informe um diagnóstico por linha.",
    )
    observacoes = st.text_area("Observações")

    if not st.button("Criar consulta", type="primary"):
        return
    if not queixa_principal.strip():
        st.error("Informe a queixa principal.")
        return
    if origem_paciente == "Criar novo paciente":
        if not all(campo.strip() for campo in [nome, cpf, email]) or telefone <= 0:
            st.error("Preencha todos os dados obrigatórios do paciente.")
            return
        try:
            paciente_id = paciente_service.criar_paciente(
                nome.strip(),
                cpf.strip(),
                telefone,
                email.strip().lower(),
            )
        except PyMongoError:
            st.error("Não foi possível criar o paciente.")
            return

    try:
        prontuario_service.criar_consulta(
            paciente_id,
            medico_id,
            queixa_principal.strip(),
            [item.strip() for item in sintomas.splitlines() if item.strip()],
            [item.strip() for item in diagnosticos.splitlines() if item.strip()],
            observacoes.strip(),
        )
    except PyMongoError:
        st.error("Não foi possível criar a consulta.")
        return

    st.success("Consulta criada com status Agendado.")
    st.rerun()

def _atualizar_diagnosticos(paciente_id, consulta_id, diagnosticos):
    novos_diagnosticos = [
        diagnostico.strip()
        for diagnostico in diagnosticos.splitlines()
        if diagnostico.strip()
    ]
    try:
        atualizado = prontuario_service.atualizar_diagnosticos(
            paciente_id,
            consulta_id,
            novos_diagnosticos,
        )
    except PyMongoError:
        st.error("Não foi possível atualizar os diagnósticos.")
        return

    if not atualizado:
        st.error("A consulta não foi encontrada para atualização.")
        return

    st.success("Diagnósticos atualizados.")
    st.rerun()

def _atualizar_anexo(paciente_id, consulta_id, arquivo):
    limite_bytes = 16 * 1024 * 1024
    if arquivo.size > limite_bytes:
        st.error("O documento não pode ultrapassar 16 MB.")
        return

    try:
        atualizado = prontuario_service.atualizar_anexo(
            paciente_id,
            consulta_id,
            arquivo,
        )
    except PyMongoError:
        st.error("Não foi possível anexar o documento.")
        return

    if not atualizado:
        st.error("O prontuário não foi encontrado para atualização.")
        return

    st.success("Documento anexado.")
    st.rerun()

def _exibir_consulta(paciente_id, consulta, numero):
    with st.expander(f"Consulta {numero}"):
        st.write(f"**Status:** {consulta.get('status', 'Agendado')}")
        st.write(f"**Queixa principal:** {consulta.get('queixa_principal', '-')}")
        st.write(
            f"**Sintomas:** {', '.join(consulta.get('sintomas', [])) or '-'}"
        )
        with st.form(f"diagnosticos_{consulta['_id']}"):
            diagnosticos = st.text_area(
                "Diagnósticos",
                value="\n".join(consulta.get("diagnosticos", [])),
                help="Informe um diagnóstico por linha.",
            )
            salvar = st.form_submit_button("Salvar diagnósticos")

        if salvar:
            _atualizar_diagnosticos(paciente_id, consulta["_id"], diagnosticos)

        arquivo = st.file_uploader(
            "Anexar documento PDF",
            type=["pdf"],
            key=f"anexo_{consulta['_id']}",
            help="Tamanho máximo: 16 MB, conforme o limite do MongoDB.",
        )
        if arquivo is not None and st.button(
            "Salvar documento",
            key=f"salvar_anexo_{consulta['_id']}",
        ):
            _atualizar_anexo(paciente_id, consulta["_id"], arquivo)

        anexos = consulta.get("exames_laudos", [])
        for anexo_numero, anexo in enumerate(anexos, start=1):
            conteudo = anexo.get("arquivo_conteudo", anexo.get("arquivo"))
            if conteudo is not None:
                st.download_button(
                    f"Baixar documento: {anexo.get('nome_arquivo', 'documento.pdf')}",
                    data=conteudo,
                    file_name=anexo.get("nome_arquivo", "documento.pdf"),
                    mime=anexo.get("tipo_arquivo", "application/pdf"),
                    key=f"baixar_anexo_{consulta['_id']}_{anexo_numero}",
                )
            else:
                st.warning("O documento foi cadastrado, mas não está disponível.")

        st.write(f"**Observações:** {consulta.get('observacoes', '-')}")

def exibir_historico():
    st.subheader("Atendimento Médico - Histórico de Consultas")

    col1, col2 = st.columns([5, 1])

    with col1:
        st.write("Selecione um paciente para consultar seu histórico clínico.")

    with col2:
        if st.button("Criar consulta"):
             _criar_consulta_modal()

    try:
        pacientes = paciente_service.obter_pacientes()
    except PyMongoError:
        st.error("Não foi possível carregar os pacientes.")
        return

    if not pacientes:
        st.info("Nenhum paciente cadastrado.")

    opcoes = {
        f"{paciente['nome']} - CPF: {paciente.get('cpf', 'não informado')}": paciente[
            "_id"
        ]
        for paciente in pacientes
    }

    paciente_selecionado = st.selectbox("Paciente", list(opcoes))
    
    if st.button("Consultar histórico", type="primary"):
        st.session_state["paciente_historico_id"] = opcoes[paciente_selecionado]

    if not pacientes:
        return

    paciente_id = st.session_state.get("paciente_historico_id")
    if paciente_id is None:
        return

    try:
        prontuario = prontuario_service.obter_prontuario_por_paciente(paciente_id)

    except PyMongoError:
        st.error("Não foi possível consultar o histórico.")
        return

    if not prontuario:
        st.info("Este paciente ainda não possui histórico de consultas.")
        return

    consultas = prontuario.get("consultas", [])
    st.write(f"**Consultas encontradas:** {len(consultas)}")

    for numero, consulta in enumerate(consultas, start=1):
        _exibir_consulta(paciente_id, consulta, numero)

exibir_historico()