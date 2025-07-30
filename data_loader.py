import json
import pickle
import logging
from sentence_transformers import SentenceTransformer

MODEL = None
SOFTWARE_DATA = {}
VECTOR_INDEX = {}
_top_tags = []


def load_data_and_model():
    """Load semantic model and data files."""
    global MODEL, SOFTWARE_DATA, VECTOR_INDEX, _top_tags
    logging.info("Loading semantic search model (SentenceTransformer)...")
    try:
        MODEL = SentenceTransformer('all-MiniLM-L6-v2')
        logging.info("SentenceTransformer model loaded successfully.")
    except Exception as e:
        logging.error(f"Failed to load SentenceTransformer model: {e}")
        return False

    logging.info("Attempting to load data.json and vector_index.pkl...")
    try:
        with open("data.json", 'r', encoding='utf-8') as f:
            SOFTWARE_DATA = json.load(f)
            logging.info(f"Loaded data.json with {len(SOFTWARE_DATA)} entries.")
        with open("vector_index.pkl", "rb") as f:
            VECTOR_INDEX = pickle.load(f)
            logging.info(f"Loaded vector_index.pkl with {len(VECTOR_INDEX.get('metadata',[]))} metadata entries.")
        _top_tags[:] = VECTOR_INDEX.get('top_tags', [])
        logging.info(f"Top tags: {_top_tags}")
    except (FileNotFoundError, Exception) as e:
        logging.error(f"Data file loading error: {e}")
        return False
    return True
