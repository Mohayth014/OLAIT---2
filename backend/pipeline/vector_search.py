import numpy as np
from typing import List, Dict, Any, Optional

class VectorSearchEngine:
    def __init__(self):
        self.catalog_items: List[Dict[str, Any]] = []
        self.embeddings_matrix: Optional[np.ndarray] = None

    def load_index(self, items: List[Dict[str, Any]]):
        """
        Loads catalog items and pre-computed embeddings into the vector search memory.
        Each item is expected to have 'embedding' (512-dim list/array), 'filename', 'name', etc.
        """
        self.catalog_items = items
        if items and "embedding" in items[0]:
            vectors = [np.array(it["embedding"], dtype=np.float32) for it in items if "embedding" in it and it["embedding"] is not None]
            if vectors:
                self.embeddings_matrix = np.vstack(vectors)
                # Ensure L2 normalized
                norms = np.linalg.norm(self.embeddings_matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1e-10
                self.embeddings_matrix = self.embeddings_matrix / norms
                print(f"[VectorSearchEngine] Indexed {len(vectors)} packaging embeddings.")
            else:
                self.embeddings_matrix = None
        else:
            self.embeddings_matrix = None

    def search_similar(self, query_embedding: np.ndarray, top_k: int = 4, exclude_filename: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Finds Top-K most visually similar products using cosine similarity.
        """
        if self.embeddings_matrix is None or len(self.catalog_items) == 0:
            return []

        # Normalize query embedding
        query_norm = np.linalg.norm(query_embedding)
        if query_norm > 0:
            query_vec = query_embedding / query_norm
        else:
            query_vec = query_embedding

        # Cosine similarity is dot product of normalized vectors
        scores = np.dot(self.embeddings_matrix, query_vec)

        # Sort descending
        ranked_indices = np.argsort(scores)[::-1]

        results = []
        for idx in ranked_indices:
            item = self.catalog_items[idx]
            if exclude_filename and item.get("filename") == exclude_filename:
                continue

            sim_score = float(scores[idx])
            results.append({
                "filename": item.get("filename", ""),
                "name": item.get("name", item.get("filename", "Unknown")),
                "category": item.get("category", "General Commodity"),
                "similarity_score": round(sim_score, 4),
                "similarity_percentage": round(sim_score * 100, 1),
                "thumbnail_url": item.get("thumbnail_url", "")
            })

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
