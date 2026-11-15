import unittest

from services.sessao_service import (
    PREFIXO_SESSAO,
    TEMPO_EXPIRACAO_SESSAO_CONSULTA,
    SessaoService,
)


class RedisFake:
    def __init__(self):
        self.valores = {}
        self.expiracoes = {}

    def set(self, key, value, ex=None):
        self.valores[key] = value
        self.expiracoes[key] = ex

    def get(self, key):
        return self.valores.get(key)

    def ttl(self, key):
        return self.expiracoes.get(key, -2)

    def delete(self, key):
        self.expiracoes.pop(key, None)
        return int(self.valores.pop(key, None) is not None)


class SessaoConsultaTests(unittest.TestCase):
    def setUp(self):
        self.redis = RedisFake()
        self.service = SessaoService(self.redis)

    def test_token_da_consulta_fica_no_redis_com_expiracao(self):
        token = self.service.criar_sessao_consulta("c1")

        self.assertEqual(
            self.service.obter_sessao_consulta("c1"),
            {"token": token, "expira_em": TEMPO_EXPIRACAO_SESSAO_CONSULTA},
        )

    def test_sessao_da_consulta_nao_aparece_entre_as_sessoes_de_usuarios(self):
        self.service.criar_sessao_consulta("c1")

        self.assertFalse(
            any(chave.startswith(PREFIXO_SESSAO) for chave in self.redis.valores)
        )

    def test_revogar_remove_o_token(self):
        self.service.criar_sessao_consulta("c1")
        self.service.revogar_sessao_consulta("c1")

        self.assertIsNone(self.service.obter_sessao_consulta("c1"))


if __name__ == "__main__":
    unittest.main()
