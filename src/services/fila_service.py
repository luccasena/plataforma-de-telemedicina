from datetime import datetime, timezone

CHAVE_FILA = "fila:espera"
PREFIXO_CONSULTA = "fila:consulta:"
TEMPO_EXPIRACAO_CONCLUIDA = 24 * 60 * 60


class FilaService:
    """Fila de espera da recepção virtual no Redis.

    - fila:espera (Sorted Set): membro = id da consulta, score = horário de
      chegada (timestamp). O Redis mantém a fila ordenada por ordem de chegada.
    - fila:consulta:<id> (Hash): paciente_id, paciente_nome, status e chegada.
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
                "status": "Aguardando",
                "chegada": chegada,
            },
        )
        pipeline.persist(chave_consulta)
        pipeline.execute()

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
        self.redis_client.hset(chave_consulta, "status", "Em atendimento")
        return {"consulta_id": consulta_id, **self.redis_client.hgetall(chave_consulta)}

    def obter_consultas_ativas(self):
        consultas = []
        for chave in self.redis_client.scan_iter(
            match=f"{PREFIXO_CONSULTA}*"
        ):
            dados = self.redis_client.hgetall(chave)
            if dados.get("status") == "Em atendimento":
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
