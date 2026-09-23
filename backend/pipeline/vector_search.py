import numpy as np
from typing import List, Dict, Any, Optional


class VectorSearchEngine:
    """
    In-memory cosine-similarity search over CLIP page embeddings
    ("find pages / documents that look like this one").
    """
    def __init__(self):
        self.items: List[Dict[str, Any]] = []
        self.embeddings_matrix: Optional[np.ndarray] = None

    def load_index(self, items: List[Dict[str, Any]]):
        """
        Loads items and pre-computed embeddings into memory.
        Each item is expected to have 'embedding' (512-dim list/array) plus any
        metadata to return with results (e.g. 'document_id', 'page_number').
        """
        self.items = [it for it in items if it.get("embedding") is not None]
        if not self.items:
            self.embeddings_matrix = None
            return
        vectors = np.vstack([np.array(it["embedding"], dtype=np.float32) for it in self.items])
        # Ensure L2 normalized
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        self.embeddings_matrix = vectors / norms
        print(f"[VectorSearchEngine] Indexed {len(self.items)} page embeddings.")

    def search_similar(self, query_embedding: np.ndarray, top_k: int = 4, exclude_document_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Finds Top-K most visually similar pages using cosine similarity.
        """
        if self.embeddings_matrix is None or not self.items:
            return []

        query_norm = np.linalg.norm(query_embedding)
        query_vec = query_embedding / query_norm if query_norm > 0 else query_embedding

        # Cosine similarity is dot product of normalized vectors
        scores = np.dot(self.embeddings_matrix, query_vec)
        ranked_indices = np.argsort(scores)[::-1]

        results = []
        for idx in ranked_indices:
            item = self.items[idx]
            if exclude_document_id and item.get("document_id") == exclude_document_id:
                continue
            meta = {k: v for k, v in item.items() if k != "embedding"}
            meta["similarity_score"] = round(float(scores[idx]), 4)
            results.append(meta)
            if len(results) >= top_k:
                break
        return results


# Global singleton
_vector_search_instance = None

def get_vector_search_engine() -> VectorSearchEngine:
    global _vector_search_instance
    if _vector_search_instance is None:
        _vector_search_instance = VectorSearchEngine()
    return _vector_search_instance
