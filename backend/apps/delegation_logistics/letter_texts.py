"""Tekst listu zapraszającego w językach, w których list da się złożyć (VISA-01 § 4).

Dlaczego słownik w kodzie, a nie katalog gettext: to jest **treść dokumentu** – prawnik organizatora
czyta ją w całości, zdanie po zdaniu, a konsulat traktuje jako oświadczenie organizatora. Napisy
interfejsu tłumaczy się maszynowo i poprawia, gdy ktoś zgłosi usterkę; tu każda wersja językowa jest
jednym blokiem, który da się przejrzeć i zatwierdzić naraz, i nie zmienia się przy okazji przeglądu
katalogu `.po`.

Dlaczego tylko siedem języków, choć interfejs zna jedenaście: dokumenty składa krój DejaVu
(``apps.results.certificates.register_fonts``), który ma alfabet łaciński i cyrylicę, ale nie ma znaków
chińskich, dewanagari ani bengalskich, a arabski wymaga kształtowania liter i składu od prawej. List
w tych językach wyszedłby jako rząd prostokątów – lepiej, żeby takiego wyboru nie było.

Wersja angielska jest tekstem odwrotu LOG-01 słowo w słowo (poza zdaniem o weryfikacji w stopce, które
od VISA-01 wskazuje stronę weryfikacji zamiast „skontaktuj się z organizatorem”). Tłumaczenia są
maszynowe – do przeglądu przez organizatora przed pierwszym użyciem danego języka (OPERACJE § 31.8).
Znaczniki w zdaniach są te same we wszystkich językach: ``{organizer}``, ``{country}``, ``{event}``,
``{city}``, ``{event_dates}``, ``{number}``, ``{date}`` – podstawia je ``apps.tenancy.documents``.
"""

from __future__ import annotations

#: Język domyślny i zawsze dostępny – język olimpiady międzynarodowej i konsulatów.
DEFAULT_LANGUAGE = "en"

#: Kolumny tabeli osób, w kolejności: imię i nazwisko z paszportu, obywatelstwo, data urodzenia,
#: numer paszportu, ważność, rola.
LETTER_TEXTS: dict[str, dict] = {
    "en": {
        "title": "Letter of invitation",
        "statement": (
            "On behalf of {organizer}, we have the pleasure of inviting the person(s) listed below, members "
            "of the national delegation of {country}, to take part in {event}, which will take place in "
            "{city} on {event_dates}. This letter is issued at the request of the national delegation for "
            "the purpose of a visa application."
        ),
        "signature_line": "for the organizer",
        "footer_note": (
            "Letter no. {number} of {date}. The authenticity of this letter can be confirmed on the "
            "verification page given below."
        ),
        "columns": (
            "Full name (as in passport)",
            "Nationality",
            "Date of birth",
            "Passport no.",
            "Valid until",
            "Role",
        ),
        "number_label": "No.",
        "verify_title": "Verification",
        "verify_text": (
            "Scan the QR code or open {url} and enter the code {code} to confirm that this letter is "
            "genuine and has not been revoked."
        ),
    },
    "pl": {
        "title": "List zapraszający",
        "statement": (
            "Organizator – {organizer} – ma przyjemność zaprosić wymienione niżej osoby, członków delegacji "
            "narodowej (kraj: {country}), do udziału w wydarzeniu: {event}. Miejsce: {city}. Termin: "
            "{event_dates}. List wystawiono na wniosek delegacji narodowej w celu złożenia wniosku wizowego."
        ),
        "signature_line": "w imieniu organizatora",
        "footer_note": (
            "List nr {number}, data wystawienia: {date}. Autentyczność listu można potwierdzić "
            "na stronie weryfikacji wskazanej niżej."
        ),
        "columns": (
            "Imię i nazwisko (jak w paszporcie)",
            "Obywatelstwo",
            "Data urodzenia",
            "Nr paszportu",
            "Ważny do",
            "Rola",
        ),
        "number_label": "Nr",
        "verify_title": "Weryfikacja",
        "verify_text": (
            "Zeskanuj kod QR albo otwórz {url} i wpisz kod {code}, aby potwierdzić, że list jest "
            "autentyczny i nie został unieważniony."
        ),
    },
    "es": {
        "title": "Carta de invitación",
        "statement": (
            "En nombre de {organizer}, tenemos el placer de invitar a las personas que se indican a "
            "continuación, miembros de la delegación nacional de {country}, a participar en {event}, que "
            "tendrá lugar en {city} en las fechas {event_dates}. Esta carta se expide a petición de la "
            "delegación nacional a efectos de la solicitud de visado."
        ),
        "signature_line": "por el organizador",
        "footer_note": (
            "Carta n.º {number} de {date}. La autenticidad de esta carta puede confirmarse en la página de "
            "verificación indicada a continuación."
        ),
        "columns": (
            "Nombre completo (como en el pasaporte)",
            "Nacionalidad",
            "Fecha de nacimiento",
            "N.º de pasaporte",
            "Válido hasta",
            "Función",
        ),
        "number_label": "N.º",
        "verify_title": "Verificación",
        "verify_text": (
            "Escanee el código QR o abra {url} e introduzca el código {code} para confirmar que esta carta "
            "es auténtica y no ha sido revocada."
        ),
    },
    "fr": {
        "title": "Lettre d'invitation",
        "statement": (
            "Au nom de {organizer}, nous avons le plaisir d'inviter les personnes mentionnées ci-dessous, "
            "membres de la délégation nationale de {country}, à participer à {event}, qui se tiendra à "
            "{city} aux dates suivantes : {event_dates}. Cette lettre est délivrée à la demande de la "
            "délégation nationale en vue d'une demande de visa."
        ),
        "signature_line": "pour l'organisateur",
        "footer_note": (
            "Lettre n° {number} du {date}. L'authenticité de cette lettre peut être confirmée sur la page "
            "de vérification indiquée ci-dessous."
        ),
        "columns": (
            "Nom complet (comme sur le passeport)",
            "Nationalité",
            "Date de naissance",
            "N° de passeport",
            "Valable jusqu'au",
            "Rôle",
        ),
        "number_label": "N°",
        "verify_title": "Vérification",
        "verify_text": (
            "Scannez le code QR ou ouvrez {url} et saisissez le code {code} pour confirmer que cette lettre "
            "est authentique et n'a pas été révoquée."
        ),
    },
    "pt": {
        "title": "Carta-convite",
        "statement": (
            "Em nome de {organizer}, temos o prazer de convidar as pessoas abaixo indicadas, membros da "
            "delegação nacional de {country}, a participar em {event}, que terá lugar em {city} nas datas "
            "{event_dates}. Esta carta é emitida a pedido da delegação nacional para efeitos de pedido de "
            "visto."
        ),
        "signature_line": "pelo organizador",
        "footer_note": (
            "Carta n.º {number} de {date}. A autenticidade desta carta pode ser confirmada na página de "
            "verificação indicada abaixo."
        ),
        "columns": (
            "Nome completo (como no passaporte)",
            "Nacionalidade",
            "Data de nascimento",
            "N.º do passaporte",
            "Válido até",
            "Função",
        ),
        "number_label": "N.º",
        "verify_title": "Verificação",
        "verify_text": (
            "Leia o código QR ou abra {url} e introduza o código {code} para confirmar que esta carta é "
            "autêntica e não foi revogada."
        ),
    },
    "ru": {
        "title": "Письмо-приглашение",
        "statement": (
            "Организатор – {organizer} – рад пригласить перечисленных ниже лиц, членов национальной "
            "делегации (страна: {country}), принять участие в мероприятии: {event}. "
            "Место проведения: {city}. Сроки: {event_dates}. Настоящее письмо выдано по запросу "
            "национальной делегации для подачи заявления на визу."
        ),
        "signature_line": "от имени организатора",
        "footer_note": (
            "Письмо № {number}, дата выдачи: {date}. Подлинность письма можно подтвердить "
            "на странице проверки, указанной ниже."
        ),
        "columns": (
            "Полное имя (как в паспорте)",
            "Гражданство",
            "Дата рождения",
            "№ паспорта",
            "Действителен до",
            "Роль",
        ),
        "number_label": "№",
        "verify_title": "Проверка",
        "verify_text": (
            "Отсканируйте QR-код или откройте {url} и введите код {code}, чтобы убедиться, что письмо "
            "подлинное и не было отозвано."
        ),
    },
    "id": {
        "title": "Surat undangan",
        "statement": (
            "Atas nama {organizer}, dengan senang hati kami mengundang orang-orang yang tercantum di bawah "
            "ini, anggota delegasi nasional {country}, untuk mengikuti {event} yang akan diselenggarakan di "
            "{city} pada tanggal {event_dates}. Surat ini diterbitkan atas permintaan delegasi nasional "
            "untuk keperluan pengajuan visa."
        ),
        "signature_line": "atas nama penyelenggara",
        "footer_note": (
            "Surat no. {number} tanggal {date}. Keaslian surat ini dapat dikonfirmasi di halaman verifikasi "
            "yang tercantum di bawah."
        ),
        "columns": (
            "Nama lengkap (sesuai paspor)",
            "Kewarganegaraan",
            "Tanggal lahir",
            "No. paspor",
            "Berlaku hingga",
            "Peran",
        ),
        "number_label": "No.",
        "verify_title": "Verifikasi",
        "verify_text": (
            "Pindai kode QR atau buka {url} dan masukkan kode {code} untuk memastikan bahwa surat ini asli "
            "dan belum dicabut."
        ),
    },
}


def texts_for(language: str) -> dict:
    """Teksty listu w tym języku; nieznany (np. wiersz sprzed VISA-01) – angielski."""
    return LETTER_TEXTS.get(language) or LETTER_TEXTS[DEFAULT_LANGUAGE]


def letter_languages(competition) -> list[tuple[str, str]]:
    """Języki do wyboru przy wniosku: języki interfejsu konkursu, w których list da się złożyć.

    Angielski zawsze, i zawsze pierwszy – nawet gdy konkurs ma interfejs wyłącznie po polsku, list
    do konsulatu po angielsku musi dać się wystawić. Nazwy z ``settings.LANGUAGES`` (tłumaczone).
    Przy własnym tekście listu w „Szablonach dokumentów” – wyłącznie angielski (L7, patrz
    ``letters.has_db_template``).
    """
    from django.conf import settings

    from .letters import has_db_template

    names = dict(settings.LANGUAGES)
    chosen = [DEFAULT_LANGUAGE]
    if has_db_template(competition):
        return [(DEFAULT_LANGUAGE, str(names.get(DEFAULT_LANGUAGE, DEFAULT_LANGUAGE)))]
    for code in getattr(competition, "ui_languages", None) or []:
        if code in LETTER_TEXTS and code not in chosen:
            chosen.append(code)
    return [(code, str(names.get(code, code))) for code in chosen]
