import hashlib
import secrets


PREFIXO_SESSAO = "sessao:"
TEMPO_EXPIRACAO_SESSAO = 8 * 60 * 60
# Fora do prefixo "sessao:", que lista apenas as sessões de usuários.
PREFIXO_SESSAO_CONSULTA = "sessao_consulta:"
TEMPO_EXPIRACAO_SESSAO_CONSULTA = 2 * 60 * 60


class SessaoService:
    def __init__(self, redis_client):
        self.redis_client = redis_client

    def _obter_chave(self, token):
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{PREFIXO_SESSAO}{digest}"

    def criar_sessao(self, usuario):
        token = secrets.token_urlsafe(32)
        chave = self._obter_chave(token)

        self.redis_client.hset(
            chave,
            mapping={
                "usuario_id": str(usuario["id"]),
                "nome": usuario["nome"],
                "email": usuario["email"],
                "cargo": usuario["cargo"],
            },
        )
        self.redis_client.expire(chave, TEMPO_EXPIRACAO_SESSAO)
        return token

    def obter_usuario(self, token):
        if not token:
            return None

        chave = self._obter_chave(token)
        usuario = self.redis_client.hgetall(chave)
        if not usuario:
            return None

        self.redis_client.expire(chave, TEMPO_EXPIRACAO_SESSAO)
        return usuario

    def revogar_sessao(self, token):
        if token:
            self.redis_client.delete(self._obter_chave(token))

    # Sessões das consultas ativas: o token identifica a sala da teleconsulta
    # enquanto a consulta está em andamento. Ele fica guardado para que a
    # recepção possa repassá-lo ao paciente.

    def criar_sessao_consulta(self, consulta_id):
        token = secrets.token_urlsafe(16)
        self.redis_client.set(
            f"{PREFIXO_SESSAO_CONSULTA}{consulta_id}",
            token,
            ex=TEMPO_EXPIRACAO_SESSAO_CONSULTA,
        )
        return token

    def obter_sessao_consulta(self, consulta_id):
        chave = f"{PREFIXO_SESSAO_CONSULTA}{consulta_id}"
        token = self.redis_client.get(chave)
        if token is None:
            return None
        return {"token": token, "expira_em": max(self.redis_client.ttl(chave), 0)}

    def revogar_sessao_consulta(self, consulta_id):
        self.redis_client.delete(f"{PREFIXO_SESSAO_CONSULTA}{consulta_id}")
