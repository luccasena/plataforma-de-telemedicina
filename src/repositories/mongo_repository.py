from pymongo import ReturnDocument


class MongoRepository:
    """Acesso às coleções do MongoDB usado por todos os services."""

    def __init__(self, db_client):
        self.db_client = db_client

    def get_collection(self, collection_name):
        return self.db_client[collection_name]

    def insert_document(self, collection_name, document):
        collection = self.get_collection(collection_name)
        return collection.insert_one(document)

    def find_document(self, collection_name, query, projection=None):
        collection = self.get_collection(collection_name)
        return collection.find_one(query, projection)

    def find_documents(self, collection_name, query, projection=None, sort=None):
        collection = self.get_collection(collection_name)
        cursor = collection.find(query, projection)
        if sort:
            cursor = cursor.sort(sort)
        return list(cursor)

    def aggregate(self, collection_name, pipeline):
        collection = self.get_collection(collection_name)
        return list(collection.aggregate(pipeline))

    def update_document(
        self, collection_name, query, update, array_filters=None, upsert=False
    ):
        collection = self.get_collection(collection_name)
        return collection.update_one(
            query, update, array_filters=array_filters, upsert=upsert
        )

    def find_and_update_document(self, collection_name, query, update, upsert=False):
        """Atualiza um documento e devolve a versão já atualizada."""
        collection = self.get_collection(collection_name)
        return collection.find_one_and_update(
            query,
            update,
            upsert=upsert,
            return_document=ReturnDocument.AFTER,
        )

    def delete_document(self, collection_name, query):
        collection = self.get_collection(collection_name)
        return collection.delete_one(query)
