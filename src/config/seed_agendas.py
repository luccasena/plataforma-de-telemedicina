"""Preaquece no Redis o cache das agendas futuras que estão no MongoDB.

Uso, na raiz do projeto e com os containers em execução:
    python src/config/seed_agendas.py
"""

import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.agenda_cache_service import AgendaCacheService


ARQUIVO_SECRETS = (
    Path(__file__).resolve().parents[2] / ".streamlit" / "secrets.toml"
)
FUSO_BRASILIA = timezone(timedelta(hours=-3))


def main():
    secrets = tomllib.loads(ARQUIVO_SECRETS.read_text(encoding="utf-8-sig"))
    mongo_client = MongoClient(secrets["mongodb"]["uri"], tz_aware=True)
    redis_client = redis.Redis.from_url(
        secrets["redis"]["url"],
        decode_responses=True,
    )
    try:
        mongo_client.admin.command("ping")
        redis_client.ping()
        banco = mongo_client[secrets["mongodb"]["database"]]
        cache = AgendaCacheService(redis_client, banco)
        agora = datetime.now(FUSO_BRASILIA)
        agendamentos = banco.get_collection("agendamentos").find(
            {
                "status": {"$in": ["confirmado", "em_atendimento"]},
                "horario_inicio": {"$gte": agora},
            },
            {"medico_id": 1, "horario_inicio": 1},
        )
        agendas = {
            (item["medico_id"], item["horario_inicio"].astimezone(FUSO_BRASILIA).date())
            for item in agendamentos
        }
        for medico_id, dia in sorted(agendas, key=lambda item: (str(item[0]), item[1])):
            cache.obter_agenda_dia(medico_id, dia)
        print(f"{len(agendas)} agenda(s) carregada(s) no cache Redis.")
    finally:
        mongo_client.close()
        redis_client.close()


if __name__ == "__main__":
    main()
