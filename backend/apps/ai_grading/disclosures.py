"""Informacje pokazywane koordynatorowi **przed** potwierdzeniem umowy powierzenia z dostawcą.

Decyzja organizatora (24.09.2026): umowę powierzenia potwierdza koordynator osobiście, w panelu,
po zapoznaniu się z informacją o dostawcy – nie operator komendą „z góry”. Potwierdzenie jest
oświadczeniem, więc ma być udzielone **wobec konkretnej treści**: ekran pokazuje tekst z tego modułu,
a zapis potwierdzenia (pole ``AiProviderAccount.dpa_info_version`` i wpis w dzienniku zdarzeń)
niesie skrót tej treści. Gdy tekst się zmieni (nowe warunki dostawcy, poprawka w wydaniu), zmienia
się skrót – po skrócie widać, **którą** wersję informacji koordynator miał przed oczami, a formularz
otwarty przed zmianą zostaje odrzucony („informacja się zmieniła – przeczytaj ją ponownie”).

Treść to stan stron dostawców z 24.09.2026 (adresy stoją przy każdej pozycji) i jest listą rzeczy
**do sprawdzenia przez organizatora**, a nie rozstrzygnięciem prawnym systemu.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

#: Data stanu informacji – część skrótu, więc przegląd treści bez zmian merytorycznych i tak ją zmienia.
DISCLOSURE_DATE = "2026-09-24"

#: Co wychodzi z serwisu – jedno brzmienie dla każdego dostawcy.
DATA_LEAVING = (
    "plik pracy uczestnika (PDF, zdjęcie, kod albo notatnik – notatnik jako tekst komórek)",
    "treść zadania, rozwiązanie wzorcowe, skala punktacji, rubryka i uwagi dla recenzentów",
    "NIE wychodzi: imię, nazwisko, adres e-mail, szkoła, kod uczestnika ani nazwa pliku nadana przez "
    "uczestnika – ale sam plik idzie taki, jaki wgrał uczestnik (podpis na pracy albo w skanie też)",
)

#: Ostrzeżenie o warunkach wiekowych – pokazywane wyraźnie przy dostawcach, których warunki je mają.
AGE_WARNING = (
    "Warunki tego dostawcy wymagają, żeby użytkownicy mieli ukończone 18 lat, i zakazują usług "
    "skierowanych do osób niepełnoletnich (albo takich, z których mogą one korzystać). Olimpiada jest "
    "przeznaczona dla uczniów – przed potwierdzeniem trzeba ustalić z inspektorem ochrony danych albo "
    "prawnikiem, czy takie użycie (narzędzie komitetu, bez dostępu uczestników) jest z tymi warunkami "
    "zgodne."
)


@dataclass(frozen=True)
class Disclosure:
    provider: str
    label: str
    recipient: str
    transfer: str
    links: tuple[tuple[str, str], ...]
    retention: str
    training: str
    zero_retention: str
    age_restricted: bool = False
    extra: tuple[str, ...] = field(default_factory=tuple)

    @property
    def age_warning(self) -> str:
        return AGE_WARNING if self.age_restricted else ""

    @property
    def data_leaving(self) -> tuple[str, ...]:
        return DATA_LEAVING

    @property
    def version(self) -> str:
        """Skrót treści pokazanej koordynatorowi – zapisywany razem z potwierdzeniem."""
        payload = {
            "date": DISCLOSURE_DATE,
            "data": DATA_LEAVING,
            "age": self.age_warning,
            **asdict(self),
        }
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        return f"{DISCLOSURE_DATE}/{digest.hexdigest()[:16]}"


DISCLOSURES: dict[str, Disclosure] = {
    "anthropic": Disclosure(
        provider="anthropic",
        label="Anthropic",
        recipient="Anthropic PBC (USA) – dostawca modeli Claude, podmiot przetwarzający",
        transfer=(
            "Przekazanie do USA (poza EOG) – na podstawie mechanizmu wskazanego w DPA Anthropic "
            "(standardowe klauzule umowne albo decyzja stwierdzająca odpowiedni stopień ochrony)."
        ),
        links=(
            ("Commercial Terms (włączają DPA)", "https://www.anthropic.com/legal/commercial-terms"),
            ("Data Processing Addendum", "https://www.anthropic.com/legal/data-processing-addendum"),
            (
                "Okres przechowywania danych organizacji",
                "https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data",
            ),
        ),
        retention=(
            "Wejścia i wyjścia API są kasowane automatycznie do 30 dni; dłużej, gdy treść zostanie "
            "oznaczona jako naruszenie zasad (do 2 lat) albo gdy wymaga tego prawo."
        ),
        training="Commercial Terms: Anthropic nie trenuje modeli na treściach klienta z usług.",
        zero_retention="Brak retencji (zero data retention) – wyłącznie po osobnym uzgodnieniu z Anthropic.",
    ),
    "openai": Disclosure(
        provider="openai",
        label="OpenAI",
        recipient=(
            "OpenAI Ireland Ltd. (klienci z EOG) / OpenAI, L.L.C. – dostawca modeli GPT, podmiot "
            "przetwarzający"
        ),
        transfer=(
            "Przetwarzanie także poza EOG (USA) – na podstawie standardowych klauzul umownych albo decyzji "
            "stwierdzającej odpowiedni stopień ochrony, wskazanych w DPA OpenAI. Przetwarzanie wyłącznie "
            "w EOG (eu.api.openai.com) – po zgodzie OpenAI, za dopłatą 10%."
        ),
        links=(
            ("Services Agreement", "https://cdn.openai.com/osa/openai-services-agreement.pdf"),
            ("Data Processing Addendum", "https://cdn.openai.com/pdf/openai-data-processing-addendum.pdf"),
            ("Twoje dane w API", "https://developers.openai.com/api/docs/guides/your-data"),
            ("Podwykonawcy", "https://openai.com/policies/sub-processor-list/"),
        ),
        retention=(
            "Logi do wykrywania nadużyć – do 30 dni. Serwis wysyła „store: false” (bez tego odpowiedzi "
            "leżałyby u OpenAI co najmniej 30 dni). Pliki i obrazy są skanowane pod kątem CSAM; treść "
            "oznaczona zostaje do przeglądu także przy braku retencji."
        ),
        training="Dane z API nie służą do trenowania modeli, chyba że klient wyrazi na to zgodę.",
        zero_retention="Brak retencji (ZDR) i zmodyfikowane monitorowanie nadużyć – po zgodzie OpenAI.",
    ),
    "google": Disclosure(
        provider="google",
        label="Google",
        recipient="Google Ireland Ltd. / Google LLC – Gemini API (usługa płatna), podmiot przetwarzający",
        transfer=(
            "Przetwarzanie także poza EOG – na podstawie Cloud Data Processing Addendum (zawiera "
            "standardowe klauzule umowne). Polska jest na liście obsługiwanych regionów Gemini API."
        ),
        links=(
            ("Gemini API Additional Terms", "https://ai.google.dev/gemini-api/terms"),
            ("Cloud Data Processing Addendum", "https://business.safety.google/processorterms/"),
            ("Usługi objęte DPA", "https://business.safety.google/services/"),
            ("Zasady użycia i retencja", "https://ai.google.dev/gemini-api/docs/usage-policies"),
            ("Brak retencji", "https://ai.google.dev/gemini-api/docs/zdr"),
        ),
        retention=(
            "Prompty i odpowiedzi przechowywane 55 dni na potrzeby wykrywania nadużyć (z możliwością "
            "przeglądu przez ludzi przy treści oznaczonej)."
        ),
        training=(
            "Warstwa płatna: treści nie służą do ulepszania produktów. Dla użytkowników z EOG warunki "
            "danych warstwy płatnej obowiązują także w limicie bezpłatnym – mimo to klucz powinien "
            "pochodzić z projektu z włączonymi płatnościami."
        ),
        zero_retention=(
            "W Gemini API ograniczony (bez jawnej pamięci podręcznej i Files API – serwis ich nie "
            "używa); pełny brak retencji Google oferuje w Vertex AI, nie w Gemini API."
        ),
        age_restricted=True,
    ),
    "meta": Disclosure(
        provider="meta",
        label="Meta",
        recipient=(
            "Meta Platforms Ireland Ltd. – Meta Model API (modele Muse Spark), warstwa standardowa, "
            "podmiot przetwarzający"
        ),
        transfer=(
            "Przetwarzanie także poza EOG – na podstawie Meta Global Processor Terms włączonych do warunków "
            "Meta Model API. Meta nie publikuje listy krajów, w których usługa działa – dostępność "
            "w Polsce trzeba sprawdzić kluczem („Sprawdź klucz”)."
        ),
        links=(
            ("Meta Model API Terms of Service", "https://dev.meta.ai/legal/terms-of-service"),
            (
                "Meta Global Processor Terms",
                "https://www.facebook.com/legal/terms/Meta-Global-Processor-Terms",
            ),
            ("Data Security Terms", "https://www.facebook.com/legal/terms/data_security_terms"),
            ("Brak retencji", "https://dev.meta.ai/help/policies-and-privacy/zero-data-retention"),
            ("Polityka geograficzna", "https://dev.meta.ai/legal/geographic-use-policy"),
        ),
        retention=(
            "„Tak długo, jak potrzeba” (świadczenie usługi, obowiązki prawne, przegląd nadużyć) – bez "
            "określonego okresu; możliwy przegląd przez ludzi."
        ),
        training=(
            "Warstwa standardowa: Meta nie trenuje modeli na treściach. Warstwa „-contributor” pozwala na "
            "to – serwis jej nie dopuszcza."
        ),
        zero_retention=(
            "Tylko dla kwalifikowanych kont (przez dział sprzedaży); treści oznaczone jako naruszenie są "
            "przechowywane do 2 lat także przy braku retencji."
        ),
        age_restricted=True,
        extra=(
            "Meta wyłączyła Llama API (api.llama.com) 6.07.2026. Dostawca „Meta” w serwisie to jego "
            "następca, Meta Model API (modele Muse Spark, nie Llama). Umowa albo akceptacja warunków "
            "zawarta dla dawnego Llama API może NIE obejmować Meta Model API – sprawdź, na jaką usługę "
            "jest umowa.",
            "Meta Model API jest w wersji zapoznawczej (public preview); według dokumentacji Meta dokłada "
            "do każdego promptu własny kontekst sterujący (prompt systemowy).",
        ),
    ),
}


def disclosure_for(provider: str) -> Disclosure:
    return DISCLOSURES[provider]
