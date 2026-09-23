import os
from pathlib import Path
from PIL import Image
from backend.config import DATASET_DIR, THUMBNAIL_DIR
from backend.pipeline.preprocessing import load_and_orient_image, create_thumbnail
from backend.pipeline.clip_engine import get_clip_engine
from backend.pipeline.vector_search import get_vector_search_engine
from backend.database.db import init_db, save_catalog_product, get_all_catalog_products

def index_dataset_products(force_reindex: bool = False):
    """
    Scans the FOOD dataset, computes CLIP embeddings & classifications,
    generates web-friendly thumbnails, and saves them to SQLite.
    """
    init_db()
    clip_engine = get_clip_engine()
    vector_search = get_vector_search_engine()

    existing_items = get_all_catalog_products()
    indexed_files = {it["filename"] for it in existing_items}

    dataset_path = Path(DATASET_DIR)
    if not dataset_path.exists():
        print(f"[Indexer] Warning: Dataset path {dataset_path} does not exist.")
        return

    files = [f for f in os.listdir(dataset_path) if f.lower().endswith(('.jpg', '.jpeg', '.heic', '.png'))]
    print(f"[Indexer] Found {len(files)} dataset files in {dataset_path}.")

    new_count = 0
    for idx, f in enumerate(files):
        if not force_reindex and f in indexed_files:
            continue

        file_path = dataset_path / f
        try:
            img = load_and_orient_image(file_path)
            
            # Save thumbnail as JPEG
            thumb_filename = f"{Path(f).stem}_thumb.jpg"
            thumb_path = Path(THUMBNAIL_DIR) / thumb_filename
            thumb_img = create_thumbnail(img, max_dim=360)
            thumb_img.save(thumb_path, format="JPEG", quality=85)

            # CLIP Category & Embedding
            categories = clip_engine.classify_category(img)
            embedding = clip_engine.generate_embedding(img)

            top_cat = categories[0]["label"] if categories else "General Commodity"
            clean_name = f.replace(".jpg.jpeg", "").replace(".HEIC", "").replace(".JPG.jpeg", "")

            item = {
                "id": f"CAT-{idx+1:03d}",
                "filename": f,
                "name": clean_name,
                "category": top_cat.title(),
                "thumbnail_url": f"/thumbnails/{thumb_filename}",
                "embedding": embedding.tolist()
            }
            save_catalog_product(item)
            new_count += 1
            if new_count % 10 == 0 or idx == len(files) - 1:
                print(f"[Indexer] Indexed {idx+1}/{len(files)}: {f} -> {top_cat}")

        except Exception as e:
            print(f"[Indexer] Error indexing {f}: {e}")

    # Reload vector search index
    all_catalog = get_all_catalog_products()
    vector_search.load_index(all_catalog)
    print(f"[Indexer] Vector index ready with {len(all_catalog)} packaged commodities ({new_count} newly indexed).")

if __name__ == "__main__":
    index_dataset_products()
