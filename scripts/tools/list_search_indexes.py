import os
from pymongo.mongo_client import MongoClient
from pymongo.operations import SearchIndexModel

DEFAULT_MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")

def view_index():
    # Connect to your deployment
    uri = DEFAULT_MONGO_URI
    client = MongoClient(uri)
    # Access your database and collection
    database = client["local_school_district"]
    collection = database["schools"]
    # Get a list of the collection's search indexes and print them
    cursor = collection.list_search_indexes()
    for index in cursor:
        print(index)


def edit_index():
    # Connect to your deployment
    uri = DEFAULT_MONGO_URI
    client = MongoClient(uri)
    # Access your database and collection
    database = client["local_school_district"]
    collection = database["schools"]
    # Specify a new index definition
    definition = {
        "mappings": {
            "dynamic": True
        },
    }
    # Update your search index
    collection.update_search_index("embedded-documents-tutorial", definition)
def delete_index():
    # Connect to your deployment
    uri = DEFAULT_MONGO_URI
    client = MongoClient(uri)
    # Access your database and collection
    database = client["local_school_district"]
    collection = database["schools"]
    # Delete your search index
    collection.drop_search_index("embedded-documents-tutorial")


import pymongo

# connect to your cluster

client = pymongo.MongoClient(DEFAULT_MONGO_URI)

# define pipeline

pipeline = [
  {
    '$search': {
      'index': 'embedded-documents-tutorial',
      'embeddedDocument': {
        'path': 'teachers',
        'operator': {
          'compound': {
            'must': [
              {
                'embeddedDocument': {
                  'path': 'teachers.classes',
                  'operator': {
                    'compound': {
                      'must': [
                        {
                          'text': {
                            'path': 'teachers.classes.grade',
                            'query': '12th'
                          }
                        }, {
                          'text': {
                            'path': 'teachers.classes.subject',
                            'query': 'science'
                          }
                        }
                      ]
                    }
                  }
                }
              }
            ],
            'should': [
              {
                'text': {
                  'path': 'teachers.last',
                  'query': 'smith'
                }
              }
            ]
          }
        }
      },
      'highlight': {
        'path': 'teachers.classes.subject'
      }
    }
  }, {
    '$project': {
      '_id': 1,
      'teachers': 1,
      'score': {
        '$meta': 'searchScore'
      },
      'highlights': {
        '$meta': 'searchHighlights'
      }
    }
  }
]

def create_index():
    # Connect to your Atlas deployment
    uri = "<connectionString>"
    client = MongoClient(uri)
    # Access your database and collection
    database = client["<databaseName>"]
    collection = database["<collectionName>"]
    # Create your index model, then create the search index
    search_index_model = SearchIndexModel(
        definition={
            "mappings": {
                "dynamic": True
            },
        },
        name="<indexName>",
    )
    result = collection.create_search_index(model=search_index_model)
    print(result)

# run pipeline

result = client['local_school_district']['schools'].aggregate(pipeline)

# print results

for i in result:

    print(i)
if __name__ == '__main__':
    view_index()
    #edit_index()
    #delete_index()
    print("Done")