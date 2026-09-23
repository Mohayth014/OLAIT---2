import torch
from transformers import CLIPProcessor, CLIPModel
from PIL import Image
from typing import List, Dict, Any, Tuple
import numpy as np
from backend.config import CLIP_MODEL_NAME, COMMODITY_CATEGORIES

class CLIPEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CLIPEngine, cls).__new__(cls)
            cls._instance._init_model()
        return cls._instance

    def _init_model(self):
        print(f"[CLIPEngine] Loading {CLIP_MODEL_NAME}...")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
        self.model = CLIPModel.from_pretrained(CLIP_MODEL_NAME, use_safetensors=True).to(self.device)
        self.model.eval()
        self.categories = COMMODITY_CATEGORIES
        print(f"[CLIPEngine] CLIP model initialized on {self.device}.")

    def classify_category(self, img: Image.Image, candidate_labels: List[str] = None) -> List[Dict[str, Any]]:
        """
        Performs zero-shot classification on the product image against candidate categories.
        Returns sorted list of { 'label': str, 'confidence': float, 'percentage': float }.
        """
        labels = candidate_labels or self.categories
        # Thumbnail to 448 for speed without losing classification fidelity
        thumb = img.copy()
        thumb.thumbnail((448, 448))

        inputs = self.processor(text=labels, images=thumb, return_tensors="pt", padding=True).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits_per_image # shape: [1, num_labels]
            probs = logits.softmax(dim=1).squeeze().cpu().tolist()

        if isinstance(probs, float):
            probs = [probs]

        results = []
        for label, prob in zip(labels, probs):
            results.append({
                "label": label,
                "confidence": round(float(prob), 4),
                "percentage": round(float(prob) * 100, 2)
            })

        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results

    def generate_embedding(self, img: Image.Image) -> np.ndarray:
        """
        Generates a 512-dimensional L2-normalized image embedding vector for vector search.
        """
        thumb = img.copy()
        thumb.thumbnail((336, 336))
        inputs = self.processor(images=thumb, return_tensors="pt").to(self.device)
        with torch.no_grad():
            image_features = self.model.get_image_features(**inputs)
            # transformers >=5 returns a BaseModelOutputWithPooling (projected embedding
            # in .pooler_output); older versions return the tensor directly.
            if not isinstance(image_features, torch.Tensor):
                image_features = image_features.pooler_output
            # Normalize vector to unit sphere
            image_features = image_features / image_features.norm(p=2, dim=-1, keepdim=True)
            embedding = image_features.squeeze().cpu().numpy()
        return embedding.astype(np.float32)

    def match_brand_or_terms(self, img: Image.Image, terms: List[str]) -> List[Dict[str, Any]]:
        """
        Matches product image against specific candidate brand or identity terms.
        """
        if not terms:
            return []
        inputs = self.processor(text=terms, images=img, return_tensors="pt", padding=True).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
            probs = outputs.logits_per_image.softmax(dim=1).squeeze().cpu().tolist()

        if isinstance(probs, float):
            probs = [probs]

        ranked = [{"term": t, "score": round(float(p), 4)} for t, p in zip(terms, probs)]
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

# Global singleton helper
_clip_engine_instance = None

def get_clip_engine() -> CLIPEngine:
    global _clip_engine_instance
    if _clip_engine_instance is None:
        _clip_engine_instance = CLIPEngine()
    return _clip_engine_instance
