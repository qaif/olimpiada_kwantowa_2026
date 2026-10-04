"""Podgląd motywu IQO Quantum bez Django: statyczne makiety stron + zrzuty Playwrightem.

    uv run --no-project --with playwright python -m playwright install chromium
    uv run --no-project --with playwright --with pillow python themes/iqo-quantum/_preview/render.py [katalog_zrzutów]

Co robi:
1. ``tokens.css`` (schemat ``dark``) i ``tokens-light.css`` (schemat ``light``) – generatorem
   platformy (``backend/apps/themes/tokens.py``: ``parse_tokens`` + ``build_tokens_css``), jeśli jest
   w repozytorium albo wskazany zmienną ``IQO_TOKENS_PY``; inaczej prostą emulacją.
2. ``index.html`` (strona główna, en, ciemny) i warianty ``preview-<nazwa>.html`` – makiety z **tymi
   samymi klasami i strukturą**, które renderują ``base.html`` aplikacji i sloty
   ``templates/theme/*.html`` motywu (nagłówek z ``<details class="iqo-menu">``, menu z ``nav.html``,
   plansza, karta aktualności, nagłówek strony CMS, stopka). Arkusze: ``app.css`` aplikacji
   (``IQO_APP_CSS`` albo ``backend/static/css/app.css``) → tokeny → ``../theme.css``.
   Na ``<html>`` te same atrybuty co z ``{% theme_html_attrs %}`` (``data-color-scheme``,
   ``data-layout-*``), więc warianty układów rysuje ten sam arkusz.
3. Zrzuty: ``screenshot.png`` paczki (1200×900, en, ciemny) i warianty do przeglądu (jasny,
   ar/RTL, ru, hi, wysoki kontrast, telefon 360 px z otwartym menu, warianty układów, strona treści
   z tabelą wyników, lista aktualności, konto zalogowanego, wydruk) w katalogu zrzutów
   (domyślnie ``_preview/shots/`` – poza repozytorium). Dla każdego zrzutu wypisuje poziome
   przepełnienie (musi być 0).

Katalog ``_preview/`` nie wchodzi do ZIP-a (build_zip.py).
"""
from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import os
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
THEME = HERE.parent
REPO = THEME.parent.parent
APP_CSS = pathlib.Path(os.environ.get("IQO_APP_CSS") or REPO / "backend" / "static" / "css" / "app.css")
TOKENS_PY = pathlib.Path(os.environ.get("IQO_TOKENS_PY") or REPO / "backend" / "apps" / "themes" / "tokens.py")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D102 - bez dziennika żądań na ekranie
        pass


# --- 1. tokeny ----------------------------------------------------------------------------------

def tokens_css(scheme: str) -> str:
    raw = (THEME / "tokens.json").read_bytes()
    if TOKENS_PY.is_file():
        spec = importlib.util.spec_from_file_location("theme_tokens", TOKENS_PY)
        module = importlib.util.module_from_spec(spec)
        sys.modules["theme_tokens"] = module
        spec.loader.exec_module(module)
        parsed = module.parse_tokens(raw)
        generated = module.build_tokens_css(parsed, scheme)
        for warning in parsed.errors + parsed.warnings + generated.warnings:
            print("tokens:", warning)
        return generated.css
    data = json.loads(raw)
    values = {**(data["dark"] if scheme == "dark" else data["colors"]), **data["tokens"]}
    body = "\n".join(f"  --t-{k}: {v};" for k, v in values.items())
    return f"/* Emulacja tokens.css (brak apps/themes/tokens.py). */\n:root {{\n  color-scheme: {scheme};\n{body}\n}}\n"


# --- 2. makiety -----------------------------------------------------------------------------------

STRINGS = {
    "en": dict(
        dir="ltr", site="International Quantum Olympiad", edition="2027 edition", menu="Menu",
        account="Account", site_nav="Site", skip="Skip to content",
        nav=["Home", "About", "Problems", "Schedule", "Results", "News", "FAQ"], docs="Documents",
        docs_items=["Rules", "Privacy policy", "Child protection standards"],
        lang_label="English", lang_hidden="Interface language", contrast="High contrast: on",
        support="Report a problem", login="Sign in", register="Register", logout="Log out",
        roles=["My dashboard", "My team", "Coordinator"], extra=["Forum", "Messages", "Report a problem"],
        kicker="Edition 2027", title="International Quantum Olympiad",
        text="An online and on-site competition in quantum physics and quantum computing for "
             "secondary-school students from every country. Three stages, one final, real "
             "problems from working laboratories.",
        status_pre="Current stage:", stage="Stage I – qualifying round",
        status_post="· solution deadline 15 November 2026, 18:00",
        timeline_title="Competition timeline: 2027 edition", timeline_hint="All times: Central European Time.",
        stages=[
            ("Stage I – qualifying round", "done", "finished", [("Opens", "1 Oct 2026, 09:00"), ("Deadline", "15 Nov 2026, 18:00")], "Results table"),
            ("Stage II – online round", "open", "stage open", [("Opens", "1 Dec 2026, 09:00"), ("Deadline", "20 Jan 2027, 18:00")], None),
            ("Final", "next", "upcoming", [("Venue", "Warsaw, Poland"), ("Dates", "4–7 June 2027")], None),
        ],
        not_announced="Results not announced.",
        steps_title="How to take part",
        steps=[("Create an account", "Register with your school e-mail and confirm your age."),
               ("Solve Stage I", "Upload your solutions as PDF before the deadline."),
               ("Get to the final", "The best participants are invited to Warsaw.")],
        news_title="News", news_all="All news", read_more="Read more",
        news=[("2 Oct 2026", "Registration for the 2027 edition is open", "Students from all countries can now create accounts and download the Stage I problems."),
              ("24 Sep 2026", "Free online workshops start in October", "Six sessions on qubits, measurement and entanglement, recorded for later viewing."),
              ("12 Sep 2026", "Meet the scientific committee", "Physicists from eleven universities will set and grade the problems.")],
        foot_tagline="|IQO⟩ – quantum physics for secondary schools", foot_site="Site", foot_contact="Contact",
        partners="Partners and sponsors",
        privacy="Privacy policy (GDPR)", cookies="Cookie policy", posters="Posters to download",
        rodo="Participants' personal data are processed according to the data-minimisation principle (GDPR): public results tables only show the participant code; full data are visible to the coordinator only.",
        organizer="Organiser:", org="Quantum AI Foundation", version="version 0.41.0",
        cookie_text="This website only uses necessary cookies (login session, form protection). We do not use advertising or analytics cookies.",
        cookie_ok="Got it",
        page_title="Rules of the International Quantum Olympiad", toc_title="On this page",
        chapters=["Organiser and aims", "Who can take part", "Stages and scoring", "Results"],
        results_title="Stage I – 2027", results_cols=["Place", "Participant", "P1", "P2", "P3", "Total", "Qualified"],
        yes="qualified", no="no", newsroom="Newsroom", news_index_title="News",
    ),
    "ar": dict(
        dir="rtl", site="الأولمبياد الدولي للكم", edition="دورة 2027", menu="القائمة",
        account="الحساب", site_nav="الموقع", skip="انتقل إلى المحتوى",
        nav=["الرئيسية", "حول", "المسائل", "الجدول الزمني", "النتائج", "الأخبار", "الأسئلة الشائعة"], docs="الوثائق",
        docs_items=["اللوائح", "سياسة الخصوصية", "معايير حماية الأطفال"],
        lang_label="العربية", lang_hidden="لغة الواجهة", contrast="تباين عالٍ: تشغيل",
        support="الإبلاغ عن مشكلة", login="تسجيل الدخول", register="التسجيل", logout="تسجيل الخروج",
        roles=["لوحتي", "فريقي", "المنسق"], extra=["المنتدى", "الرسائل", "الإبلاغ عن مشكلة"],
        kicker="دورة 2027", title="الأولمبياد الدولي لفيزياء الكم",
        text="مسابقة عبر الإنترنت وحضورية في فيزياء الكم والحوسبة الكمومية لطلاب المرحلة الثانوية من جميع البلدان.",
        status_pre="المرحلة الحالية:", stage="المرحلة الأولى – التصفيات",
        status_post="· آخر موعد لتسليم الحلول 15 نوفمبر 2026، 18:00",
        timeline_title="مسار المسابقة: دورة 2027", timeline_hint="جميع الأوقات بتوقيت وسط أوروبا.",
        stages=[
            ("المرحلة الأولى – التصفيات", "done", "انتهت", [("الافتتاح", "1 أكتوبر 2026"), ("الموعد النهائي", "15 نوفمبر 2026")], "جدول النتائج"),
            ("المرحلة الثانية – عبر الإنترنت", "open", "المرحلة مفتوحة", [("الافتتاح", "1 ديسمبر 2026"), ("الموعد النهائي", "20 يناير 2027")], None),
            ("النهائي", "next", "قادمة", [("المكان", "وارسو، بولندا"), ("التاريخ", "4–7 يونيو 2027")], None),
        ],
        not_announced="لم تُعلن النتائج بعد.",
        steps_title="كيف تشارك",
        steps=[("أنشئ حسابًا", "سجّل ببريدك المدرسي وأكّد عمرك."),
               ("حلّ المرحلة الأولى", "ارفع حلولك بصيغة PDF قبل الموعد النهائي."),
               ("الوصول إلى النهائي", "يُدعى أفضل المشاركين إلى وارسو.")],
        news_title="الأخبار", news_all="كل الأخبار", read_more="اقرأ المزيد",
        news=[("2 أكتوبر 2026", "التسجيل لدورة 2027 مفتوح", "يمكن للطلاب من جميع البلدان إنشاء حسابات الآن."),
              ("24 سبتمبر 2026", "ورش عمل مجانية عبر الإنترنت", "ست جلسات حول الكيوبتات والقياس والتشابك."),
              ("12 سبتمبر 2026", "تعرّف على اللجنة العلمية", "فيزيائيون من إحدى عشرة جامعة.")],
        foot_tagline="|IQO⟩ – فيزياء الكم للمدارس الثانوية", foot_site="الموقع", foot_contact="اتصل بنا",
        partners="الشركاء والرعاة",
        privacy="سياسة الخصوصية", cookies="سياسة ملفات تعريف الارتباط", posters="ملصقات للتنزيل",
        rodo="تُعالج البيانات الشخصية للمشاركين وفق مبدأ تقليل البيانات: جداول النتائج العامة تعرض رمز المشارك فقط.",
        organizer="الجهة المنظمة:", org="Quantum AI Foundation", version="الإصدار 0.41.0",
        cookie_text="يستخدم هذا الموقع ملفات تعريف الارتباط الضرورية فقط.", cookie_ok="فهمت",
        page_title="لوائح الأولمبياد الدولي للكم", toc_title="في هذه الصفحة",
        chapters=["الجهة المنظمة والأهداف", "من يمكنه المشاركة", "المراحل والتقييم", "النتائج"],
        results_title="المرحلة الأولى – 2027", results_cols=["المركز", "المشارك", "م1", "م2", "م3", "المجموع", "متأهل"],
        yes="متأهل", no="لا", newsroom="غرفة الأخبار", news_index_title="الأخبار",
    ),
    "ru": dict(
        dir="ltr", site="Международная квантовая олимпиада", edition="выпуск 2027", menu="Меню",
        account="Учётная запись", site_nav="Сайт", skip="Перейти к содержанию",
        nav=["Главная", "Об олимпиаде", "Задачи", "Расписание", "Результаты", "Новости", "Частые вопросы",
             "Для учителей", "Мастер-классы", "Партнёры и спонсоры"], docs="Документы",
        docs_items=["Положение", "Политика конфиденциальности", "Стандарты защиты несовершеннолетних"],
        lang_label="Русский", lang_hidden="Язык интерфейса", contrast="Высокий контраст: включить",
        support="Сообщить о проблеме", login="Войти", register="Зарегистрироваться", logout="Выйти",
        roles=["Мой кабинет", "Моя команда", "Координатор"], extra=["Форум", "Сообщения", "Сообщить о проблеме"],
        kicker="Выпуск 2027", title="Международная квантовая олимпиада",
        text="Дистанционное и очное соревнование по квантовой физике и квантовым вычислениям для старшеклассников из всех стран.",
        status_pre="Текущий этап:", stage="Этап I – отборочный тур",
        status_post="· срок сдачи решений 15 ноября 2026, 18:00",
        timeline_title="Ход соревнований: выпуск 2027", timeline_hint="Всё время – центральноевропейское.",
        stages=[
            ("Этап I – отборочный тур", "done", "завершён", [("Открытие", "1 окт. 2026, 09:00"), ("Срок сдачи", "15 нояб. 2026, 18:00")], "Таблица результатов"),
            ("Этап II – дистанционный тур", "open", "этап открыт", [("Открытие", "1 дек. 2026, 09:00"), ("Срок сдачи", "20 янв. 2027, 18:00")], None),
            ("Финал", "next", "предстоящий", [("Место", "Варшава, Польша"), ("Даты", "4–7 июня 2027")], None),
        ],
        not_announced="Результаты не объявлены.",
        steps_title="Как принять участие",
        steps=[("Создайте учётную запись", "Зарегистрируйтесь со школьным адресом электронной почты."),
               ("Решите задачи этапа I", "Загрузите решения в формате PDF до срока."),
               ("Пройдите в финал", "Лучших участников пригласят в Варшаву.")],
        news_title="Новости", news_all="Все новости", read_more="Читать далее",
        news=[("2 окт. 2026", "Открыта регистрация на выпуск 2027", "Школьники из всех стран уже могут создавать учётные записи."),
              ("24 сент. 2026", "Бесплатные онлайн-семинары", "Шесть занятий о кубитах, измерении и запутанности."),
              ("12 сент. 2026", "Научный комитет олимпиады", "Физики из одиннадцати университетов.")],
        foot_tagline="|IQO⟩ – квантовая физика для старших классов", foot_site="Сайт", foot_contact="Контакты",
        partners="Партнёры и спонсоры",
        privacy="Политика конфиденциальности", cookies="Политика использования файлов cookie", posters="Плакаты для скачивания",
        rodo="Персональные данные участников обрабатываются по принципу минимизации: в публичных таблицах результатов виден только код участника.",
        organizer="Организатор:", org="Quantum AI Foundation", version="версия 0.41.0",
        cookie_text="Этот сайт использует только необходимые файлы cookie.", cookie_ok="Понятно",
        page_title="Положение о Международной квантовой олимпиаде", toc_title="На этой странице",
        chapters=["Организатор и цели", "Кто может участвовать", "Этапы и оценивание", "Результаты"],
        results_title="Этап I – 2027", results_cols=["Место", "Участник", "З1", "З2", "З3", "Сумма", "Прошёл"],
        yes="прошёл", no="нет", newsroom="Пресс-центр", news_index_title="Новости",
    ),
    "hi": dict(
        dir="ltr", site="अंतर्राष्ट्रीय क्वांटम ओलंपियाड", edition="संस्करण 2027", menu="मेनू",
        account="खाता", site_nav="साइट", skip="सामग्री पर जाएँ",
        nav=["होम", "परिचय", "प्रश्न", "समय-सारणी", "परिणाम", "समाचार", "सामान्य प्रश्न"], docs="दस्तावेज़",
        docs_items=["नियम", "गोपनीयता नीति", "बाल संरक्षण मानक"],
        lang_label="हिन्दी", lang_hidden="इंटरफ़ेस भाषा", contrast="उच्च कंट्रास्ट: चालू",
        support="समस्या की सूचना दें", login="लॉग इन करें", register="पंजीकरण करें", logout="लॉग आउट",
        roles=["मेरा पैनल", "मेरी टीम", "समन्वयक"], extra=["फ़ोरम", "संदेश", "समस्या की सूचना दें"],
        kicker="संस्करण 2027", title="अंतर्राष्ट्रीय क्वांटम ओलंपियाड",
        text="सभी देशों के माध्यमिक विद्यालय के छात्रों के लिए क्वांटम भौतिकी और क्वांटम कंप्यूटिंग की प्रतियोगिता।",
        status_pre="वर्तमान चरण:", stage="चरण I – क्वालीफाइंग राउंड",
        status_post="· समाधान जमा करने की अंतिम तिथि 15 नवंबर 2026",
        timeline_title="प्रतियोगिता की समय-रेखा: संस्करण 2027", timeline_hint="सभी समय: मध्य यूरोपीय समय।",
        stages=[
            ("चरण I – क्वालीफाइंग राउंड", "done", "समाप्त", [("शुरुआत", "1 अक्टूबर 2026"), ("अंतिम तिथि", "15 नवंबर 2026")], "परिणाम तालिका"),
            ("चरण II – ऑनलाइन राउंड", "open", "चरण खुला है", [("शुरुआत", "1 दिसंबर 2026"), ("अंतिम तिथि", "20 जनवरी 2027")], None),
            ("फ़ाइनल", "next", "आगामी", [("स्थान", "वारसॉ, पोलैंड"), ("तिथियाँ", "4–7 जून 2027")], None),
        ],
        not_announced="परिणाम घोषित नहीं हुए।",
        steps_title="कैसे भाग लें",
        steps=[("खाता बनाएँ", "अपने विद्यालय ई-मेल से पंजीकरण करें।"),
               ("चरण I हल करें", "अंतिम तिथि से पहले PDF में समाधान अपलोड करें।"),
               ("फ़ाइनल तक पहुँचें", "सर्वश्रेष्ठ प्रतिभागियों को वारसॉ आमंत्रित किया जाता है।")],
        news_title="समाचार", news_all="सभी समाचार", read_more="आगे पढ़ें",
        news=[("2 अक्टूबर 2026", "संस्करण 2027 के लिए पंजीकरण खुला", "सभी देशों के छात्र अब खाते बना सकते हैं।"),
              ("24 सितंबर 2026", "निःशुल्क ऑनलाइन कार्यशालाएँ", "क्यूबिट, मापन और उलझाव पर छह सत्र।"),
              ("12 सितंबर 2026", "वैज्ञानिक समिति से मिलें", "ग्यारह विश्वविद्यालयों के भौतिक विज्ञानी।")],
        foot_tagline="|IQO⟩ – माध्यमिक विद्यालयों के लिए क्वांटम भौतिकी", foot_site="साइट", foot_contact="संपर्क",
        partners="साझेदार और प्रायोजक",
        privacy="गोपनीयता नीति", cookies="कुकी नीति", posters="डाउनलोड के लिए पोस्टर",
        rodo="प्रतिभागियों के व्यक्तिगत डेटा को न्यूनतमीकरण सिद्धांत के अनुसार संसाधित किया जाता है।",
        organizer="आयोजक:", org="Quantum AI Foundation", version="संस्करण 0.41.0",
        cookie_text="यह वेबसाइट केवल आवश्यक कुकीज़ का उपयोग करती है।", cookie_ok="समझ गया",
        page_title="अंतर्राष्ट्रीय क्वांटम ओलंपियाड के नियम", toc_title="इस पृष्ठ पर",
        chapters=["आयोजक और उद्देश्य", "कौन भाग ले सकता है", "चरण और अंक", "परिणाम"],
        results_title="चरण I – 2027", results_cols=["स्थान", "प्रतिभागी", "प्र1", "प्र2", "प्र3", "कुल", "चयनित"],
        yes="चयनित", no="नहीं", newsroom="न्यूज़रूम", news_index_title="समाचार",
    ),
}

CURRENT = ' aria-current="page"'
QUALIFIED = ' class="is-qualified"'

ICON_GLOBE = (
    '<svg class="pref-icon" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">'
    '<circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" stroke-width="1.5"/>'
    '<path d="M1.75 9h14.5M9 1.75c2 2 3 4.4 3 7.25s-1 5.25-3 7.25M9 1.75c-2 2-3 4.4-3 7.25s1 5.25 3 7.25" '
    'fill="none" stroke="currentColor" stroke-width="1.2"/></svg>'
)
ICON_CONTRAST = (
    '<svg class="pref-icon" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">'
    '<circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" stroke-width="1.5"/>'
    '<path d="M9 1.75a7.25 7.25 0 0 1 0 14.5z" fill="currentColor"/></svg>'
)


def logo_html(scheme: str, logo: str, surface: str = "page") -> str:
    """Odpowiednik ``templates/theme/partials/logo.html`` (pliki z ``../assets/logo``)."""
    files = {"lockup": ("lockup.svg", "lockup-white.svg", 540, 258), "mark": ("mark.svg", "mark-white.svg", 280, 280),
             "full": ("logo.svg", "logo-white.svg", 610, 298)}
    light, dark, w, h = files[logo]
    light, dark = f"../assets/logo/{light}", f"../assets/logo/{dark}"
    if surface == "cover" or scheme == "dark":
        return f'<img class="iqo-logo iqo-logo--page iqo-logo--cover" src="{dark}" alt="" width="{w}" height="{h}">'
    return (f'<img class="iqo-logo iqo-logo--page" src="{light}" alt="" width="{w}" height="{h}">'
            f'<img class="iqo-logo iqo-logo--cover iqo-logo--cover-only" src="{dark}" alt="" width="{w}" height="{h}">')


def header_html(s: dict, o: dict) -> str:
    current = o.get("current", 0)
    items = []
    for i, title in enumerate(s["nav"]):
        cls = "nav__link" + (" nav__link--home-text" if i == 0 else "")
        aria = ' aria-current="page"' if i == current else ""
        active = " is-active" if i == current else ""
        items.append(f'<li class="iqo-nav__item{active}"><a class="{cls}" href="#"{aria}>{title}</a></li>')
    docs = "".join(f'<li><a class="nav-menu__link" href="#">{t}</a></li>' for t in s["docs_items"])
    items.append(
        '<li class="iqo-nav__item iqo-nav__item--group"><details class="nav-menu">'
        f'<summary class="nav__link nav-menu__summary">{s["docs"]}</summary>'
        f'<ul class="nav-menu__list">{docs}</ul></details></li>'
    )
    if o.get("external"):
        items.append('<li class="iqo-nav__item"><a class="nav__link iqo-ext" href="#" target="_blank" '
                     'rel="noopener noreferrer">iqo-official.org</a></li>')
    prefs = (
        '<form class="inline account-bar__prefs"><details class="lang-menu">'
        f'<summary class="btn btn--small btn--ghost account-bar__btn lang-menu__summary">{ICON_GLOBE}'
        f'<span>{s["lang_label"]}</span><span class="visually-hidden">{s["lang_hidden"]}</span></summary>'
        f'<ul class="lang-menu__list"><li><span class="lang-menu__item lang-menu__item--current">{s["lang_label"]}</span></li>'
        '<li><button type="button" class="lang-menu__item">Polski</button></li></ul></details></form>'
        '<form class="inline account-bar__prefs"><button type="button" class="btn btn--small btn--ghost account-bar__btn '
        f'account-bar__btn--icon" aria-pressed="false" aria-label="{s["contrast"]}" title="{s["contrast"]}">{ICON_CONTRAST}</button></form>'
    )
    if o.get("logged_in"):
        roles = "".join(f'<li><a class="account-bar__link" href="#"{CURRENT if i == 0 else ""}>{t}</a></li>'
                        for i, t in enumerate(s["roles"]))
        extra = "".join(f'<a class="account-bar__link" href="#">{t}</a>' for t in s["extra"])
        open_attr = " open" if o.get("account_open") else ""
        account = (
            f'<details class="iqo-acct"{open_attr}><summary class="iqo-acct__toggle" title="ada@example.org">'
            '<span class="iqo-icon iqo-icon--account" aria-hidden="true"></span>'
            f'<span class="iqo-acct__label">{s["account"]}</span>'
            '<span class="iqo-icon iqo-icon--chevron" aria-hidden="true"></span></summary>'
            '<div class="iqo-acct__panel"><p class="account-bar__who who iqo-acct__who">ada.lovelace@school.example.org</p>'
            f'<ul class="iqo-acct__list">{roles}</ul><div class="iqo-acct__extra">{extra}</div>'
            '<form class="inline account-bar__logout iqo-acct__logout">'
            f'<button type="button" class="btn btn--small btn--secondary account-bar__btn">{s["logout"]}</button></form></div></details>'
        )
    else:
        account = (
            f'<span class="iqo-account__support"><a class="account-bar__link" href="#">{s["support"]}</a></span>'
            f'<a class="btn btn--small btn--secondary account-bar__btn iqo-account__login" href="#">{s["login"]}</a>'
            f'<a class="btn btn--small btn--accent account-bar__btn" href="#">{s["register"]}</a>'
        )
    logo = o.get("logo", "lockup")
    menu_open = " open" if o.get("menu_open") else ""
    return f"""
<header class="iqo-header" data-iqo-header>
  <div class="iqo-header__bar">
    <a class="iqo-brand iqo-brand--{logo}" href="#">{logo_html(o["scheme"], logo)}
      <span class="iqo-brand__name">{s["site"]}</span><span class="iqo-brand__edition">{s["edition"]}</span></a>
    <details class="iqo-menu"{menu_open}><summary class="iqo-menu__toggle"><span class="iqo-icon iqo-icon--menu" aria-hidden="true"></span><span class="iqo-menu__label">{s["menu"]}</span></summary></details>
    <div class="iqo-header__nav"><nav class="nav nav--cms iqo-nav" aria-label="{s["site_nav"]}"><ul class="iqo-nav__list">{"".join(items)}</ul></nav></div>
    <nav class="account-bar iqo-account" aria-label="{s["account"]}">{prefs}{account}</nav>
  </div>
</header>"""


def hero_html(s: dict) -> str:
    return f"""
<section class="iqo-hero hero--slider" data-hero-slider aria-label="Highlights">
  <div class="iqo-hero__art" aria-hidden="true"><span class="iqo-hero__fringes"></span><span class="iqo-hero__orbit"></span><span class="iqo-hero__sphere"></span></div>
  <div class="hero-slider__viewport iqo-hero__viewport" tabindex="-1">
    <div class="hero-slide iqo-slide iqo-slide--intro" role="group">
      <div class="iqo-hero__inner">
        <p class="iqo-hero__kicker">{s["kicker"]}</p>
        <h1 class="iqo-hero__title">{s["title"]}</h1>
        <div class="iqo-hero__text"><p>{s["text"]}</p></div>
        <div class="iqo-hero__actions">
          <a class="btn btn--accent iqo-btn--arrow" href="#">{s["register"]}</a>
          <a class="btn btn--secondary" href="#">{s["login"]}</a>
        </div>
        <p class="iqo-hero__status">{s["status_pre"]} <strong>{s["stage"]}</strong> {s["status_post"]}</p>
      </div>
    </div>
  </div>
</section>"""


def home_html(s: dict) -> str:
    stages = ""
    for kind, state, badge, dates, results in s["stages"]:
        badge_cls = {"done": "badge--ok", "open": "badge--accent", "next": "badge--neutral"}[state]
        rows = "".join(f"<div><dt>{dt}</dt><dd>{dd}</dd></div>" for dt, dd in dates)
        foot = (f'<a class="btn btn--secondary btn--small" href="#">{results}</a>' if results
                else f'<p class="hint mt-0">{s["not_announced"]}</p>')
        stages += (f'<li class="timeline__item timeline__item--{state}"><p class="timeline__kind">{kind}</p>'
                   f'<span class="badge {badge_cls}">{badge}</span><dl class="timeline__dates">{rows}</dl>{foot}</li>')
    steps = "".join(f'<li class="steps__item"><p class="steps__title">{t}</p><p class="steps__text">{x}</p></li>'
                    for t, x in s["steps"])
    news = "".join(f'<li><article class="card news-card"><span class="news-card__date">{d}</span>'
                   f'<h3><a href="#">{t}</a></h3><p>{x}</p></article></li>' for d, t, x in s["news"])
    return hero_html(s) + f"""
<section class="section">
  <div class="section__head"><h2 class="mt-0">{s["timeline_title"]}</h2><p class="hint">{s["timeline_hint"]}</p></div>
  <ol class="timeline">{stages}</ol>
</section>
<section class="section">
  <div class="section__head"><h2 class="mt-0">{s["steps_title"]}</h2></div>
  <ol class="steps">{steps}</ol>
  <p class="steps__cta"><a class="btn btn--accent" href="#">{s["register"]}</a></p>
</section>
<section class="section" id="aktualnosci">
  <div class="section__head"><h2 class="mt-0">{s["news_title"]}</h2><a href="#">{s["news_all"]}</a></div>
  <ul class="cards">{news}</ul>
</section>"""


def content_html(s: dict) -> str:
    """Strona treści CMS (slot page_header + spis sekcji) z tabelą wyników jak ``results_page``."""
    toc = "".join(f'<li><a href="#">{c}</a></li>' for c in s["chapters"])
    rows = ""
    data = [("1.", "IQO-27-0412", 10, 9.5, 10, True), ("2.", "IQO-27-0077", 10, 8, 9, True),
            ("3.", "IQO-27-1203", 9, 8.5, 7.5, True), ("4.", "IQO-27-0981", 7, 6, 8, False)]
    for place, code, a, b, c, ok in data:
        badge = (f'<span class="badge badge--ok">{s["yes"]}</span>' if ok else f'<span class="badge badge--neutral">{s["no"]}</span>')
        rows += (f'<tr{QUALIFIED if ok else ""}><td>{place}</td><th scope="row">{code}</th>'
                 f'<td class="num">{a}</td><td class="num">{b}</td><td class="num">{c}</td><td class="num total">{a + b + c}</td><td>{badge}</td></tr>')
    head = "".join(f"<th>{c}</th>" for c in s["results_cols"])
    body = "".join(f'<h2 class="doc-heading doc-heading--chapter">{c}</h2><p>{s["text"]}</p>' for c in s["chapters"][:3])
    return f"""
<div class="doc-layout">
  <div class="doc-head">
    <div class="page-head iqo-page-head"><p class="iqo-page-head__kicker">{s["site"]}</p><h1 class="iqo-page-head__title">{s["page_title"]}</h1></div>
    <div class="cms-body prose doc-intro"><p>{s["text"]}</p></div>
  </div>
  <nav class="doc-toc" aria-labelledby="spis"><h2 class="doc-toc__title" id="spis">{s["toc_title"]}</h2><ol class="doc-toc__list">{toc}</ol></nav>
  <div class="doc-body prose">{body}
    <section class="results-block section">
      <div class="section__head"><h2 class="mt-0">{s["results_title"]}</h2></div>
      <div class="scroll" role="region" tabindex="0"><table class="table table--rank table--sticky-rank"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>
    </section>
  </div>
</div>"""


def news_index_html(s: dict) -> str:
    cards = "".join(
        f'<li><article class="card news-card iqo-news"><p class="iqo-news__meta"><time class="news-card__date">{d}</time></p>'
        f'<h2 class="iqo-news__title"><a class="iqo-news__link" href="#">{t}</a></h2><p class="iqo-news__lead">{x}</p>'
        f'<p class="card__foot iqo-news__more" aria-hidden="true"><span>{s["read_more"]}</span><span class="iqo-icon iqo-icon--arrow"></span></p></article></li>'
        for d, t, x in s["news"] * 2
    )
    return f"""
<div class="page-head"><span class="eyebrow">{s["newsroom"]}</span><h1>{s["news_index_title"]}</h1><div class="lead"><p>{s["text"]}</p></div></div>
<ul class="cards cards--wide news-list">{cards}</ul>"""


def footer_html(s: dict, o: dict) -> str:
    partners = ""
    if o.get("partners"):
        logos = "".join(
            f'<li class="sponsor-slider__item"><span class="sponsor-slider__frame"><img class="sponsor-slider__logo" '
            f'src="../assets/logo/{f}" alt="Partner {i}" width="540" height="258"></span></li>'
            for i, f in enumerate(["lockup.svg", "logo.svg", "lockup.svg", "logo.svg"], 1))
        partners = (f'<p class="iqo-footer__label" aria-hidden="true">{s["partners"]}</p>'
                    '<div class="sponsor-slider-dock"><div class="sponsor-slider" aria-label="Partners">'
                    f'<ul class="sponsor-slider__track">{logos}</ul></div></div>')
    return f"""
<footer class="footer iqo-footer">
  <div class="iqo-footer__partners">{partners}</div>
  <div class="iqo-footer__inner">
    <span class="iqo-footer__orbit" aria-hidden="true"></span>
    <div class="iqo-footer__head"><p class="iqo-footer__wordmark">{s["site"]}</p><p class="iqo-footer__tagline">{s["foot_tagline"]}</p></div>
    <div class="iqo-footer__cols">
      <div class="iqo-footer__col iqo-footer__col--brand"><a class="iqo-footer__logo" href="#">{logo_html(o["scheme"], o.get("logo", "lockup"), "cover")}<span class="visually-hidden">{s["site"]}</span></a></div>
      <div class="iqo-footer__col"><p class="iqo-footer__label">{s["foot_site"]}</p>
        <p class="footer__links iqo-footer__links"><a href="#">{s["privacy"]}</a><a href="#">{s["cookies"]}</a><a class="footer__link" href="#">{s["support"]}</a><a href="#">{s["posters"]}</a></p></div>
      <div class="iqo-footer__col iqo-footer__col--contact"><p class="iqo-footer__label">{s["foot_contact"]}</p>
        <p class="iqo-footer__organizer"><span>{s["organizer"]} <strong>{s["org"]}</strong></span></p>
        <p>ul. Przykładowa 1, 00-001 Warszawa</p>
        <p class="iqo-footer__contact"><a href="#">contact@iqo-official.org</a><a href="#">+48 22 000 00 00</a></p></div>
    </div>
    <div class="iqo-footer__base"><p class="iqo-footer__privacy">{s["rodo"]}</p><p class="iqo-footer__version">{s["version"]}</p></div>
  </div>
</footer>"""


PAGE = """<!doctype html>
<html lang="{lang}" dir="{dir}" data-theme="iqo-quantum" data-color-scheme="{scheme}"{layouts}{contrast_attr}>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{site} – IQO Quantum preview</title>
  <link rel="stylesheet" href="{app_css}">
  <link rel="stylesheet" href="{tokens_href}">
  <link rel="stylesheet" href="../theme.css">
</head>
<body>
<a class="skip-link" href="#tresc">{skip}</a>
{header}
<main class="page" id="tresc">{main}
</main>
{footer}
{cookie}
</body>
</html>
"""

DEFAULT_LAYOUTS = {"header": "split", "home-hero": "full-bleed", "footer": "columns", "cards": "outline"}


def render(lang: str, **o) -> str:
    s = STRINGS[lang]
    o.setdefault("scheme", "dark")
    layouts = {**DEFAULT_LAYOUTS, **o.get("layouts", {})}
    page = o.get("page", "home")
    main = {"home": home_html, "content": content_html, "news": news_index_html}[page](s)
    cookie = ""
    if o.get("cookie"):
        cookie = ('<div class="cookie-notice" role="region"><div class="cookie-notice__inner">'
                  f'<p class="cookie-notice__text">{s["cookie_text"]} <a class="cookie-notice__link" href="#">{s["cookies"]}</a></p>'
                  f'<button type="button" class="btn btn--small btn--accent cookie-notice__ok">{s["cookie_ok"]}</button></div></div>')
    return PAGE.format(
        lang=lang, dir=s["dir"], scheme=o["scheme"], site=s["site"], skip=s["skip"],
        layouts="".join(f' data-layout-{k}="{v}"' for k, v in layouts.items()),
        contrast_attr=' data-contrast="high"' if o.get("contrast") else "",
        app_css=os.path.relpath(APP_CSS, HERE).replace(os.sep, "/"),
        tokens_href="tokens.css" if o["scheme"] == "dark" else "tokens-light.css",
        header=header_html(s, {**o, "current": 0 if page == "home" else (5 if page == "news" else 1)}),
        main=main, footer=footer_html(s, o), cookie=cookie,
    )


#: nazwa: (język, opcje makiety, szerokość, wysokość, pełna strona)
VARIANTS = {
    "en-dark": ("en", {}, 1200, 900, False),
    "en-dark-full": ("en", {"partners": True}, 1440, 900, True),
    "en-light": ("en", {"scheme": "light"}, 1200, 900, True),
    "en-light-alt": ("en", {"scheme": "light", "layouts": {"header": "centered", "home-hero": "split",
                                                          "footer": "compact", "cards": "elevated"},
                            "logo": "mark"}, 1280, 900, True),
    "en-dark-alt": ("en", {"layouts": {"header": "centered", "cards": "elevated", "footer": "compact"},
                           "logo": "full"}, 1280, 900, False),
    "en-content": ("en", {"page": "content", "logged_in": True, "external": True}, 1280, 900, True),
    "en-content-light": ("en", {"page": "content", "scheme": "light"}, 1280, 900, True),
    "en-news-light": ("en", {"page": "news", "scheme": "light", "layouts": {"cards": "elevated"}}, 1200, 900, True),
    "en-account": ("en", {"page": "news", "logged_in": True, "account_open": True}, 1200, 700, False),
    "ar-rtl": ("ar", {}, 1200, 900, True),
    "ar-content": ("ar", {"page": "content", "scheme": "light"}, 1200, 900, False),
    "ru-long": ("ru", {"external": True}, 1200, 900, True),
    "hi-tall": ("hi", {}, 1200, 900, False),
    "en-contrast": ("en", {"contrast": True}, 1200, 900, True),
    "en-contrast-light": ("en", {"contrast": True, "scheme": "light", "page": "content"}, 1200, 900, False),
    "en-mobile": ("en", {"cookie": True}, 360, 800, True),
    "en-mobile-menu": ("en", {"menu_open": True}, 360, 800, False),
    "ru-mobile-light": ("ru", {"scheme": "light", "menu_open": True, "logged_in": True}, 360, 800, False),
    "en-tablet": ("en", {"scheme": "light"}, 820, 1000, False),
    "en-print": ("en", {}, 900, 1200, True),
}


def main() -> None:
    shots = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "shots"
    only = set(sys.argv[2:])
    shots.mkdir(parents=True, exist_ok=True)
    (HERE / "tokens.css").write_text(tokens_css("dark"), encoding="utf-8", newline="\n")
    (HERE / "tokens-light.css").write_text(tokens_css("light"), encoding="utf-8", newline="\n")
    (HERE / "index.html").write_text(render("en"), encoding="utf-8", newline="\n")
    variants = {k: v for k, v in VARIANTS.items() if not only or k in only}
    pages = {}
    for name, (lang, opts, *_rest) in variants.items():
        path = HERE / f"preview-{name}.html"
        path.write_text(render(lang, **opts), encoding="utf-8", newline="\n")
        pages[name] = path

    from playwright.sync_api import sync_playwright

    # Strony idą przez lokalny serwer HTTP (katalog repozytorium), a nie ``file://``: maski SVG
    # (``mask-image``) przeglądarka pobiera w trybie CORS, a ``file://`` go nie przechodzi.
    # Na serwerze to samo zapewnia nagłówek CORS bucketu (README, „Zależności”).
    handler = functools.partial(QuietHandler, directory=str(REPO))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, (lang, opts, w, h, full) in variants.items():
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1,
                                    color_scheme=opts.get("scheme", "dark"))
            if name.endswith("-print"):
                page.emulate_media(media="print")
            page.goto(base + pages[name].relative_to(REPO).as_posix())
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready")
            overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            out = shots / f"{name}.png"
            page.screenshot(path=str(out), full_page=full)
            print(f"{out}  (poziome przepełnienie: {overflow}px)")
            if name == "en-dark":
                target = THEME / "screenshot.png"
                page.screenshot(path=str(target))
                try:  # paleta 256 kolorów: ~4× mniejszy plik, różnica niewidoczna w galerii
                    from PIL import Image

                    Image.open(target).convert("RGB").quantize(256, dither=Image.Dither.NONE).save(
                        target, optimize=True)
                except ImportError:
                    pass
                print(target, target.stat().st_size, "B")
            page.close()
        browser.close()
    server.shutdown()
    for path in pages.values():
        path.unlink()


if __name__ == "__main__":
    main()
