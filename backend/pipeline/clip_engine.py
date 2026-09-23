import torch
from transformers import CLIPProcessor, CLIPModel
from PIL import Image
from typing import List, Dict, Any, Tuple
import numpy as np
from backend.config import CLIP_MODEL_NAME, SOURCE_TYPES

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
        self.source_types = SOURCE_TYPES
        print(f"[CLIPEngine] CLIP model initialized on {self.device}.")

    def classify_source_type(self, img: Image.Image) -> List[Dict[str, Any]]:
        """
        Zero-shot classification of what kind of source a page comes from
        (modern print, historical print, handwritten, palm-leaf, inscription).
        Returns [{'source_type', 'confidence', 'percentage'}] sorted by confidence.
        """
        keys = list(self.source_types.keys())
        prompts = [self.source_types[k] for k in keys]
        # Thumbnail to 448 for speed without losing classification fidelity
        thumb = img.copy()
        thumb.thumbnail((448, 448))

        inputs = self.processor(text=prompts, images=thumb, return_tensors="pt", padding=True).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
            probs = outputs.logits_per_image.softmax(dim=1).squeeze().cpu().tolist()

        if isinstance(probs, float):
            probs = [probs]

        results = [
            {"source_type": k, "confidence": round(float(p), 4), "percentage": round(float(p) * 100, 2)}
            for k, p in zip(keys, probs)
        ]
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

# Global singleton helper
_clip_engine_instance = None

def get_clip_engine() -> CLIPEngine:
    global _clip_engine_instance
    if _clip_engine_instance is None:
        _clip_engine_instance = CLIPEngine()
    return _clip_engine_instance
