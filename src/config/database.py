import streamlit as st
import redis
from neo4j import GraphDatabase
from psycopg_pool import ConnectionPool
from pymongo import MongoClient
from repositories.mongo_repository import MongoRepository
from repositories.neo4j_repository import Neo4jRepository
from repositories.postgres_repository import PostgresRepository

@st.cache_resource
def conectar_postgres():
    return ConnectionPool(
        st.secrets["postgres"]["uri"],
        min_size=1,
        max_size=5,
        timeout=5,
        kwargs={"connect_timeout": 3},
        check=ConnectionPool.check_connection,
        open=True,
    )

def obter_postgres_repository():
    return PostgresRepository(conectar_postgres())

@st.cache_resource
def conectar_mongodb():
    return MongoClient(
        st.secrets["mongodb"]["uri"],
        serverSelectionTimeoutMS=3000,
    )

def obter_banco_mongodb():
    cliente = conectar_mongodb()
    return cliente[
        st.secrets["mongodb"]["database"]
    ]

def obter_mongo_repository():
    return MongoRepository(obter_banco_mongodb())

@st.cache_resource
def conectar_redis():
    return redis.Redis.from_url(
        st.secrets["redis"]["url"],
        decode_responses=True,
    )

@st.cache_resource
def conectar_neo4j():
    configuracao = st.secrets["neo4j"]
    return GraphDatabase.driver(
        configuracao["uri"],
        auth=(
            configuracao["username"],
            configuracao["password"],
        ),
    )

def obter_neo4j_repository():
    return Neo4jRepository(
        conectar_neo4j(),
        st.secrets["neo4j"].get("database", "neo4j"),
    )
