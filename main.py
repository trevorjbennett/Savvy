import flet as ft
from ipc import SearchWorker, ChocoWorker
from ui import main as ui_main


def main():
    """Application entry point."""
    search_worker = SearchWorker()
    choco_worker = ChocoWorker()

    async def app_target(page: ft.Page):
        await ui_main(page, search_worker, choco_worker)

    try:
        ft.app(target=app_target, assets_dir="assets")
    finally:
        search_worker.close()
        choco_worker.close()


if __name__ == "__main__":
    main()
