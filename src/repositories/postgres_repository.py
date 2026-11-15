from contextlib import contextmanager

from psycopg.rows import dict_row


class PostgresRepository:
    """Acesso às tabelas do PostgreSQL usado pelos services.

    Cada operação pega uma conexão do pool e a devolve ao terminar. A transação
    é confirmada ao fim da operação ou desfeita se ocorrer um erro.
    """

    def __init__(self, pool):
        self.pool = pool

    def fetch_one(self, query, params=None):
        with self.pool.connection() as conexao:
            with conexao.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()

    def fetch_all(self, query, params=None):
        with self.pool.connection() as conexao:
            with conexao.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()

    def execute(self, query, params=None):
        """Executa um comando sem retorno de linhas e devolve as linhas afetadas."""
        with self.pool.connection() as conexao:
            return conexao.execute(query, params).rowcount

    @contextmanager
    def transacao(self):
        """Cursor de uma transação confirmada só se o bloco terminar sem erro.

        Serve para desfazer a gravação no PostgreSQL quando a etapa seguinte,
        feita em outro banco, falha.
        """
        with self.pool.connection() as conexao:
            with conexao.cursor(row_factory=dict_row) as cursor:
                yield cursor
