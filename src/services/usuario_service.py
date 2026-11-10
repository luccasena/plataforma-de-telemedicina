from repositories.mongo_repository import MongoRepository
from pymongo import ReturnDocument

class UsuarioService:
    def __init__(self, mongo_repository: MongoRepository):
        self.mongo_repository = mongo_repository

    def autenticar(self, email, senha):
        return self.mongo_repository.get_collection("usuarios").find_one(
        {
            "email": email.strip().lower(),
            "senha": senha,
            "cargo": {"$in": ["Medico", "Recepcionista"]},
            "status": "Ativo",
        },
        {"nome": 1, "email": 1, "cargo": 1},
    )

    def obter_medicos(self):
        return list(
            self.mongo_repository.get_collection("usuarios").find(
                {"cargo": "Medico", "status": "Ativo"},
                {"nome": 1, "crm": 1},
            ).sort("nome", 1)
        )

    def criar_medico(self, nome, cpf, crm, uf_crm, telefone, email, senha):
        return self.mongo_repository.get_collection("usuarios").find_one_and_update(
            {"cpf": cpf},
            {
                "$set": {
                    "nome": nome,
                    "cargo": "Medico",
                    "crm": crm,
                    "uf_crm": uf_crm,
                    "telefone": telefone,
                    "email": email,
                    "senha": senha,
                    "status": "Ativo",
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
