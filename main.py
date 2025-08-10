import flet as ft
from ipc import SearchWorker
from ui import main as ui_main


def main():
    """Application entry point."""
    worker = SearchWorker()

    async def app_target(page: ft.Page):
        await ui_main(page, worker)

    try:
        ft.app(target=app_target, assets_dir="assets")
    finally:
        worker.close()


if __name__ == "__main__":
    main()
