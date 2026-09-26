"""Sprawdzenia witryny konkursu po imporcie – ``verify_cutover`` (DJ-02 § 10.3) i ``dj_pages.W003`` (S16).

Funkcje bez sieci i bez żądań: czytają bazę djcms (strony opublikowane, adresy, przekierowania).
Część „czy adres odpowiada 200” robi komenda ``verify_cutover`` klientem testowym Django.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LANGUAGE = "pl"


def _published_page_ids(site) -> set[int]:
    """Strony witryny z opublikowaną wersją (``PageContent.objects`` pod versioningiem = opublikowane)."""
    from cms.models import PageContent

    return set(
        PageContent.objects.filter(page__site=site, language=LANGUAGE).values_list("page_id", flat=True)
    )


def published_paths(site) -> dict[str, int]:
    """``{"/zadania/": page_id}`` opublikowanych stron witryny (``"/"`` – strona główna)."""
    from cms.models import PageUrl

    published = _published_page_ids(site)
    paths = {}
    for page_id, path in PageUrl.objects.filter(page__site=site, language=LANGUAGE).values_list(
        "page_id", "path"
    ):
        if page_id in published:
            paths[f"/{path}/" if path else "/"] = page_id
    return paths


def normalise_page_path(path: str) -> str:
    """``"/dokumenty/regulamin"`` → ``"/dokumenty/regulamin/"``; bez zapytania i kotwicy."""
    path = (path or "/").split("?", 1)[0].split("#", 1)[0].strip()
    stripped = path.strip("/")
    return f"/{stripped}/" if stripped else "/"


def missing_linked_paths(competition) -> list[str]:
    """Ścieżki z ``linked_paths`` konkursu (odnośniki aplikacji: zgody, warsztaty), pod którymi nie
    stoi opublikowana strona witryny – link z aplikacji prowadziłby na 404 (S16)."""
    paths = published_paths(competition.site)
    wanted = [normalise_page_path(path) for path in competition.linked_paths or [] if isinstance(path, str)]
    return sorted({path for path in wanted if path not in paths})


def app_path_collisions(site) -> list[str]:
    """Opublikowane strony pod adresem aplikacji głównej (S5) – ``/<ścieżka>/: powód``."""
    from apps.pages.validation import path_collides_with_app

    return [
        f"{path}: {reason}"
        for path in sorted(published_paths(site))
        if (reason := path_collides_with_app(path.strip("/")))
    ]


def bundle_paths(bundle) -> dict[int, str]:
    """Ścieżki stron paczki w witrynie djcms (``/``, ``/zadania/``) po identyfikatorze Wagtaila.

    Ten sam układ, co w imporcie: dzieci strony głównej Wagtaila stoją w korzeniu witryny.
    """
    raw: dict[int, str] = {}
    for dto in bundle.pages:
        parent = dto.get("parent_id")
        if parent is None:
            raw[dto["id"]] = ""
        else:
            parent_path = raw.get(parent, "")
            raw[dto["id"]] = f"{parent_path}/{dto['slug']}" if parent_path else dto["slug"]
    return {page_id: (f"/{path}/" if path else "/") for page_id, path in raw.items()}


@dataclass
class SiteCheck:
    """Wynik sprawdzenia jednej witryny – wiersz tabeli ``verify_cutover``."""

    slug: str
    pages: int = 0
    bundle_pages: int = 0
    paths_ok: int = 0
    paths_total: int = 0
    redirects: int = 0
    bundle_redirects: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures
