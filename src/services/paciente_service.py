from repositories.mongo_repository import MongoRepository


class PacienteService:
    def __init__(self, mongo_repository: MongoRepository):
        self.mongo_repository = mongo_repository

    def obter_pacientes(self):
        return self.mongo_repository.find_documents(
            "pacientes",
            {},
            {"_id": 1, "nome": 1, "cpf": 1},
            sort=[("nome", 1)],
        )

    def criar_paciente(self, nome, cpf, telefone, email):
        resultado = self.mongo_repository.insert_document(
            "pacientes",
            {
                "nome": nome,
                "cpf": cpf,
                "telefone": telefone,
                "email": email,
                "status": "Ativo",
            },
        )
        return resultado.inserted_id
