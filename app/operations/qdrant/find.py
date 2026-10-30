from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

client = QdrantClient(url="http://localhost:6333")

def qdrant_find_one(key: str, value: str, coll: str):
    filtered_points, next_offset = client.scroll(
        collection_name=coll,
        limit=1,
        with_payload=True,
        with_vectors=False,  
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key=key,
                    match=MatchValue(value=value)
                )
            ]
        )
    )
    return filtered_points[0] if filtered_points else None
    