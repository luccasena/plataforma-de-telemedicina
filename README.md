# Plataforma de Telemedicina

## Infraestrutura local

Para iniciar MongoDB, Redis e Neo4j:

```bash
docker compose up -d
```

## Gerar dados iniciais

Com os containers em execução, gere as coleções, os validadores e os dados de
exemplo no MongoDB e no Neo4j:

```bash
python src/config/seed.py
```


Serviços disponíveis:

- MongoDB: `localhost:27018`
- Redis: `localhost:6379`
- Neo4j Browser: `http://localhost:7474`
- Neo4j Bolt: `bolt://localhost:7687`

As credenciais podem ser sobrescritas com as variáveis `MONGO_ROOT_USERNAME`,
`MONGO_ROOT_PASSWORD`, `REDIS_PASSWORD` e `NEO4J_AUTH`.
