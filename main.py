import flet as ft
from ipc import SearchWorker
from ui import main as ui_main

if __name__ == "__main__":
    search_worker = SearchWorker()
    ft.app(target=lambda page: ui_main(page, search_worker), assets_dir="assets")
    search_worker.close()
