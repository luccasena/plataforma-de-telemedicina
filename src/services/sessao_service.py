import hashlib
import secrets


PREFIXO_SESSAO = "sessao:"
TEMPO_EXPIRACAO_SESSAO = 8 * 60 * 60


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
                "usuario_id": str(usuario["_id"]),
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
