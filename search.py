import re
import data_loader
from datetime import datetime
from typing import List, Dict, Any, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

import data_loader

def perform_search(query: str) -> List[Dict[str, Any]]:
    import logging
    logging.info(f"perform_search called with query: '{query}'")
    if not query:
        logging.warning("Query is empty.")
        return []
    if not data_loader.MODEL:
        logging.error("MODEL is not loaded.")
        return []
    if not data_loader.VECTOR_INDEX:
        logging.error("VECTOR_INDEX is not loaded.")
        return []
    if not data_loader.SOFTWARE_DATA:
        logging.error("SOFTWARE_DATA is not loaded.")
        return []
    query_embedding = data_loader.MODEL.encode(query, convert_to_tensor=False)
    similarities = cosine_similarity([query_embedding], data_loader.VECTOR_INDEX['embeddings'])[0]
    top_indices = np.argsort(similarities)[-50:][::-1]
    results = []
    for idx in top_indices:
        if similarities[idx] > 0.3:
            metadata_id = data_loader.VECTOR_INDEX['metadata'][idx]
            key, version_idx_str = metadata_id.split('::')
            version_idx = int(version_idx_str)
            version_data = data_loader.SOFTWARE_DATA[key]['Versions'][version_idx].copy()
            version_data['SoftwareTitle'] = data_loader.SOFTWARE_DATA[key]['Title']
            version_data['__metadata_id'] = metadata_id
            results.append(version_data)
    logging.info(f"perform_search returning {len(results)} results.")
    return results

def perform_tag_filter(tag: str) -> List[Dict[str, Any]]:
    tag_map = data_loader.VECTOR_INDEX.get("tag_map", {})
    if not tag or not tag_map or not data_loader.SOFTWARE_DATA:
        return []
    lower_tag = tag.lower()
    matching_ids = [metadata_id for metadata_id, tags in tag_map.items() if lower_tag in tags]
    results = []
    for metadata_id in matching_ids:
        try:
            key, version_idx_str = metadata_id.split('::')
            version_idx = int(version_idx_str)
            software_info = data_loader.SOFTWARE_DATA[key]
            version_data = software_info['Versions'][version_idx]
            result_item = version_data.copy()
            result_item['SoftwareTitle'] = software_info.get('Title', key)
            result_item['__metadata_id'] = metadata_id
            results.append(result_item)
        except (KeyError, IndexError, ValueError):
            continue
    return results

def find_related_packages(target_pkg_data: dict, count: int = 4) -> List[Dict[str, Any]]:
    target_metadata_id = target_pkg_data.get('__metadata_id')
    if not target_metadata_id or not data_loader.MODEL or not data_loader.VECTOR_INDEX or not data_loader.SOFTWARE_DATA:
        return []
    try:
        target_idx = data_loader.VECTOR_INDEX['metadata'].index(target_metadata_id)
        target_embedding = data_loader.VECTOR_INDEX['embeddings'][target_idx]
    except (ValueError, IndexError):
        import logging
        logging.warning(f"Could not find metadata_id {target_metadata_id} in index.")
        return []
    similarities = cosine_similarity([target_embedding], data_loader.VECTOR_INDEX['embeddings'])[0]
    top_indices = np.argsort(similarities)[-(count+1):][::-1]
    results = []
    for idx in top_indices:
        metadata_id = data_loader.VECTOR_INDEX['metadata'][idx]
        if metadata_id == target_metadata_id:
            continue
        key, version_idx_str = metadata_id.split('::')
        version_idx = int(version_idx_str)
        version_data = data_loader.SOFTWARE_DATA[key]['Versions'][version_idx].copy()
        version_data['SoftwareTitle'] = data_loader.SOFTWARE_DATA[key]['Title']
        version_data['__metadata_id'] = metadata_id
        results.append(version_data)
        if len(results) == count:
            break
    return results

def get_default_results() -> List[Dict[str, Any]]:
    if not data_loader.SOFTWARE_DATA or not data_loader.VECTOR_INDEX:
        return []
    indexed_packages = []
    for metadata_id in data_loader.VECTOR_INDEX['metadata']:
        try:
            key, version_idx_str = metadata_id.split('::')
            version_idx = int(version_idx_str)
            software_info = data_loader.SOFTWARE_DATA[key]
            version_data = software_info['Versions'][version_idx].copy()
            version_data['SoftwareTitle'] = software_info.get('Title', key)
            version_data['__metadata_id'] = metadata_id
            indexed_packages.append(version_data)
        except (KeyError, IndexError, ValueError):
            continue
    indexed_packages.sort(key=lambda v: int(re.search(r'\((\d+)\)', v.get("LastUpdated", "/Date(0)/")).group(1) or 0), reverse=True)
    return indexed_packages[:50]

def format_timestamp(date_str: Optional[str]) -> str:
    if not date_str:
        return "N/A"
    match = re.search(r'\((\d+)\)', date_str)
    if match:
        timestamp_ms = match.group(1)
        if timestamp_ms:
            timestamp = int(timestamp_ms) / 1000
            try:
                return datetime.fromtimestamp(timestamp).strftime('%d %B %Y')
            except (ValueError, OSError):
                return "Invalid Date"
    return "Invalid Date"