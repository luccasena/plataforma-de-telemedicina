import streamlit as st
import redis
from neo4j import GraphDatabase
from pymongo import MongoClient

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
