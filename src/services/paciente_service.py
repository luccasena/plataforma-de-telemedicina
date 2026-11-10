import streamlit as st
from repositories.mongo_repository import MongoRepository
from pymongo.errors import PyMongoError
from config.database import obter_banco_mongodb

class PacienteService:
    def __init__(self, mongo_repository: MongoRepository):
        self.mongo_repository = mongo_repository

    def obter_pacientes(self):
        try:
            banco = obter_banco_mongodb()
            pacientes = list(
                banco.get_collection("pacientes").find(
                    {},
                    {"_id": 1, "nome": 1, "cpf": 1},
                ).sort("nome", 1)
            )
        
        except PyMongoError:
            st.error("Não foi possível carregar os pacientes.")
            return

        if not pacientes:
            st.info("Nenhum paciente cadastrado.")
            return

        return pacientes

    def criar_paciente(self, nome, cpf, telefone, email):
        resultado = self.mongo_repository.get_collection("pacientes").insert_one(
            {
                "nome": nome,
                "cpf": cpf,
                "telefone": telefone,
                "email": email,
                "status": "Ativo",
            }
        )
        return resultado.inserted_id