import flet as ft
import asyncio
import logging
from typing import List

from data_loader import _top_tags
from ipc import SearchWorker
import search
# The 'Counter' import is no longer needed

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Application Theme Colors (Black & White Professional - Refined Swiss) ---
TEXT_PRIMARY = ft.Colors.BLACK87
TEXT_SECONDARY = ft.Colors.BLACK54
TEXT_ON_PRIMARY_ACTION = ft.Colors.WHITE
BACKGROUND_COLOR = ft.Colors.WHITE
BORDER_COLOR = ft.Colors.BLACK12
BORDER_COLOR_FOCUSED = ft.Colors.BLACK38
BUTTON_PRIMARY_BG = ft.Colors.BLACK87
BUTTON_SECONDARY_BG = ft.Colors.GREY_100
BUTTON_RADIUS = 8
SEARCH_BAR_RADIUS = 28

# --- Global References ---
_page_ref: ft.Page = None
_global_snackbar: ft.SnackBar = None
_package_list_view_ref: ft.ListView = None
_current_search_query_field: ft.TextField = None
_queue_count_button: ft.TextButton = None
_top_tags: List[str] = []
_search_worker: SearchWorker = None

package_detail_dialog = ft.AlertDialog(
    modal=True,
    title=ft.Text("Package Details"),
    content=ft.Text("Loading..."),
    actions_alignment=ft.MainAxisAlignment.END,
    shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS),
    bgcolor=BACKGROUND_COLOR,
    surface_tint_color=ft.Colors.WHITE
)

# --- Semantic Search Engine ---



# --- UI Functions ---

class AppNotifier:
    @staticmethod
    async def show_snackbar(message: str, color: str = TEXT_ON_PRIMARY_ACTION, bgcolor: str = BUTTON_PRIMARY_BG, duration=3000):
        if _page_ref and _global_snackbar:
            _global_snackbar.content = ft.Text(message, color=color)
            _global_snackbar.bgcolor = bgcolor
            _global_snackbar.duration = duration
            _global_snackbar.open = True
            _page_ref.update()

def close_dialog_global(dialog_instance):
    dialog_instance.open = False
    if _page_ref:
        _page_ref.update()

async def show_package_details_global(pkg_data: dict):
    if not _page_ref: return

    async def show_related_package(new_pkg_data: dict):
        close_dialog_global(package_detail_dialog)
        await asyncio.sleep(0.05)
        await show_package_details_global(new_pkg_data)

    package_detail_dialog.title = ft.Text(pkg_data.get("SoftwareTitle", "Details"), weight=ft.FontWeight.BOLD, size=20, color=TEXT_PRIMARY)

    details_column = [
        ft.Text(f"Version: {pkg_data.get('Version', 'N/A')}", color=TEXT_SECONDARY, size=14),
        ft.Text(f"Last Updated: {search.format_timestamp(pkg_data.get('LastUpdated'))}", color=TEXT_SECONDARY, size=14),
        ft.Container(height=15),
        ft.Text("Summary:", weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, size=16),
        ft.Text(pkg_data.get("Summary", "Not available."), color=TEXT_PRIMARY, size=15, selectable=True),
        ft.Container(height=15),
        ft.Text(f"Tags: {pkg_data.get('Tags', 'N/A')}", italic=True, color=TEXT_SECONDARY, size=14),
        ft.Divider(height=25, color=BORDER_COLOR),
    ]

    related_items_container = ft.Container()
    details_content = ft.Column(
        controls=details_column + [related_items_container],
        tight=True, scroll=ft.ScrollMode.ADAPTIVE, height=400, spacing=6
    )
    package_detail_dialog.content = details_content
    package_detail_dialog.actions = [
        ft.TextButton("Close", on_click=lambda _: close_dialog_global(package_detail_dialog), style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS), color=TEXT_SECONDARY))
    ]
    package_detail_dialog.open = True
    _page_ref.update()

    related_items_container.content = ft.Row(
        [ft.ProgressRing(width=16, height=16, stroke_width=2), ft.Text("Finding related items...", color=TEXT_SECONDARY, size=14)],
        spacing=10
    )
    _page_ref.update()
    
    related_packages = await asyncio.to_thread(search.find_related_packages, pkg_data)

    if related_packages:
        suggestion_chips = [
            ft.Chip(
                label=ft.Text(rel_pkg.get('SoftwareTitle', 'Unknown')),
                leading=ft.Icon(ft.Icons.SETTINGS_APPLICATIONS_ROUNDED, size=16),
                on_click=lambda _, pkg=rel_pkg: _page_ref.run_task(show_related_package, pkg),
                tooltip=f"View details for {rel_pkg.get('SoftwareTitle')}",
                bgcolor=BUTTON_SECONDARY_BG
            ) for rel_pkg in related_packages
        ]
        
        related_items_container.content = ft.Column([
            ft.Text("You might also like...", weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, size=16),
            ft.Container(height=8),
            ft.Row(controls=suggestion_chips, wrap=True, spacing=10)
        ])
    else:
        related_items_container.content = None
    
    _page_ref.update()

def create_package_list_item(pkg_data: dict):
    title = pkg_data.get('SoftwareTitle', 'Unknown Package')
    summary = pkg_data.get('Summary', 'No summary available.')
    version = pkg_data.get('Version', 'N/A')
    
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SETTINGS_APPLICATIONS_ROUNDED, color=TEXT_PRIMARY, size=32),
                ft.Column(
                    [
                        ft.Text(title, weight=ft.FontWeight.W_500, size=17, color=TEXT_PRIMARY),
                        ft.Text(f"v{version} - {summary}", size=14, color=TEXT_SECONDARY, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                    ],
                    spacing=4, alignment=ft.MainAxisAlignment.CENTER, expand=True
                ),
                ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, color=TEXT_SECONDARY, opacity=0.7)
            ],
            spacing=20, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.symmetric(vertical=18, horizontal=25),
        border_radius=BUTTON_RADIUS, ink=True,
        on_click=lambda _: _page_ref.run_task(show_package_details_global, pkg_data),
        border=ft.border.all(1, BORDER_COLOR),
    )

async def run_search_and_update_view(query: str):
    global _package_list_view_ref
    if not _page_ref or not _package_list_view_ref: return
    _package_list_view_ref.controls.clear()

    loading_indicator = ft.Row([ft.ProgressRing(width=20, height=20, stroke_width=2.5, color=TEXT_PRIMARY), ft.Text("Searching...", color=TEXT_SECONDARY)], alignment=ft.MainAxisAlignment.CENTER, spacing=10)
    _package_list_view_ref.controls.append(loading_indicator)
    _page_ref.update()

    results = await asyncio.to_thread(_search_worker.search, query)

    _package_list_view_ref.controls.clear()

    if results:
        for pkg_data in results:
            _package_list_view_ref.controls.append(create_package_list_item(pkg_data))
    elif query.strip():
        if query.lower().startswith("tag:"):
            tag = query.split(":", 1)[1]
            _package_list_view_ref.controls.append(ft.Container(content=ft.Text(f"No packages found with the tag '{tag}'.", text_align=ft.TextAlign.CENTER, color=TEXT_SECONDARY, italic=True, size=14), padding=ft.padding.all(30)))
        else:
            _package_list_view_ref.controls.append(ft.Container(content=ft.Text(f"No results found for '{query}'.", text_align=ft.TextAlign.CENTER, color=TEXT_SECONDARY, italic=True, size=14), padding=ft.padding.all(30)))
    else:
        _package_list_view_ref.controls.append(ft.Container(content=ft.Text("No packages found. The data source might be empty.", text_align=ft.TextAlign.CENTER, color=TEXT_SECONDARY, italic=True, size=14), padding=ft.padding.all(30)))

    _page_ref.update()


# --- Screen Definitions ---

def create_app_bar(current_screen: str):
    global _queue_count_button
    if not _queue_count_button:
        _queue_count_button = ft.TextButton("Queue", icon=ft.Icons.QUEUE_ROUNDED, on_click=lambda _: _page_ref.run_task(show_queue_screen), style=ft.ButtonStyle(color=TEXT_PRIMARY, shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS)))

    def toggle_fullscreen(e):
        _page_ref.window_full_screen = not _page_ref.window_full_screen
        fullscreen_button.icon = ft.Icons.FULLSCREEN_EXIT_ROUNDED if _page_ref.window_full_screen else ft.Icons.FULLSCREEN_ROUNDED
        _page_ref.update()

    minimize_button = ft.IconButton(icon=ft.Icons.MINIMIZE_ROUNDED, icon_size=16, on_click=lambda _: _page_ref.window_minimize(), tooltip="Minimize")
    fullscreen_button = ft.IconButton(icon=ft.Icons.FULLSCREEN_ROUNDED, icon_size=16, on_click=toggle_fullscreen, tooltip="Toggle Fullscreen")
    close_button = ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_size=16, on_click=lambda _: _page_ref.window_destroy(), tooltip="Close", style=ft.ButtonStyle(color=TEXT_SECONDARY, overlay_color={"hovered": ft.Colors.RED_700}))

    return ft.AppBar(
        leading=ft.IconButton(ft.Icons.HOME_ROUNDED, tooltip="Home", icon_color=TEXT_PRIMARY, on_click=lambda _: _page_ref.run_task(show_initial_screen)) if current_screen != "initial" else None,
        leading_width=80,
        title=ft.WindowDragArea(content=ft.Container(content=ft.Text("Savvy App Centre", weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY), alignment=ft.alignment.center), expand=True),
        center_title=True, bgcolor=BACKGROUND_COLOR,
        actions=[
            ft.Container(content=ft.Text("Search Mode", color=TEXT_SECONDARY, size=12), alignment=ft.alignment.center, padding=ft.padding.only(right=15)),
            _queue_count_button, ft.Container(width=10),
            minimize_button, fullscreen_button, close_button, ft.Container(width=10)
        ],
        toolbar_height=70, elevation=1, surface_tint_color=ft.Colors.WHITE
    )

async def show_initial_screen():
    global _current_search_query_field
    if not _page_ref: return
    _page_ref.controls.clear()
    _page_ref.appbar = create_app_bar("initial")
    _page_ref.bgcolor = BACKGROUND_COLOR

    _current_search_query_field = ft.TextField(
        hint_text="Search for applications...", expand=True, border_radius=SEARCH_BAR_RADIUS,
        content_padding=ft.padding.symmetric(horizontal=30, vertical=22), text_size=17,
        color=TEXT_PRIMARY, hint_style=ft.TextStyle(color=TEXT_SECONDARY, size=17),
        border_color=BORDER_COLOR, focused_border_color=BORDER_COLOR_FOCUSED,
        bgcolor=BACKGROUND_COLOR, border_width=1.5,
        prefix_icon=ft.Icon(ft.Icons.SEARCH, color=TEXT_SECONDARY),
        on_submit=lambda e: _page_ref.run_task(show_results_screen, e.control.value)
    )

    search_button_initial = ft.ElevatedButton(
        "Search", on_click=lambda _: _page_ref.run_task(show_results_screen, _current_search_query_field.value),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=SEARCH_BAR_RADIUS),
            padding=ft.padding.symmetric(horizontal=35, vertical=22),
            bgcolor=BUTTON_PRIMARY_BG, color=TEXT_ON_PRIMARY_ACTION
        )
    )
    
    search_area = ft.Container(
        content=ft.Row([_current_search_query_field, search_button_initial], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=750,
    )
    
    category_chips = [
        ft.Chip(
            label=ft.Text(tag.capitalize()),
            leading=ft.Icon(ft.Icons.LABEL_OUTLINE_ROUNDED),
            on_click=lambda _, t=tag: _page_ref.run_task(show_results_screen, f"tag:{t}"),
            bgcolor=ft.Colors.BLACK12
        ) for tag in _top_tags
    ]
    
    category_section = ft.Container(
        content=ft.Column([
            ft.Text("Or browse by category", size=16, color=TEXT_SECONDARY),
            ft.Container(height=5),
            ft.Row(controls=category_chips, wrap=True, spacing=10)
        ]),
        width=750,
        padding=ft.padding.only(top=30)
    ) if _top_tags else ft.Container()

    main_centered_content = ft.Column(
        [
            ft.Container(height=50),
            ft.Image(src="assets/comp_logo.png", width=100, height=100, fit=ft.ImageFit.CONTAIN, error_content=ft.Icon(ft.Icons.QUESTION_MARK, size=48)),
            ft.Container(height=15),
            ft.Text("Welcome to Your App Centre", size=28, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, text_align=ft.TextAlign.CENTER),
            ft.Text("Discover applications using semantic search.", size=16, color=TEXT_SECONDARY, text_align=ft.TextAlign.CENTER),
            ft.Container(height=40),
            search_area,
            category_section,
        ],
        alignment=ft.MainAxisAlignment.START,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=12, expand=True
    )
    
    quick_actions_panel = ft.Container(
        content=ft.ElevatedButton(
            "Browse All Recently Updated", icon=ft.Icons.STOREFRONT_ROUNDED, 
            on_click=lambda _: _page_ref.run_task(show_results_screen, ""), 
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS), 
                bgcolor=BUTTON_PRIMARY_BG, color=TEXT_ON_PRIMARY_ACTION
            )
        ),
        padding=ft.padding.all(25), bottom=0, left=0,
    )

    faded_hue_effect = ft.Container(
        width=400, height=400,
        gradient=ft.RadialGradient(
            center=ft.alignment.bottom_right, radius=1.2,
            colors=[ft.Colors.BLUE_GREY_300, ft.Colors.TRANSPARENT],
            stops=[0.0, 0.8]
        ),
        right=0, bottom=0, opacity=0.3
    )
    
    initial_screen_layout = ft.Stack([faded_hue_effect, ft.Row([main_centered_content], alignment=ft.MainAxisAlignment.CENTER), quick_actions_panel], expand=True)
    _page_ref.add(initial_screen_layout)
    _page_ref.update()

async def show_results_screen(query: str):
    global _package_list_view_ref, _current_search_query_field
    if not _page_ref: return
    _page_ref.controls.clear()
    _page_ref.appbar = create_app_bar("results")
    _page_ref.bgcolor = BACKGROUND_COLOR

    if query.lower().startswith("tag:"):
        tag = query.split(":", 1)[1]
        title_text = f"Results for tag: '{tag.capitalize()}'"
    elif not query:
        title_text = "Recently Updated Packages"
    else:
        title_text = "Search Results"

    _current_search_query_field = ft.TextField(
        value=query if not query.lower().startswith("tag:") else "",
        hint_text="Search by meaning...", expand=True, border_radius=SEARCH_BAR_RADIUS,
        content_padding=ft.padding.symmetric(horizontal=30, vertical=22), text_size=17,
        color=TEXT_PRIMARY, hint_style=ft.TextStyle(color=TEXT_SECONDARY, size=17),
        border_color=BORDER_COLOR, focused_border_color=BORDER_COLOR_FOCUSED,
        bgcolor=BACKGROUND_COLOR, border_width=1.5,
        prefix_icon=ft.Icon(ft.Icons.SEARCH, color=TEXT_SECONDARY),
        on_submit=lambda e: _page_ref.run_task(run_search_and_update_view, e.control.value)
    )

    search_button = ft.ElevatedButton(
        "Search", on_click=lambda _: _page_ref.run_task(run_search_and_update_view, _current_search_query_field.value),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=SEARCH_BAR_RADIUS),
            padding=ft.padding.symmetric(horizontal=35, vertical=22),
            bgcolor=BUTTON_PRIMARY_BG, color=TEXT_ON_PRIMARY_ACTION
        )
    )

    search_bar_row = ft.Row([_current_search_query_field, search_button], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    _package_list_view_ref = ft.ListView(expand=True, spacing=12, padding=ft.padding.only(top=15, bottom=20))

    main_column = ft.Column(
        [
            ft.Container(height=10),
            search_bar_row,
            ft.Divider(height=1, color=BORDER_COLOR),
            ft.Row([ft.Text(title_text, size=22, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY, expand=True)], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Divider(height=1, color=BORDER_COLOR),
            _package_list_view_ref
        ],
        expand=True, spacing=15
    )

    screen_layout = ft.Container(content=main_column, padding=ft.padding.symmetric(horizontal=30, vertical=20), expand=True)

    faded_hue_effect = ft.Container(
        width=400, height=400,
        gradient=ft.RadialGradient(
            center=ft.alignment.bottom_right, radius=1.2,
            colors=[ft.Colors.BLUE_GREY_300, ft.Colors.TRANSPARENT],
            stops=[0.0, 0.8]
        ),
        right=0, bottom=0, opacity=0.3
    )
    
    _page_ref.add(ft.Stack([faded_hue_effect, screen_layout], expand=True))
    _page_ref.update()
    await run_search_and_update_view(query)

async def show_queue_screen():
    if not _page_ref: return
    _page_ref.controls.clear()
    _page_ref.appbar = create_app_bar("queue")
    _page_ref.bgcolor = BACKGROUND_COLOR

    placeholder_content = ft.Column(
        [
            ft.Icon(ft.Icons.QUEUE_PLAY_NEXT_OUTLINED, size=64, color=TEXT_SECONDARY),
            ft.Text("Queue Not Available", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ft.Text("The installation queue feature is not implemented in this version.", size=16, color=TEXT_SECONDARY, text_align=ft.TextAlign.CENTER)
        ],
        alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=20, expand=True
    )
    
    faded_hue_effect = ft.Container(
        width=400, height=400,
        gradient=ft.RadialGradient(center=ft.alignment.bottom_right, radius=1.2, colors=[ft.Colors.BLUE_GREY_300, ft.Colors.TRANSPARENT], stops=[0.0, 0.8]),
        right=0, bottom=0, opacity=0.3
    )

    _page_ref.add(ft.Stack([faded_hue_effect, placeholder_content], expand=True))
    _page_ref.update()

async def main(page: ft.Page, search_worker: SearchWorker):
    global _page_ref, _global_snackbar, _search_worker
    _page_ref = page
    _search_worker = search_worker

    page.title = "Savvy App Centre (Semantic Search)"
    page.window_frameless = True
    page.window_min_width = 900
    page.window_min_height = 800
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.theme = ft.Theme(font_family="Roboto, Inter")

    _global_snackbar = ft.SnackBar(content=ft.Text(""), open=False, behavior=ft.SnackBarBehavior.FLOATING, shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS), margin=ft.margin.all(15), padding=ft.padding.symmetric(horizontal=20, vertical=15), bgcolor=BUTTON_PRIMARY_BG, duration=3000)
    page.overlay.append(_global_snackbar)
    page.overlay.append(package_detail_dialog)

    # The worker loads data when it starts; if it failed, show an error screen
    test = await asyncio.to_thread(_search_worker.search, "")
    if isinstance(test, dict) and test.get('error'):
        page.add(
            ft.Column(
                [
                    ft.Icon(name=ft.Icons.ERROR_OUTLINE_ROUNDED, color=ft.Colors.RED_600, size=50),
                    ft.Text("Application Error", size=24, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
                    ft.Text(
                        "Could not load `data.json` or `vector_index.pkl`.\nPlease ensure these files are in the same directory.",
                        text_align=ft.TextAlign.CENTER, size=16, color=TEXT_SECONDARY
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=20, expand=True
            )
        )
        page.update()
        return
    
    await show_initial_screen()

if __name__ == "__main__":
    worker = SearchWorker()

    async def app_target(page: ft.Page):
        await main(page, worker)

    try:
        ft.app(target=app_target, assets_dir="assets")
    finally:
        worker.close()
