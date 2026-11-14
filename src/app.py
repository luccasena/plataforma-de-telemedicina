import streamlit as st
from pymongo.errors import PyMongoError
from services.usuario_service import UsuarioService
from config.database import conectar_redis, obter_mongo_repository
from services.sessao_service import SessaoService

usuario_service = UsuarioService(obter_mongo_repository())
sessao_service = SessaoService(conectar_redis())

def exibir_login():
    st.title("Login")
    st.subheader("Acesse a plataforma de telemedicina")

    with st.form("login"):
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", type="primary")

    if not entrar:
        return

    if not email or not senha:
        st.error("Informe o e-mail e a senha.")
        return

    try:
        usuario = usuario_service.autenticar(email, senha)
    except PyMongoError:
        st.error("Não foi possível verificar o login. Tente novamente.")
        return

    if usuario is None:
        st.error("E-mail ou senha inválidos.")
        return

    token = sessao_service.criar_sessao(usuario)
    st.session_state["token_sessao"] = token
    st.session_state["usuario"] = usuario
    st.rerun()

def exibir_aplicacao():
    if st.sidebar.button("Sair"):
        sessao_service.revogar_sessao(st.session_state.get("token_sessao"))
        st.session_state.clear()
        st.rerun()

    home_page = st.Page(
        "pages/home/page.py",
        title="Página Inicial",
        url_path="home",
    )

    atendimento_medico_page = st.Page(
        "pages/atendimento_medico/page.py",
        title="Atendimento Médico",
        url_path="atendimento-medico",
    )
    consultas_page = st.Page(
        "pages/finalizacao_consultas/page.py",
        title="Consultas",
        url_path="consultas",
    )
    mapeamento_page = st.Page(
        "pages/mapeamento_clinico/page.py",
        title="Mapeamento Clínico",
        url_path="mapeamento-clinico",
    )
    recepcao_page = st.Page(
        "pages/recepcao_virtual/page.py",
        title="Recepção Virtual",
        url_path="recepcao-virtual",
    )

    pagina = st.navigation(
        [home_page, atendimento_medico_page, consultas_page, mapeamento_page, recepcao_page],
        position="top",
    )
    pagina.run()

token_sessao = st.session_state.get("token_sessao")
usuario_sessao = sessao_service.obter_usuario(token_sessao)

if usuario_sessao is None:
    if token_sessao:
        st.session_state.clear()
    exibir_login()
else:
    st.session_state["usuario"] = usuario_sessao
    exibir_aplicacao()
