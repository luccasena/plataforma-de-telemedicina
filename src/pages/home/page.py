import streamlit as st

if st.session_state.get("usuario") is None:
    st.error("Usuário não autenticado. Por favor, faça login.")
    st.stop()

usuario = st.session_state["usuario"]

st.title("Plataforma de Telemedicina")
st.text(
    f"Seja bem-vindo, {usuario['nome']}!\n\nAqui você pode acompanhar suas consultas e histórico de clientes!")