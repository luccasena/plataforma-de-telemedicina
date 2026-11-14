from datetime import date, datetime, time, timezone

from repositories.mongo_repository import MongoRepository
from repositories.postgres_repository import PostgresRepository

COLECAO_MEDICOS = "medicos"


class UsuarioService:
    """Médicos e recepcionistas.

    Os dados de acesso (nome, CPF, e-mail, senha, cargo e status) ficam na
    tabela usuarios do PostgreSQL. Os detalhes dos médicos (CRM, telefone,
    especialidades e data de cadastro) ficam na coleção medicos do MongoDB,
    ligados ao usuário pelo campo usuario_id.
    """

    def __init__(
        self,
        postgres_repository: PostgresRepository,
        mongo_repository: MongoRepository,
    ):
        self.postgres_repository = postgres_repository
        self.mongo_repository = mongo_repository

    def autenticar(self, email, senha):
        return self.postgres_repository.fetch_one(
            """
            SELECT id, nome, email, cargo
            FROM usuarios
            WHERE email = %s
              AND senha = %s
              AND cargo IN ('Medico', 'Recepcionista')
              AND status = 'Ativo'
            """,
            (email.strip().lower(), senha),
        )

    def obter_medicos(self):
        """Médicos ativos, com o CRM guardado na coleção medicos."""
        medicos = self.postgres_repository.fetch_all(
            """
            SELECT id, nome
            FROM usuarios
            WHERE cargo = 'Medico' AND status = 'Ativo'
            ORDER BY nome
            """
        )
        if not medicos:
            return []

        detalhes = {
            detalhe["usuario_id"]: detalhe
            for detalhe in self.mongo_repository.find_documents(
                COLECAO_MEDICOS,
                {"usuario_id": {"$in": [medico["id"] for medico in medicos]}},
                {"_id": 0, "usuario_id": 1, "crm": 1, "uf_crm": 1},
            )
        }
        for medico in medicos:
            detalhe = detalhes.get(medico["id"])
            if detalhe:
                medico["crm"] = detalhe["crm"]
                medico["uf_crm"] = detalhe["uf_crm"]
        return medicos

    def obter_nomes(self, ids):
        """Devolve {id: nome} dos usuários informados."""
        ids = sorted({usuario_id for usuario_id in ids if usuario_id is not None})
        if not ids:
            return {}
        linhas = self.postgres_repository.fetch_all(
            "SELECT id, nome FROM usuarios WHERE id = ANY(%s)",
            (ids,),
        )
        return {linha["id"]: linha["nome"] for linha in linhas}

    def criar_medico(
        self, nome, cpf, crm, uf_crm, telefone, email, senha, especialidades=()
    ):
        """Cria ou atualiza (pelo CPF) o médico nos dois bancos."""
        with self.postgres_repository.transacao() as cursor:
            cursor.execute(
                """
                INSERT INTO usuarios (nome, cpf, cargo, email, senha, status)
                VALUES (%s, %s, 'Medico', %s, %s, 'Ativo')
                ON CONFLICT (cpf) DO UPDATE SET
                    nome = EXCLUDED.nome,
                    cargo = EXCLUDED.cargo,
                    email = EXCLUDED.email,
                    senha = EXCLUDED.senha,
                    status = EXCLUDED.status
                RETURNING id, nome, cpf, cargo, email, status
                """,
                (nome, cpf, email, senha),
            )
            usuario = cursor.fetchone()
            # Gravado ainda dentro da transação: se o MongoDB falhar, o usuário
            # não fica no PostgreSQL sem os detalhes.
            detalhes = self.mongo_repository.find_and_update_document(
                COLECAO_MEDICOS,
                {"usuario_id": usuario["id"]},
                {
                    "$set": {
                        "crm": crm,
                        "uf_crm": uf_crm,
                        "telefone": telefone,
                        "especialidades": list(especialidades),
                    },
                    "$setOnInsert": {
                        "data_cadastro": datetime.combine(
                            date.today(), time(), timezone.utc
                        )
                    },
                },
                upsert=True,
            )
        detalhes.pop("_id", None)
        return {**usuario, **detalhes}
