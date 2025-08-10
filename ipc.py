import multiprocessing
import subprocess
import sys
from typing import Any, Dict, List

from data_loader import load_data_and_model
import search


class ChocoWorker:
    def __init__(self):
        self.request_q: multiprocessing.Queue = multiprocessing.Queue()
        self.response_q: multiprocessing.Queue = multiprocessing.Queue()
        self.proc = multiprocessing.Process(target=self._worker, daemon=True)
        self.proc.start()

    def _worker(self):
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.info("ChocoWorker process started.")

        if sys.platform != "win32":
            logging.warning("ChocoWorker started on a non-Windows platform. It will not be functional.")

        while True:
            message = self.request_q.get()
            logging.info(f"ChocoWorker received message: {message}")

            if message.get('type') == 'stop':
                logging.info("ChocoWorker received stop signal. Exiting.")
                break

            if sys.platform != "win32":
                response = {
                    'status': 'error',
                    'command': message.get('command'),
                    'package_title': message.get('package_title'),
                    'message': 'Chocolatey is only available on Windows.'
                }
                self.response_q.put(response)
                continue

            choco_path = 'C:\\ProgramData\\chocolatey\\bin\\choco.bat'

            if message.get('type') == 'check_status':
                try:
                    result = subprocess.run([choco_path, '--version'], capture_output=True, text=True, check=True, encoding='utf-8')
                    version = result.stdout.strip()
                    response = {'status': 'choco_ok', 'version': version}
                except FileNotFoundError:
                    response = {'status': 'choco_not_found', 'message': f'Chocolatey not found at {choco_path}.'}
                except Exception as e:
                    response = {'status': 'choco_error', 'message': str(e)}
                self.response_q.put(response)
                continue

            if message.get('type') == 'command':
                command = message.get('command')
                package_id = message.get('package_id')
                package_title = message.get('package_title')

                if not command or not package_id:
                    response = {
                        'status': 'error',
                        'command': command,
                        'package_title': package_title,
                        'message': 'Invalid command message received.'
                    }
                    self.response_q.put(response)
                    continue

                try:
                    if command not in ['install', 'uninstall', 'upgrade']:
                        raise ValueError(f"Command '{command}' is not allowed.")

                    choco_command = [choco_path, command, package_id, '-y']

                    logging.info(f"Executing command: {' '.join(choco_command)}")

                    result = subprocess.run(
                        choco_command,
                        capture_output=True,
                        text=True,
                        check=False,
                        encoding='utf-8'
                    )

                    logging.info(f"Command finished with exit code {result.returncode}")

                    if result.returncode == 0:
                        response = {
                            'status': 'success',
                            'command': command,
                            'package_title': package_title,
                            'message': f"Successfully executed '{command}' for {package_title}."
                        }
                    else:
                        error_message = result.stderr.strip().split('\n')[-1] or result.stdout.strip().split('\n')[-1] or f"Choco command failed with exit code {result.returncode}."
                        response = {
                            'status': 'error',
                            'command': command,
                            'package_title': package_title,
                            'message': error_message
                        }

                except FileNotFoundError:
                    logging.error("Choco command not found. Is Chocolatey installed and in the system's PATH?")
                    response = {
                        'status': 'error',
                        'command': command,
                        'package_title': package_title,
                        'message': 'Choco executable not found. Is it installed?'
                    }
                except Exception as e:
                    logging.error(f"An unexpected error occurred in ChocoWorker: {e}")
                    response = {
                        'status': 'error',
                        'command': command,
                        'package_title': package_title,
                        'message': f"An unexpected error occurred: {e}"
                    }

                self.response_q.put(response)

    def execute(self, command: str, package_id: str, package_title: str):
        self.request_q.put({
            'type': 'command',
            'command': command,
            'package_id': package_id,
            'package_title': package_title
        })

    def check_status(self):
        """Requests a status check from the worker."""
        self.request_q.put({'type': 'check_status'})
        # The response will be retrieved from the response_q by a listener in the UI
        return self.response_q.get()

    def close(self):
        # If the queue is empty, putting an item can hang if the process is dead
        # A better approach might be to check if the process is alive
        if self.proc.is_alive():
            self.request_q.put({'type': 'stop'})
            self.proc.join(timeout=5) # Add a timeout
            if self.proc.is_alive():
                self.proc.terminate() # Force terminate if it doesn't stop


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
            self.response_q.put({'status': 'error', 'message': 'load_failed'})
            return
        logging.info("SearchWorker successfully loaded model and data.")
        self.response_q.put({'status': 'ready'})
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
