import flet as ft
import asyncio
import logging
import threading
from typing import List, Optional, Callable
from datetime import datetime, timedelta

from data_loader import _top_tags
from ipc import SearchWorker, ChocoWorker
from typing import Awaitable
import search

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
_package_list_view_ref: Optional[ft.ListView] = None
_results_grid_ref: Optional[ft.GridView] = None
_current_search_query_field: ft.TextField = None
_queue_count_button: ft.TextButton = None
_sort_dropdown: ft.Dropdown = None
_recent_toggle: ft.Switch = None
_top_tags: List[str] = []
_search_worker: SearchWorker = None
_choco_worker: Optional[ChocoWorker] = None
_debounce_task: Optional[asyncio.Task] = None
_theme_toggle: Optional[ft.Switch] = None

# Layout state
_is_wide: bool = False  # recalculated on resize/fullscreen
_current_screen: str = "initial"  # initial | results | queue
_last_query: str = ""
_search_worker_ready: bool = False

# In-memory user features
_install_queue: List[dict] = []
_favorites: set[str] = set()
_recent_searches: List[str] = []  # keep last 6

package_detail_dialog = ft.AlertDialog(
    modal=True,
    title=ft.Text("Package Details"),
    content=ft.Text("Loading..."),
    actions_alignment=ft.MainAxisAlignment.END,
    shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS),
    bgcolor=BACKGROUND_COLOR,
    surface_tint_color=ft.Colors.WHITE,
)

settings_dialog = ft.AlertDialog(
    modal=True,
    title=ft.Text("Settings"),
    content=ft.Column(), # Content will be populated dynamically
    actions=[
        ft.TextButton("Close", on_click=lambda e: close_dialog_global(settings_dialog))
    ],
    actions_alignment=ft.MainAxisAlignment.END,
    shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS),
    bgcolor=BACKGROUND_COLOR,
)

# ---------- Helpers ----------

async def show_settings_dialog():
    """Opens the settings dialog and triggers content loading."""
    settings_dialog.content = ft.Row(
        [
            ft.ProgressRing(width=16, height=16, stroke_width=2),
            ft.Text("Checking Chocolatey status..."),
        ],
        spacing=10
    )
    settings_dialog.open = True
    _page_ref.update()

    # Get status from worker
    response = await asyncio.to_thread(_choco_worker.check_status)

    status = response.get('status')
    if status == 'choco_ok':
        version = response.get('version', 'Unknown version')
        content = ft.Row(
            [
                ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color=ft.Colors.GREEN),
                ft.Text(f"Chocolatey found (v{version})", color=TEXT_PRIMARY),
            ],
            spacing=10
        )
    else:
        message = response.get('message', 'An unknown error occurred.')
        content = ft.Row(
            [
                ft.Icon(ft.Icons.ERROR_ROUNDED, color=ft.Colors.RED),
                ft.Text(f"Chocolatey not found: {message}", color=TEXT_PRIMARY),
            ],
            spacing=10
        )

    settings_dialog.content = ft.Column([content])
    _page_ref.update()

def flow_wrap(controls: List[ft.Control], spacing: int = 6, run_spacing: int = 6) -> ft.Control:
    """Use ft.Wrap when available, else fall back to a horizontally scrollable Row."""
    try:
        # Newer Flet
        return ft.Wrap(controls=controls, spacing=spacing, run_spacing=run_spacing)
    except AttributeError:
        # Older Flet fallback
        return ft.Row(controls=controls, spacing=spacing, scroll=ft.ScrollMode.ADAPTIVE)

async def _show_snackbar_bg(message: str, bgcolor=None):
    if bgcolor is None:
        await AppNotifier.show_snackbar(message)
    else:
        await AppNotifier.show_snackbar(message, bgcolor=bgcolor)

class Debouncer:
    """Thread-safe debouncer that schedules the async callback via page.run_task."""
    def __init__(self, delay_ms: int, coro_callback: Callable[[str], Awaitable[None]]):
        self.delay = delay_ms / 1000
        self.coro_callback = coro_callback
        self._timer: Optional[threading.Timer] = None
        self._last_value: Optional[str] = None

    def trigger(self, value: str):
        self._last_value = value
        if self._timer:
            self._timer.cancel()

        def _fire():
            # Always hop to Flet's asyncio loop
            if _page_ref:
                _page_ref.run_task(self.coro_callback, self._last_value)

        self._timer = threading.Timer(self.delay, _fire)
        self._timer.daemon = True
        self._timer.start()

_debouncer: Optional[Debouncer] = None


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
    if not _page_ref:
        return

    async def show_related_package(new_pkg_data: dict):
        close_dialog_global(package_detail_dialog)
        await asyncio.sleep(0.05)
        await show_package_details_global(new_pkg_data)

    title = pkg_data.get("SoftwareTitle", "Details")
    is_fav = title in _favorites
    fav_icon = ft.Icons.STAR_ROUNDED if is_fav else ft.Icons.STAR_BORDER_ROUNDED

    package_detail_dialog.title = ft.Row([
        ft.Text(title, weight=ft.FontWeight.BOLD, size=20, color=TEXT_PRIMARY, expand=True),
        ft.IconButton(
            icon=fav_icon,
            tooltip="Toggle favorite",
            on_click=lambda e: toggle_favorite(title, from_dialog=True),
        ),
    ])

    details_column = [
        ft.Text(f"Version: {pkg_data.get('Version', 'N/A')}", color=TEXT_SECONDARY, size=14),
        ft.Text(f"Last Updated: {search.format_timestamp(pkg_data.get('LastUpdated'))}", color=TEXT_SECONDARY, size=14),
        ft.Container(height=15),
        ft.Text("Summary:", weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, size=16),
        ft.Text(pkg_data.get("Summary", "Not available."), color=TEXT_PRIMARY, size=15, selectable=True),
        ft.Container(height=15),
        ft.Text(f"Tags: {pkg_data.get('Tags', 'N/A')}", italic=True, color=TEXT_SECONDARY, size=14),
        ft.Divider(height=25, color=BORDER_COLOR),
        ft.Row(
            [
                ft.ElevatedButton(
                    "Install",
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    on_click=lambda e, d=pkg_data: add_to_queue(d),
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS), bgcolor=BUTTON_PRIMARY_BG, color=TEXT_ON_PRIMARY_ACTION),
                ),
                ft.OutlinedButton(
                    "Copy Title",
                    icon=ft.Icons.CONTENT_COPY_ROUNDED,
                    on_click=lambda e: copy_to_clipboard(title),
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS)),
                ),
            ],
            spacing=10,
        ),
    ]

    related_items_container = ft.Container()
    details_content = ft.Column(
        controls=details_column + [related_items_container],
        tight=True,
        scroll=ft.ScrollMode.ADAPTIVE,
        height=420,
        spacing=6,
    )
    package_detail_dialog.content = details_content
    package_detail_dialog.actions = [
        ft.TextButton(
            "Close",
            on_click=lambda _: close_dialog_global(package_detail_dialog),
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS), color=TEXT_SECONDARY),
        )
    ]
    package_detail_dialog.open = True
    _page_ref.update()

    # Related packages async fill
    related_items_container.content = ft.Row(
        [ft.ProgressRing(width=16, height=16, stroke_width=2), ft.Text("Finding related items...", color=TEXT_SECONDARY, size=14)],
        spacing=10,
    )
    _page_ref.update()

    related_packages = await asyncio.to_thread(_search_worker.find_related, pkg_data)

    if related_packages:
        suggestion_chips = [
            ft.Chip(
                label=ft.Text(rel_pkg.get("SoftwareTitle", "Unknown")),
                leading=ft.Icon(ft.Icons.SETTINGS_APPLICATIONS_ROUNDED, size=16),
                on_click=lambda _, pkg=rel_pkg: _page_ref.run_task(show_related_package, pkg),
                tooltip=f"View details for {rel_pkg.get('SoftwareTitle')}",
                bgcolor=BUTTON_SECONDARY_BG,
            )
            for rel_pkg in related_packages
        ]

        related_items_container.content = ft.Column(
            [
                ft.Text("You might also like...", weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, size=16),
                ft.Container(height=8),
                flow_wrap(suggestion_chips, spacing=10, run_spacing=10),
            ]
        )
    else:
        related_items_container.content = None

    _page_ref.update()


def toggle_favorite(title: str, from_dialog: bool = False):
    if title in _favorites:
        _favorites.remove(title)
        if _page_ref:
            _page_ref.run_task(_show_snackbar_bg, f"Removed '{title}' from favorites", ft.Colors.GREY_800)
    else:
        _favorites.add(title)
        if _page_ref:
            _page_ref.run_task(_show_snackbar_bg, f"Added '{title}' to favorites")
    if from_dialog:
        package_detail_dialog.title.controls[-1].icon = ft.Icons.STAR_ROUNDED if title in _favorites else ft.Icons.STAR_BORDER_ROUNDED
        _page_ref.update()


def copy_to_clipboard(text: str):
    if not _page_ref:
        return
    _page_ref.set_clipboard(text)
    _page_ref.run_task(_show_snackbar_bg, "Copied to clipboard")


def add_to_queue(pkg_data: dict):
    title = pkg_data.get("SoftwareTitle", "Unknown")
    if any(item.get("SoftwareTitle") == title for item in _install_queue):
        if _page_ref:
            _page_ref.run_task(_show_snackbar_bg, "Already in queue", ft.Colors.GREY_800)
        return
    _install_queue.append(pkg_data)
    update_queue_badge()
    if _page_ref:
        _page_ref.run_task(_show_snackbar_bg, f"Queued '{title}' for install")


def remove_from_queue(title: str):
    global _install_queue
    _install_queue = [i for i in _install_queue if i.get("SoftwareTitle") != title]
    update_queue_badge()
    if _page_ref.route == "/queue":
        _page_ref.run_task(show_queue_screen)


def update_queue_badge():
    if _queue_count_button:
        _queue_count_button.text = f"Queue ({len(_install_queue)})"
        _page_ref.update()


# UI builders

def create_package_list_item(pkg_data: dict):
    title = pkg_data.get("SoftwareTitle", "Unknown Package")
    summary = pkg_data.get("Summary", "No summary available.")
    version = pkg_data.get("Version", "N/A")
    tags = (pkg_data.get("Tags") or "").split()
    is_fav = title in _favorites

    tag_chips = [
        ft.Container(
            content=ft.Text(t, size=11, color=TEXT_SECONDARY),
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border=ft.border.all(1, BORDER_COLOR),
            border_radius=999,
        )
        for t in tags[:3]
    ]

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.SETTINGS_APPLICATIONS_ROUNDED, color=TEXT_PRIMARY, size=28),
                        ft.Column(
                            [
                                ft.Row([
                                    ft.Text(title, weight=ft.FontWeight.W_500, size=16, color=TEXT_PRIMARY, expand=True),
                                    ft.IconButton(
                                        icon=ft.Icons.STAR_ROUNDED if is_fav else ft.Icons.STAR_BORDER_ROUNDED,
                                        tooltip="Favorite",
                                        on_click=lambda e, t=title: toggle_favorite(t),
                                    ),
                                ]),
                                ft.Text(f"v{version} – {summary}", size=13, color=TEXT_SECONDARY, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                                flow_wrap(tag_chips, spacing=6, run_spacing=6),
                            ],
                            spacing=3,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                            tooltip="Details",
                            on_click=lambda e: _page_ref.run_task(show_package_details_global, pkg_data),
                        ),
                        ft.FilledButton(
                            text="Install",
                            icon=ft.Icons.DOWNLOAD_ROUNDED,
                            on_click=lambda e, d=pkg_data: add_to_queue(d),
                            style=ft.ButtonStyle(
                                shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS),
                                bgcolor=BUTTON_PRIMARY_BG,
                                color=TEXT_ON_PRIMARY_ACTION,
                                padding=ft.padding.symmetric(horizontal=16, vertical=12),
                            ),
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ]
        ),
        padding=ft.padding.symmetric(vertical=14, horizontal=18),
        border_radius=BUTTON_RADIUS,
        ink=True,
        border=ft.border.all(1, BORDER_COLOR),
    )


async def run_search_and_update_view(query: str):
    global _last_query
    _last_query = query
    if not _page_ref or (not _package_list_view_ref and not _results_grid_ref):
        return

    # Save recent query
    if query and (not _recent_searches or _recent_searches[-1] != query):
        _recent_searches.append(query)
        if len(_recent_searches) > 6:
            _recent_searches.pop(0)

    container = _results_grid_ref or _package_list_view_ref
    container.controls.clear()

    loading_indicator = ft.Row(
        [ft.ProgressRing(width=20, height=20, stroke_width=2.5, color=TEXT_PRIMARY), ft.Text("Searching...", color=TEXT_SECONDARY)],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )
    container.controls.append(loading_indicator)
    _page_ref.update()

    results = await asyncio.to_thread(_search_worker.search, query)

    # Filters/sort
    results = results or []

    if _recent_toggle and _recent_toggle.value:
        cutoff = datetime.utcnow() - timedelta(days=90)
        def _is_recent(pkg: dict):
            ts = pkg.get("LastUpdated")
            try:
                dt = datetime.utcfromtimestamp(ts) if isinstance(ts, (int, float)) else datetime.fromisoformat(str(ts))
                return dt >= cutoff
            except Exception:
                return False
        results = [r for r in results if _is_recent(r)]

    if _sort_dropdown:
        if _sort_dropdown.value == "az":
            results.sort(key=lambda x: (x.get("SoftwareTitle") or "").lower())
        elif _sort_dropdown.value == "za":
            results.sort(key=lambda x: (x.get("SoftwareTitle") or "").lower(), reverse=True)
        else:
            results.sort(key=lambda x: x.get("LastUpdated") or 0, reverse=True)

    container.controls.clear()

    if results:
        for pkg_data in results:
            item = create_package_list_item(pkg_data)
            container.controls.append(item)
    elif query.strip():
        if query.lower().startswith("tag:"):
            tag = query.split(":", 1)[1]
            container.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Text(f"No packages found with the tag '{tag}'.", text_align=ft.TextAlign.CENTER, color=TEXT_SECONDARY, italic=True, size=14),
                        ft.Container(height=8),
                        ft.Text("Try removing filters or a different tag.", size=12, color=TEXT_SECONDARY, text_align=ft.TextAlign.CENTER),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.all(30),
                )
            )
        else:
            chips = [ft.Chip(label=ft.Text(s), on_click=lambda e, q=s: _page_ref.run_task(run_search_and_update_view, q)) for s in reversed(_recent_searches)]
            container.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Text(f"No results found for '{query}'.", text_align=ft.TextAlign.CENTER, color=TEXT_SECONDARY, italic=True, size=14),
                        ft.Container(height=8),
                        ft.Text("Recent searches:", size=12, color=TEXT_SECONDARY),
                        flow_wrap(chips),
                    ]),
                    padding=ft.padding.all(30),
                )
            )
    else:
        container.controls.append(
            ft.Container(
                content=ft.Text(
                    "No packages found. The data source might be empty.",
                    text_align=ft.TextAlign.CENTER,
                    color=TEXT_SECONDARY,
                    italic=True,
                    size=14,
                ),
                padding=ft.padding.all(30),
            )
        )

    _page_ref.update()


# --- Screen Definitions ---

def _keyboard_handler(e: ft.KeyboardEvent):
    if e.key == "k" and (e.ctrl or e.meta):
        if _current_search_query_field:
            _current_search_query_field.focus()
            _page_ref.update()
    if e.key == "escape" and _current_search_query_field:
        _current_search_query_field.value = ""
        _page_ref.update()


def create_app_bar(current_screen: str):
    global _queue_count_button, _theme_toggle
    if not _queue_count_button:
        _queue_count_button = ft.TextButton(
            "Queue (0)",
            icon=ft.Icons.QUEUE_ROUNDED,
            on_click=lambda _: _page_ref.run_task(show_queue_screen),
            style=ft.ButtonStyle(color=TEXT_PRIMARY, shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS)),
        )

    if not _theme_toggle:
        _theme_toggle = ft.Switch(value=False, on_change=_toggle_theme, tooltip="Dark mode")

    # Define this unconditionally so it's always available
    def toggle_fullscreen(e):
        # Support different Flet builds: window_full_screen (desktop) or window_maximized (fallback)
        current_full = False
        if hasattr(_page_ref, "window_full_screen"):
            current_full = bool(getattr(_page_ref, "window_full_screen") or False)
            setattr(_page_ref, "window_full_screen", not current_full)
            current_full = not current_full
        elif hasattr(_page_ref, "window_maximized"):
            current_full = bool(getattr(_page_ref, "window_maximized") or False)
            setattr(_page_ref, "window_maximized", not current_full)
            current_full = not current_full
        # Update icon based on new state
        fullscreen_button.icon = ft.Icons.FULLSCREEN_EXIT_ROUNDED if current_full else ft.Icons.FULLSCREEN_ROUNDED
        _sync_layout_with_window()
        _page_ref.update()

    minimize_button = ft.IconButton(icon=ft.Icons.MINIMIZE_ROUNDED, icon_size=16, on_click=lambda _: _page_ref.window_minimize(), tooltip="Minimize")
    fullscreen_button = ft.IconButton(icon=ft.Icons.FULLSCREEN_ROUNDED, icon_size=16, on_click=toggle_fullscreen, tooltip="Toggle Fullscreen")
    close_button = ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_size=16, on_click=lambda _: _page_ref.window_destroy(), tooltip="Close", style=ft.ButtonStyle(color=TEXT_SECONDARY, overlay_color={"hovered": ft.Colors.RED_700}))

    return ft.AppBar(
        leading=ft.IconButton(ft.Icons.HOME_ROUNDED, tooltip="Home", icon_color=TEXT_PRIMARY, on_click=lambda _: _page_ref.run_task(show_initial_screen)) if current_screen != "initial" else None,
        leading_width=80,
        title=ft.WindowDragArea(content=ft.Container(content=ft.Text("Savvy App Centre", weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY), alignment=ft.alignment.center), expand=True),
        center_title=True,
        bgcolor=BACKGROUND_COLOR,
        actions=[
            ft.Container(content=ft.Text("Search Mode", color=TEXT_SECONDARY, size=12), alignment=ft.alignment.center, padding=ft.padding.only(right=12)),
            _queue_count_button,
            ft.TextButton("Installed", icon=ft.Icons.HISTORY_ROUNDED, on_click=lambda _: _page_ref.run_task(show_installed_screen)),
            ft.Container(width=10),
            ft.Icon(ft.Icons.DARK_MODE_ROUNDED, color=TEXT_SECONDARY, size=18),
            _theme_toggle,
            ft.IconButton(
                icon=ft.Icons.SETTINGS_ROUNDED,
                tooltip="Settings",
                on_click=lambda _: _page_ref.run_task(show_settings_dialog),
            ),
            ft.Container(width=10),
            minimize_button,
            fullscreen_button,
            close_button,
            ft.Container(width=10),
        ],
        toolbar_height=70,
        elevation=1,
        surface_tint_color=ft.Colors.WHITE,
    )


def _toggle_theme(e: ft.ControlEvent):
    if not _page_ref:
        return
    _page_ref.theme_mode = ft.ThemeMode.DARK if e.control.value else ft.ThemeMode.LIGHT
    _page_ref.update()


async def show_initial_screen():
    global _current_search_query_field, _debouncer, _current_screen
    if not _page_ref:
        return
    _current_screen = "initial"
    _page_ref.controls.clear()
    _page_ref.appbar = create_app_bar("initial")
    _page_ref.bgcolor = BACKGROUND_COLOR

    def on_change(e: ft.ControlEvent):
        if _debouncer and _search_worker_ready:
            _debouncer.trigger(e.control.value)

    _current_search_query_field = ft.TextField(
        hint_text="Loading search model..." if not _search_worker_ready else "Search for applications...",
        expand=True,
        border_radius=SEARCH_BAR_RADIUS,
        content_padding=ft.padding.symmetric(horizontal=30, vertical=22),
        text_size=17,
        color=TEXT_PRIMARY,
        hint_style=ft.TextStyle(color=TEXT_SECONDARY, size=17),
        border_color=BORDER_COLOR,
        focused_border_color=BORDER_COLOR_FOCUSED,
        bgcolor=BACKGROUND_COLOR,
        border_width=1.5,
        prefix_icon=ft.Icon(ft.Icons.SEARCH, color=TEXT_SECONDARY),
        on_submit=lambda e: _page_ref.run_task(show_results_screen, e.control.value),
        on_change=on_change,
        disabled=not _search_worker_ready,
    )

    _debouncer = Debouncer(400, preview_live_results)

    search_button_initial = ft.ElevatedButton(
        "Search",
        on_click=lambda _: _page_ref.run_task(show_results_screen, _current_search_query_field.value),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=SEARCH_BAR_RADIUS),
            padding=ft.padding.symmetric(horizontal=35, vertical=22),
            bgcolor=BUTTON_PRIMARY_BG,
            color=TEXT_ON_PRIMARY_ACTION,
        ),
        disabled=not _search_worker_ready,
    )

    search_area = ft.Container(
        content=ft.Row([_current_search_query_field, search_button_initial], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=750 if not _is_wide else None,
        expand=_is_wide,
    )

    category_chips = [
        ft.Chip(
            label=ft.Text(tag.capitalize()),
            leading=ft.Icon(ft.Icons.LABEL_OUTLINE_ROUNDED),
            on_click=lambda _, t=tag: _page_ref.run_task(show_results_screen, f"tag:{t}"),
            bgcolor=ft.Colors.BLACK12,
        )
        for tag in _top_tags
    ]

    category_section = (
        ft.Container(
            content=ft.Column(
                [
                    ft.Text("Or browse by category", size=16, color=TEXT_SECONDARY),
                    ft.Container(height=5),
                    flow_wrap(category_chips, spacing=10, run_spacing=10),
                ]
            ),
            width=750 if not _is_wide else None,
            padding=ft.padding.only(top=30),
        )
        if _top_tags
        else ft.Container()
    )

    fav_section = (
        ft.Container(
            content=ft.Column([
                ft.Text("Favorites", size=16, color=TEXT_SECONDARY),
                ft.Container(height=6),
                flow_wrap([ft.Chip(label=ft.Text(t), on_click=lambda e, q=t: _page_ref.run_task(show_results_screen, q)) for t in sorted(_favorites)]),
            ]),
            width=750 if not _is_wide else None,
            padding=ft.padding.only(top=10),
        )
        if _favorites
        else ft.Container()
    )

    if _is_wide:
        left = ft.Column([
            ft.Container(height=30),
            ft.Image(src="assets/comp_logo.png", width=120, height=120, fit=ft.ImageFit.CONTAIN, error_content=ft.Icon(ft.Icons.QUESTION_MARK, size=48)),
            ft.Container(height=10),
            ft.Text("Welcome to Your App Centre", size=34, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ft.Text("Discover applications using semantic search.", size=16, color=TEXT_SECONDARY),
            ft.Container(height=20),
            search_area,
            ft.Container(height=10),
            category_section,
            ft.Container(height=10),
            fav_section,
        ], expand=True, spacing=10)

        tips = ft.Column([
            ft.Text("Pro tips", size=16, weight=ft.FontWeight.W_600),
            ft.Text("• Press Ctrl/Cmd+K to focus search", color=TEXT_SECONDARY, size=12),
            ft.Text("• Type tag:devtools to filter by tag", color=TEXT_SECONDARY, size=12),
            ft.Text("• Use A→Z sort on results", color=TEXT_SECONDARY, size=12),
            ft.Container(height=10),
            ft.Text("Recent searches", size=16, weight=ft.FontWeight.W_600),
            flow_wrap(
                [ft.Chip(label=ft.Text(s), on_click=lambda e, q=s: _page_ref.run_task(show_results_screen, q)) for s in reversed(_recent_searches)],
                spacing=6, run_spacing=6
            ),
        ], spacing=6)

        right_card = ft.Container(
            content=tips,
            padding=ft.padding.all(20),
            border_radius=BUTTON_RADIUS,
            border=ft.border.all(1, BORDER_COLOR),
            width=380,
        )

        main_centered_content = ft.Container(
            content=ft.Row([left, right_card], spacing=30, expand=True),
            padding=ft.padding.symmetric(horizontal=40),
            expand=True,
        )
    else:
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
                fav_section,
            ],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
            expand=True,
        )

    quick_actions_panel = ft.Container(
        content=ft.Row([
            ft.ElevatedButton(
                "Browse All Recently Updated",
                icon=ft.Icons.STOREFRONT_ROUNDED,
                on_click=lambda _: _page_ref.run_task(show_results_screen, ""),
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS), bgcolor=BUTTON_PRIMARY_BG, color=TEXT_ON_PRIMARY_ACTION),
            ),
            ft.OutlinedButton(
                "Open Queue",
                icon=ft.Icons.QUEUE_ROUNDED,
                on_click=lambda _: _page_ref.run_task(show_queue_screen),
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS)),
            ),
        ], spacing=10),
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
    _page_ref.on_keyboard_event = _keyboard_handler
    _page_ref.add(initial_screen_layout)
    _page_ref.update()


async def preview_live_results(q: str):
    # optional: show quick live preview dropdown; currently disabled
    pass


async def show_results_screen(query: str):
    global _package_list_view_ref, _results_grid_ref, _current_search_query_field, _sort_dropdown, _recent_toggle, _debouncer, _current_screen, _last_query
    if not _page_ref:
        return
    _current_screen = "results"
    _last_query = query
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

    def on_change(e: ft.ControlEvent):
        if _debouncer and _search_worker_ready:
            _debouncer.trigger(e.control.value)

    _current_search_query_field = ft.TextField(
        value=query if not query.lower().startswith("tag:") else "",
        hint_text="Loading search model..." if not _search_worker_ready else "Search by meaning...",
        expand=True,
        border_radius=SEARCH_BAR_RADIUS,
        content_padding=ft.padding.symmetric(horizontal=30, vertical=22),
        text_size=17,
        color=TEXT_PRIMARY,
        hint_style=ft.TextStyle(color=TEXT_SECONDARY, size=17),
        border_color=BORDER_COLOR,
        focused_border_color=BORDER_COLOR_FOCUSED,
        bgcolor=BACKGROUND_COLOR,
        border_width=1.5,
        prefix_icon=ft.Icon(ft.Icons.SEARCH, color=TEXT_SECONDARY),
        on_submit=lambda e: _page_ref.run_task(run_search_and_update_view, e.control.value),
        on_change=on_change,
        disabled=not _search_worker_ready,
    )

    _debouncer = Debouncer(400, run_search_and_update_view)

    search_button = ft.ElevatedButton(
        "Search",
        on_click=lambda _: _page_ref.run_task(run_search_and_update_view, _current_search_query_field.value),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=SEARCH_BAR_RADIUS),
            padding=ft.padding.symmetric(horizontal=35, vertical=22),
            bgcolor=BUTTON_PRIMARY_BG,
            color=TEXT_ON_PRIMARY_ACTION,
        ),
        disabled=not _search_worker_ready,
    )

    _sort_dropdown = ft.Dropdown(
        value="updated",
        options=[
            ft.dropdown.Option(key="updated", text="Last updated"),
            ft.dropdown.Option(key="az", text="A → Z"),
            ft.dropdown.Option(key="za", text="Z → A"),
        ],
        on_change=lambda e: _page_ref.run_task(run_search_and_update_view, _current_search_query_field.value),
        width=165,
    )

    _recent_toggle = ft.Switch(label="Updated in last 90 days", value=False, on_change=lambda e: _page_ref.run_task(run_search_and_update_view, _current_search_query_field.value))

    filter_row = ft.Row([
        ft.Text("Sort:", color=TEXT_SECONDARY),
        _sort_dropdown,
        ft.Container(width=20),
        _recent_toggle,
    ], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    search_bar_row = ft.Row([_current_search_query_field, search_button], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # List vs Grid
    if _is_wide:
        _results_grid_ref = ft.GridView(expand=True, runs_count=0, max_extent=420, child_aspect_ratio=2.8, spacing=12, run_spacing=12)
        _package_list_view_ref = None
    else:
        _package_list_view_ref = ft.ListView(expand=True, spacing=12, padding=ft.padding.only(top=10, bottom=20))
        _results_grid_ref = None

    # Recent searches chips under bar
    recent_chip_row = flow_wrap(
        [ft.Chip(label=ft.Text(s), on_click=lambda e, q=s: _page_ref.run_task(run_search_and_update_view, q)) for s in reversed(_recent_searches)]
    )

    results_area = _results_grid_ref if _is_wide else _package_list_view_ref

    main_column = ft.Column(
        [
            ft.Container(height=10),
            search_bar_row,
            ft.Container(height=8),
            recent_chip_row if _recent_searches else ft.Container(),
            ft.Divider(height=1, color=BORDER_COLOR),
            ft.Row([ft.Text(title_text, size=22, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY, expand=True), filter_row], vertical_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Divider(height=1, color=BORDER_COLOR),
            ft.Row([
                ft.Container(
                    content=ft.Column([
                        ft.Text("Filters", size=16, weight=ft.FontWeight.W_600),
                        filter_row,
                        ft.Container(height=10),
                        ft.Text("Top tags", size=14, weight=ft.FontWeight.W_600, color=TEXT_SECONDARY),
                        flow_wrap([ft.Chip(label=ft.Text(t.capitalize()), on_click=lambda e, tt=t: _page_ref.run_task(show_results_screen, f"tag:{tt}")) for t in _top_tags], spacing=6, run_spacing=6),
                        ft.Container(height=10),
                        ft.Text("Favorites", size=14, weight=ft.FontWeight.W_600, color=TEXT_SECONDARY),
                        flow_wrap([ft.Chip(label=ft.Text(t), on_click=lambda e, q=t: _page_ref.run_task(show_results_screen, q)) for t in sorted(_favorites)], spacing=6, run_spacing=6),
                    ], spacing=8),
                    width=260,
                    visible=_is_wide,
                ),
                ft.VerticalDivider(width=1, color=BORDER_COLOR) if _is_wide else ft.Container(),
                ft.Container(results_area, expand=True),
            ], expand=True, vertical_alignment=ft.CrossAxisAlignment.START),
        ],
        expand=True,
        spacing=10,
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

    _page_ref.on_keyboard_event = _keyboard_handler
    _page_ref.add(ft.Stack([faded_hue_effect, screen_layout], expand=True))
    _page_ref.update()
    await run_search_and_update_view(query)


class QueueItem:
    def __init__(self, pkg_data: dict):
        self.pkg_data = pkg_data
        self.pkg_title = pkg_data.get("SoftwareTitle", "Unknown")
        self.pkg_id = pkg_data.get("PackageIdentifier")
        self.status = "Pending"

        self.status_icon = ft.Icon(ft.Icons.DOWNLOAD_ROUNDED)
        self.status_text = ft.Text(self.status, italic=True, color=TEXT_SECONDARY)
        self.retry_button = ft.IconButton(icon=ft.Icons.REFRESH_ROUNDED, visible=False, on_click=self.install)
        self.remove_button = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE_ROUNDED, on_click=self.remove)

        self.view = ft.Container(
            content=ft.Row([
                self.status_icon,
                ft.Text(self.pkg_title, expand=True),
                self.status_text,
                self.retry_button,
                self.remove_button,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            padding=ft.padding.symmetric(vertical=10, horizontal=14),
            border=ft.border.all(1, BORDER_COLOR),
            border_radius=BUTTON_RADIUS,
        )

    async def install(self, e=None):
        if not self.pkg_id:
            self.status = "Failed"
            self.status_icon.name = ft.Icons.ERROR_ROUNDED
            self.status_icon.color = ft.Colors.RED
            self.status_text.value = "Missing Package ID"
            await _page_ref.update_async()
            return

        self.status = "Installing"
        self.status_icon = ft.ProgressRing(width=16, height=16, stroke_width=2)
        self.status_text.value = self.status
        self.retry_button.visible = False
        self.remove_button.disabled = True
        await _page_ref.update_async()

        response = await asyncio.to_thread(_choco_worker.execute, 'install', self.pkg_id, self.pkg_title)

        if response.get("status") == "success":
            self.status = "Success"
            self.status_icon = ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color=ft.Colors.GREEN)
            self.status_text.value = self.status
            self.remove_button.disabled = False
            await _page_ref.update_async()
            await asyncio.sleep(2)
            remove_from_queue(self.pkg_title)
        else:
            self.status = "Failed"
            self.status_icon = ft.Icon(ft.Icons.ERROR_ROUNDED, color=ft.Colors.RED)
            self.status_text.value = self.status
            self.retry_button.visible = True
            self.remove_button.disabled = False
            await AppNotifier.show_snackbar(response.get('message', 'An unknown error occurred.'), bgcolor=ft.Colors.RED_800)
            await _page_ref.update_async()

    def remove(self, e):
        remove_from_queue(self.pkg_title)

    def build(self):
        return self.view


async def show_queue_screen():
    global _current_screen
    if not _page_ref: return
    _current_screen = "queue"
    _page_ref.controls.clear()
    _page_ref.appbar = create_app_bar("queue")
    _page_ref.bgcolor = BACKGROUND_COLOR

    queue_list_view = ft.ListView(expand=True, spacing=10)
    if _install_queue:
        for pkg in _install_queue:
            queue_list_view.controls.append(QueueItem(pkg))

    progress_bar = ft.ProgressBar(width=400, value=0)
    overall_status_text = ft.Text("Ready.")

    async def install_all(e):
        install_button.disabled = True
        clear_button.disabled = True

        total_items = len(queue_list_view.controls)
        installed_count = 0

        for i, item in enumerate(queue_list_view.controls):
            if isinstance(item, QueueItem) and item.status == "Pending":
                progress_bar.value = (i + 1) / total_items
                overall_status_text.value = f"Installing {item.pkg_title}..."
                await item.install()
                if item.status == "Success":
                    installed_count += 1

        overall_status_text.value = f"Finished. {installed_count}/{total_items} installed successfully."
        install_button.disabled = False
        clear_button.disabled = False
        await _page_ref.update_async()

    install_button = ft.FilledButton("Install All", icon=ft.Icons.PLAYLIST_ADD_CHECK_CIRCLE_ROUNDED, on_click=install_all, style=ft.ButtonStyle(bgcolor=BUTTON_PRIMARY_BG, color=TEXT_ON_PRIMARY_ACTION, shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS)))

    def _clear_queue(e):
        _install_queue.clear()
        update_queue_badge()
        _page_ref.run_task(show_queue_screen)

    clear_button = ft.OutlinedButton("Clear Queue", icon=ft.Icons.DELETE_SWEEP_ROUNDED, on_click=_clear_queue)

    if not _install_queue:
        _page_ref.add(ft.Column([
            ft.Icon(ft.Icons.QUEUE_PLAY_NEXT_OUTLINED, size=64, color=TEXT_SECONDARY),
            ft.Text("Queue is empty", size=22, weight=ft.FontWeight.BOLD),
            ft.Text("Find apps and click Install to queue them.", size=16, text_align=ft.TextAlign.CENTER),
        ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True))
    else:
        actions = ft.Row([
            install_button, clear_button, progress_bar, overall_status_text
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        layout = ft.Column([
            ft.Container(height=8),
            ft.Text("Installation Queue", size=22, weight=ft.FontWeight.W_600),
            ft.Container(height=6),
            queue_list_view,
            ft.Divider(color=BORDER_COLOR),
            actions,
        ], expand=True)
        _page_ref.add(ft.Container(layout, padding=ft.padding.symmetric(horizontal=30, vertical=20), expand=True))

    await _page_ref.update_async()


async def show_installed_screen():
    global _current_screen
    if not _page_ref:
        return
    _current_screen = "installed"
    _page_ref.controls.clear()
    _page_ref.appbar = create_app_bar("installed")
    _page_ref.bgcolor = BACKGROUND_COLOR

    loading_indicator = ft.Row(
        [ft.ProgressRing(width=16, height=16, stroke_width=2), ft.Text("Loading installed packages...")],
        alignment=ft.MainAxisAlignment.CENTER
    )

    layout = ft.Column([
        ft.Container(height=8),
        ft.Text("Installed Packages", size=22, weight=ft.FontWeight.W_600),
        ft.Container(height=6),
        loading_indicator,
    ], expand=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    _page_ref.add(ft.Container(layout, padding=ft.padding.symmetric(horizontal=30, vertical=20), expand=True))
    _page_ref.update()

    response = await asyncio.to_thread(_choco_worker.list_installed)

    layout.controls.remove(loading_indicator)

    if response.get('status') == 'success':
        packages = response.get('packages', [])
        if packages:
            list_view = ft.ListView(expand=True, spacing=10)
            for pkg in packages:
                list_view.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, color=ft.Colors.GREEN),
                            ft.Text(pkg.get('name'), weight=ft.FontWeight.BOLD),
                            ft.Text(f"v{pkg.get('version')}"),
                        ], spacing=10),
                        padding=ft.padding.symmetric(vertical=10, horizontal=14),
                        border=ft.border.all(1, BORDER_COLOR),
                        border_radius=BUTTON_RADIUS,
                    )
                )
            layout.controls.append(list_view)
        else:
            layout.controls.append(ft.Text("No locally installed packages found."))
    else:
        error_message = response.get('message', 'An unknown error occurred.')
        layout.controls.append(ft.Text(f"Error loading packages: {error_message}", color=ft.Colors.RED))

    _page_ref.update()


async def listen_for_worker_status():
    """Waits for the ready signal from the search worker and refreshes the UI."""
    global _search_worker_ready

    # This runs in the background, waiting for the first message from the worker
    response = await asyncio.to_thread(_search_worker.response_q.get)

    if isinstance(response, dict) and response.get('status') == 'ready':
        _search_worker_ready = True
        # Refresh the current screen to enable search controls
        if _current_screen == "initial":
            await show_initial_screen()
        elif _current_screen == "results":
            await show_results_screen(_last_query)
        # Trigger an initial search to populate the default view
        await run_search_and_update_view("")

    elif isinstance(response, dict) and response.get('status') == 'error':
        # The worker failed to load, show a persistent error
        if _current_search_query_field:
            _current_search_query_field.hint_text = "Error: Search model failed to load."
            _current_search_query_field.update()
        await AppNotifier.show_snackbar("Critical Error: The search model failed to load.", bgcolor=ft.Colors.RED_800, duration=10000)


async def main(page: ft.Page, search_worker: SearchWorker, choco_worker: ChocoWorker):
    global _page_ref, _global_snackbar, _search_worker, _choco_worker
    _page_ref = page
    _search_worker = search_worker
    _choco_worker = choco_worker

    page.title = "Savvy App Centre (Semantic Search)"
    page.window_frameless = True
    page.window_min_width = 900
    page.window_min_height = 800
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.theme = ft.Theme(font_family="Roboto, Inter")
    page.theme_mode = ft.ThemeMode.LIGHT

    _global_snackbar = ft.SnackBar(
        content=ft.Text(""),
        open=False,
        behavior=ft.SnackBarBehavior.FLOATING,
        shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS),
        margin=ft.margin.all(15),
        padding=ft.padding.symmetric(horizontal=20, vertical=15),
        bgcolor=BUTTON_PRIMARY_BG,
        duration=3000,
    )
    page.overlay.append(_global_snackbar)
    page.overlay.append(package_detail_dialog)
    page.overlay.append(settings_dialog)

    def on_resize(e: ft.ControlEvent):
        _sync_layout_with_window()
    page.on_resize = on_resize

    # Immediately show the UI without blocking
    _sync_layout_with_window()
    await show_initial_screen()

    # Start a background task to listen for the worker's ready signal
    page.run_task(listen_for_worker_status)


def _sync_layout_with_window():
    global _is_wide
    if not _page_ref:
        return
    # Get best-available fullscreen/maximized state and width
    is_full = getattr(_page_ref, "window_full_screen", None)
    if is_full is None:
        is_full = getattr(_page_ref, "window_maximized", False)
    win_w = getattr(_page_ref, "window_width", 0) or 0
    new_is_wide = bool(is_full) or (win_w >= 1280)
    if new_is_wide != _is_wide:
        _is_wide = new_is_wide
        if _current_screen == "initial":
            _page_ref.run_task(show_initial_screen)
        elif _current_screen == "results":
            _page_ref.run_task(show_results_screen, _last_query)
        elif _current_screen == "queue":
            _page_ref.run_task(show_queue_screen)


if __name__ == "__main__":
    worker = SearchWorker()

    async def app_target(page: ft.Page):
        await main(page, worker)

    try:
        ft.app(target=app_target, assets_dir="assets")
    finally:
        worker.close()
