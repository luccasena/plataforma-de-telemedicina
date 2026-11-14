from datetime import datetime, timezone

CHAVE_FILA = "fila:espera"
PREFIXO_CONSULTA = "fila:consulta:"
TEMPO_EXPIRACAO_CONCLUIDA = 24 * 60 * 60
STATUS_AGUARDANDO = "Aguardando"
STATUS_EM_ANDAMENTO = "Em andamento"


class FilaService:
    """Fila de espera da recepção virtual no Redis.

    - fila:espera (Sorted Set): membro = id da consulta no MongoDB, score =
      horário de chegada (timestamp). O Redis mantém a fila ordenada por ordem
      de chegada.
    - fila:consulta:<id> (Hash): paciente_id, paciente_nome, status e chegada.
      O status acompanha o da consulta no MongoDB.
    """

    def __init__(self, redis_client):
        self.redis_client = redis_client

    def adicionar_na_fila(self, consulta_id, paciente_id, paciente_nome):
        chegada = datetime.now(timezone.utc).timestamp()
        chave_consulta = f"{PREFIXO_CONSULTA}{consulta_id}"

        pipeline = self.redis_client.pipeline()
        pipeline.zadd(CHAVE_FILA, {str(consulta_id): chegada})
        pipeline.hset(
            chave_consulta,
            mapping={
                "paciente_id": str(paciente_id),
                "paciente_nome": paciente_nome,
                "status": STATUS_AGUARDANDO,
                "chegada": chegada,
            },
        )
        pipeline.persist(chave_consulta)
        pipeline.execute()

    def obter_ids_na_fila(self):
        return set(self.redis_client.zrange(CHAVE_FILA, 0, -1))

    def obter_fila(self):
        fila = []
        for posicao, consulta_id in enumerate(
            self.redis_client.zrange(CHAVE_FILA, 0, -1)
        ):
            dados = self.redis_client.hgetall(f"{PREFIXO_CONSULTA}{consulta_id}")
            fila.append(
                {"consulta_id": consulta_id, "posicao": posicao + 1, **dados}
            )
        return fila

    def chamar_proximo(self):
        proximos = self.redis_client.zpopmin(CHAVE_FILA, 1)
        if not proximos:
            return None

        consulta_id = proximos[0][0]
        chave_consulta = f"{PREFIXO_CONSULTA}{consulta_id}"
        self.redis_client.hset(chave_consulta, "status", STATUS_EM_ANDAMENTO)
        return {"consulta_id": consulta_id, **self.redis_client.hgetall(chave_consulta)}

    def devolver_a_fila(self, consulta_id):
        """Desfaz chamar_proximo, recolocando a consulta na posição original."""
        chave_consulta = f"{PREFIXO_CONSULTA}{consulta_id}"
        chegada = float(self.redis_client.hget(chave_consulta, "chegada"))

        pipeline = self.redis_client.pipeline()
        pipeline.zadd(CHAVE_FILA, {str(consulta_id): chegada})
        pipeline.hset(chave_consulta, "status", STATUS_AGUARDANDO)
        pipeline.execute()

    def descartar(self, consulta_id):
        """Remove da fila uma entrada que não corresponde a consulta agendada."""
        pipeline = self.redis_client.pipeline()
        pipeline.zrem(CHAVE_FILA, str(consulta_id))
        pipeline.delete(f"{PREFIXO_CONSULTA}{consulta_id}")
        pipeline.execute()

    def obter_consultas_ativas(self):
        consultas = []
        for chave in self.redis_client.scan_iter(
            match=f"{PREFIXO_CONSULTA}*"
        ):
            dados = self.redis_client.hgetall(chave)
            if dados.get("status") == STATUS_EM_ANDAMENTO:
                consultas.append(
                    {"consulta_id": chave[len(PREFIXO_CONSULTA) :], **dados}
                )
        return consultas

    def remover_da_fila(self, consulta_id):
        chave_consulta = f"{PREFIXO_CONSULTA}{consulta_id}"

        pipeline = self.redis_client.pipeline()
        pipeline.zrem(CHAVE_FILA, str(consulta_id))
        pipeline.hset(chave_consulta, "status", "Concluído")
        pipeline.expire(chave_consulta, TEMPO_EXPIRACAO_CONCLUIDA)
        removidos, _, _ = pipeline.execute()
        return removidos > 0
