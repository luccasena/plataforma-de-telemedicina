

class MongoRepository:
    def __init__(self, db_client):
        self.db_client = db_client

    def get_collection(self, collection_name):
        return self.db_client[collection_name]

    def insert_document(self, collection_name, document):
        collection = self.get_collection(collection_name)
        return collection.insert_one(document)

    def find_documents(self, collection_name, query):
        collection = self.get_collection(collection_name)
        return collection.find(query)

    def update_document(self, collection_name, query, update):
        collection = self.get_collection(collection_name)
        return collection.update_one(query, update)

    def delete_document(self, collection_name, query):
        collection = self.get_collection(collection_name)
        return collection.delete_one(query)