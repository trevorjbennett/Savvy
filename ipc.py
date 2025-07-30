import multiprocessing
from typing import Any, Dict, List

from data_loader import load_data_and_model
import search


class SearchWorker:
    def __init__(self):
        self.request_q: multiprocessing.Queue = multiprocessing.Queue()
        self.response_q: multiprocessing.Queue = multiprocessing.Queue()
        self.proc = multiprocessing.Process(target=self._worker, daemon=True)
        self.proc.start()

    def _worker(self):
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.info("SearchWorker process started. Attempting to load model and data...")
        success = load_data_and_model()
        if not success:
            logging.error("SearchWorker failed to load model/data. Returning error to main process.")
            self.response_q.put({'error': 'load_failed'})
            return
        logging.info("SearchWorker successfully loaded model and data.")
        while True:
            message = self.request_q.get()
            logging.info(f"SearchWorker received message: {message}")
            if message.get('type') == 'stop':
                logging.info("SearchWorker received stop signal. Exiting.")
                break
            query = message.get('query', '')
            if query.lower().startswith('tag:'):
                tag = query.split(':', 1)[1]
                results = search.perform_tag_filter(tag)
            elif query:
                results = search.perform_search(query)
            else:
                results = search.get_default_results()
            logging.info(f"SearchWorker sending {len(results) if isinstance(results, list) else 'error'} results back.")
            self.response_q.put(results)

    def search(self, query: str) -> List[Dict[str, Any]]:
        self.request_q.put({'type': 'search', 'query': query})
        return self.response_q.get()

    def close(self):
        self.request_q.put({'type': 'stop'})
        self.proc.join()
