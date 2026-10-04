"""Podgląd motywu IQO Quantum bez Django: statyczna makieta strony głównej + zrzuty Playwrightem.

    uv run --no-project --with playwright python -m playwright install chromium
    uv run --no-project --with playwright --with pillow python themes/iqo-quantum/_preview/render.py [katalog_zrzutów]

Co robi:
1. ``tokens.css`` (schemat ``dark`` z manifestu) i ``tokens-light.css`` – generatorem platformy
   (``backend/apps/themes/tokens.py``: ``parse_tokens`` + ``build_tokens_css``), jeśli jest
   w repozytorium albo wskazany zmienną ``IQO_TOKENS_PY``; inaczej prostą emulacją.
2. ``index.html`` (en, ciemny) i warianty ``preview-<nazwa>.html`` – makieta z **tymi samymi
   klasami**, które renderują ``base.html`` aplikacji i sloty ``templates/theme/*.html`` motywu.
   Arkusze: ``app.css`` aplikacji (``IQO_APP_CSS`` albo ``backend/static/css/app.css``) →
   tokeny → ``../theme.css``. Logo w makiecie = ``{{ theme.assets }}`` zastąpione ``../``.
3. Zrzuty: ``screenshot.png`` paczki (1200×900, en, ciemny) i warianty do przeglądu
   (ar/RTL, ru, hi, jasny, wysoki kontrast, telefon, wydruk) w katalogu zrzutów.

Katalog ``_preview/`` nie wchodzi do ZIP-a (build_zip.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
THEME = HERE.parent
REPO = THEME.parent.parent
APP_CSS = pathlib.Path(os.environ.get("IQO_APP_CSS") or REPO / "backend" / "static" / "css" / "app.css")
TOKENS_PY = pathlib.Path(os.environ.get("IQO_TOKENS_PY") or REPO / "backend" / "apps" / "themes" / "tokens.py")


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
        for warning in parsed.warnings + generated.warnings:
            print("tokens:", warning)
        return generated.css
    data = json.loads(raw)
    values = {**(data["dark"] if scheme == "dark" else data["colors"]), **data["tokens"]}
    body = "\n".join(f"  --t-{k}: {v};" for k, v in values.items())
    return f"/* Emulacja tokens.css (brak apps/themes/tokens.py). */\n:root {{\n  color-scheme: {scheme};\n{body}\n}}\n"


# --- 2. makieta -----------------------------------------------------------------------------------

STRINGS = {
    "en": dict(
        dir="ltr", site="International Quantum Olympiad", edition="2027 edition",
        nav=["About", "Problems", "Schedule", "Results", "News", "FAQ"], docs="Documents",
        docs_items=["All documents", "Rules", "Privacy policy"],
        lang_label="English", lang_hidden="Interface language", contrast="High contrast: on",
        support="Report a problem", login="Log in", register="Register",
        kicker="Edition 2027 · Warsaw", title="International Quantum Olympiad",
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
        news_title="News", news_all="All news",
        news=[("2 Oct 2026", "Registration for the 2027 edition is open", "Students from all countries can now create accounts and download the Stage I problems."),
              ("24 Sep 2026", "Free online workshops start in October", "Six sessions on qubits, measurement and entanglement, recorded for later viewing."),
              ("12 Sep 2026", "Meet the scientific committee", "Physicists from eleven universities will set and grade the problems.")],
        foot_tagline="|IQO⟩ – quantum physics for secondary schools",
        privacy="Privacy policy (GDPR)", cookies="Cookie policy", posters="Posters to download",
        rodo="Participants' personal data are processed according to the data-minimisation principle (GDPR): public results tables only show the participant code; full data are visible to the coordinator only.",
        organizer="Organiser:", org="Quantum AI Foundation", version="version 0.41.0",
        cookie_text="This website only uses necessary cookies (login session, form protection). We do not use advertising or analytics cookies.",
        cookie_ok="Got it",
    ),
    "ar": dict(
        dir="rtl", site="الأولمبياد الدولي للكم", edition="دورة 2027",
        nav=["حول", "المسائل", "الجدول الزمني", "النتائج", "الأخبار", "الأسئلة الشائعة"], docs="الوثائق",
        docs_items=["كل الوثائق", "اللوائح", "سياسة الخصوصية"],
        lang_label="العربية", lang_hidden="لغة الواجهة", contrast="تباين عالٍ: تشغيل",
        support="الإبلاغ عن مشكلة", login="تسجيل الدخول", register="التسجيل",
        kicker="دورة 2027 · وارسو", title="الأولمبياد الدولي لفيزياء الكم",
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
        news_title="الأخبار", news_all="كل الأخبار",
        news=[("2 أكتوبر 2026", "التسجيل لدورة 2027 مفتوح", "يمكن للطلاب من جميع البلدان إنشاء حسابات الآن."),
              ("24 سبتمبر 2026", "ورش عمل مجانية عبر الإنترنت", "ست جلسات حول الكيوبتات والقياس والتشابك."),
              ("12 سبتمبر 2026", "تعرّف على اللجنة العلمية", "فيزيائيون من إحدى عشرة جامعة.")],
        foot_tagline="|IQO⟩ – فيزياء الكم للمدارس الثانوية",
        privacy="سياسة الخصوصية", cookies="سياسة ملفات تعريف الارتباط", posters="ملصقات للتنزيل",
        rodo="تُعالج البيانات الشخصية للمشاركين وفق مبدأ تقليل البيانات: جداول النتائج العامة تعرض رمز المشارك فقط.",
        organizer="الجهة المنظمة:", org="Quantum AI Foundation", version="الإصدار 0.41.0",
        cookie_text="يستخدم هذا الموقع ملفات تعريف الارتباط الضرورية فقط.", cookie_ok="فهمت",
    ),
    "ru": dict(
        dir="ltr", site="Международная квантовая олимпиада", edition="выпуск 2027",
        nav=["Об олимпиаде", "Задачи", "Расписание", "Результаты", "Новости", "Частые вопросы"], docs="Документы",
        docs_items=["Все документы", "Положение", "Политика конфиденциальности"],
        lang_label="Русский", lang_hidden="Язык интерфейса", contrast="Высокий контраст: включить",
        support="Сообщить о проблеме", login="Войти", register="Зарегистрироваться",
        kicker="Выпуск 2027 · Варшава", title="Международная квантовая олимпиада",
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
        news_title="Новости", news_all="Все новости",
        news=[("2 окт. 2026", "Открыта регистрация на выпуск 2027", "Школьники из всех стран уже могут создавать учётные записи."),
              ("24 сент. 2026", "Бесплатные онлайн-семинары", "Шесть занятий о кубитах, измерении и запутанности."),
              ("12 сент. 2026", "Научный комитет олимпиады", "Физики из одиннадцати университетов.")],
        foot_tagline="|IQO⟩ – квантовая физика для старших классов",
        privacy="Политика конфиденциальности", cookies="Политика использования файлов cookie", posters="Плакаты для скачивания",
        rodo="Персональные данные участников обрабатываются по принципу минимизации: в публичных таблицах результатов виден только код участника.",
        organizer="Организатор:", org="Quantum AI Foundation", version="версия 0.41.0",
        cookie_text="Этот сайт использует только необходимые файлы cookie.", cookie_ok="Понятно",
    ),
    "hi": dict(
        dir="ltr", site="अंतर्राष्ट्रीय क्वांटम ओलंपियाड", edition="संस्करण 2027",
        nav=["परिचय", "प्रश्न", "समय-सारणी", "परिणाम", "समाचार", "सामान्य प्रश्न"], docs="दस्तावेज़",
        docs_items=["सभी दस्तावेज़", "नियम", "गोपनीयता नीति"],
        lang_label="हिन्दी", lang_hidden="इंटरफ़ेस भाषा", contrast="उच्च कंट्रास्ट: चालू",
        support="समस्या की सूचना दें", login="लॉग इन करें", register="पंजीकरण करें",
        kicker="संस्करण 2027 · वारसॉ", title="अंतर्राष्ट्रीय क्वांटम ओलंपियाड",
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
        news_title="समाचार", news_all="सभी समाचार",
        news=[("2 अक्टूबर 2026", "संस्करण 2027 के लिए पंजीकरण खुला", "सभी देशों के छात्र अब खाते बना सकते हैं।"),
              ("24 सितंबर 2026", "निःशुल्क ऑनलाइन कार्यशालाएँ", "क्यूबिट, मापन और उलझाव पर छह सत्र।"),
              ("12 सितंबर 2026", "वैज्ञानिक समिति से मिलें", "ग्यारह विश्वविद्यालयों के भौतिक विज्ञानी।")],
        foot_tagline="|IQO⟩ – माध्यमिक विद्यालयों के लिए क्वांटम भौतिकी",
        privacy="गोपनीयता नीति", cookies="कुकी नीति", posters="डाउनलोड के लिए पोस्टर",
        rodo="प्रतिभागियों के व्यक्तिगत डेटा को न्यूनतमीकरण सिद्धांत के अनुसार संसाधित किया जाता है।",
        organizer="आयोजक:", org="Quantum AI Foundation", version="संस्करण 0.41.0",
        cookie_text="यह वेबसाइट केवल आवश्यक कुकीज़ का उपयोग करती है।", cookie_ok="समझ गया",
    ),
}

PAGE = """<!doctype html>
<html lang="{lang}" dir="{dir}"{contrast_attr}>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{site} – IQO Quantum preview</title>
  <link rel="stylesheet" href="{app_css}">
  <link rel="stylesheet" href="{tokens_href}">
  <link rel="stylesheet" href="../theme.css">
</head>
<body>
<a class="skip-link" href="#tresc">Skip to content</a>

<!-- slot: theme/header.html -->
<header class="iqo-header" data-iqo-header>
  <div class="iqo-header__inner">
    <a class="iqo-brand" href="#">
      <img class="iqo-brand__logo" src="../assets/logo/{logo}" alt="" width="540" height="258">
      <span class="iqo-brand__name">{site}</span>
      <span class="iqo-brand__edition">{edition}</span>
    </a>
    <nav class="nav nav--cms iqo-nav" aria-label="Site">
      {nav_html}
      <details class="nav-menu">
        <summary class="nav__link nav-menu__summary">{docs}</summary>
        <ul class="nav-menu__list">{docs_html}</ul>
      </details>
    </nav>
    <nav class="account-bar iqo-account" aria-label="Account">
      <form class="inline account-bar__prefs">
        <details class="lang-menu">
          <summary class="btn btn--small btn--ghost account-bar__btn lang-menu__summary">
            <svg class="pref-icon" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
              <circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" stroke-width="1.5"/>
              <path d="M1.75 9h14.5M9 1.75c2 2 3 4.4 3 7.25s-1 5.25-3 7.25M9 1.75c-2 2-3 4.4-3 7.25s1 5.25 3 7.25" fill="none" stroke="currentColor" stroke-width="1.2"/>
            </svg>
            <span>{lang_label}</span><span class="visually-hidden">{lang_hidden}</span>
          </summary>
          <ul class="lang-menu__list"><li><span class="lang-menu__item lang-menu__item--current">{lang_label}</span></li></ul>
        </details>
      </form>
      <form class="inline account-bar__prefs">
        <button type="button" class="btn btn--small btn--ghost account-bar__btn account-bar__btn--icon" aria-pressed="false" aria-label="{contrast}" title="{contrast}">
          <svg class="pref-icon" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
            <circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" stroke-width="1.5"/>
            <path d="M9 1.75a7.25 7.25 0 0 1 0 14.5z" fill="currentColor"/>
          </svg>
        </button>
      </form>
      <span class="account-bar__sep" aria-hidden="true"></span>
      <a class="account-bar__link" href="#">{support}</a>
      <a class="btn btn--small btn--secondary account-bar__btn" href="#">{login}</a>
      <a class="btn btn--small btn--accent account-bar__btn" href="#">{register}</a>
    </nav>
  </div>
</header>

<main class="page" id="tresc">
<!-- slot: theme/home_hero.html -->
<section class="iqo-hero hero--slider" data-hero-slider aria-label="Highlights">
  <div class="hero-slider__viewport iqo-hero__viewport" tabindex="-1">
    <div class="hero-slide iqo-slide iqo-slide--intro" role="group">
      <div class="iqo-hero__inner">
        <p class="iqo-hero__kicker">{kicker}</p>
        <h1 class="iqo-hero__title">{title}</h1>
        <div class="iqo-hero__text"><p>{text}</p></div>
        <div class="iqo-hero__actions">
          <a class="btn btn--accent" href="#">{register}</a>
          <a class="btn btn--secondary" href="#">{login}</a>
        </div>
        <p class="iqo-hero__status">{status_pre} <strong>{stage}</strong> {status_post}</p>
      </div>
    </div>
  </div>
</section>

<section class="section">
  <div class="section__head">
    <h2 class="mt-0">{timeline_title}</h2>
    <p class="hint">{timeline_hint}</p>
  </div>
  <ol class="timeline">{stages_html}</ol>
</section>

<section class="section">
  <div class="section__head"><h2 class="mt-0">{steps_title}</h2></div>
  <ol class="steps">{steps_html}</ol>
  <p class="steps__cta"><a class="btn btn--accent" href="#">{register}</a></p>
</section>

<section class="section" id="aktualnosci">
  <div class="section__head">
    <h2 class="mt-0">{news_title}</h2>
    <a href="#">{news_all}</a>
  </div>
  <ul class="cards">{news_html}</ul>
</section>
</main>

<!-- slot: theme/footer.html -->
<footer class="footer iqo-footer">
  <div class="iqo-footer__inner">
    <div class="iqo-footer__top">
      <p class="iqo-footer__brand">
        <img class="iqo-footer__mark" src="../assets/logo/mark-white.svg" alt="" width="44" height="44">
        <span><strong>{site}</strong><span class="iqo-footer__tagline">{foot_tagline}</span></span>
      </p>
      <p class="footer__links iqo-footer__links">
        <a href="#">{privacy}</a><a href="#">{cookies}</a><a class="footer__link" href="#">{support}</a><a href="#">{posters}</a>
      </p>
    </div>
    <p class="iqo-footer__privacy">{rodo}</p>
    <div class="iqo-footer__meta">
      <p class="iqo-footer__organizer"><span>{organizer} <strong>{org}</strong></span></p>
      <p>ul. Przykładowa 1, 00-001 Warszawa</p>
      <p class="iqo-footer__contact"><a href="#">contact@iqo-official.org</a></p>
      <p class="iqo-footer__version">{version}</p>
    </div>
  </div>
</footer>
{cookie_html}
</body>
</html>
"""


def render(lang: str, scheme: str = "dark", contrast: bool = False, cookie: bool = False) -> str:
    s = STRINGS[lang]
    current = ' aria-current="page"'
    nav_html = "\n      ".join(
        f'<a class="nav__link" href="#"{current if i == 0 else ""}>{t}</a>'
        for i, t in enumerate(s["nav"])
    )
    docs_html = "".join(f'<li><a class="nav-menu__link" href="#">{t}</a></li>' for t in s["docs_items"])
    stages_html = ""
    for kind, state, badge, dates, results in s["stages"]:
        badge_cls = {"done": "badge--ok", "open": "badge--accent", "next": "badge--neutral"}[state]
        rows = "".join(f"<div><dt>{dt}</dt><dd>{dd}</dd></div>" for dt, dd in dates)
        foot = (f'<a class="btn btn--secondary btn--small" href="#">{results}</a>' if results
                else f'<p class="hint mt-0">{s["not_announced"]}</p>')
        stages_html += (
            f'<li class="timeline__item timeline__item--{state}"><p class="timeline__kind">{kind}</p>'
            f'<span class="badge {badge_cls}">{badge}</span><dl class="timeline__dates">{rows}</dl>{foot}</li>'
        )
    steps_html = "".join(
        f'<li class="steps__item"><p class="steps__title">{t}</p><p class="steps__text">{x}</p></li>'
        for t, x in s["steps"]
    )
    news_html = "".join(
        f'<li><article class="card news-card"><span class="news-card__date">{d}</span>'
        f'<h3><a href="#">{t}</a></h3><p>{x}</p></article></li>'
        for d, t, x in s["news"]
    )
    cookie_html = ""
    if cookie:
        cookie_html = (
            '<div class="cookie-notice" role="region"><div class="cookie-notice__inner">'
            f'<p class="cookie-notice__text">{s["cookie_text"]} <a class="cookie-notice__link" href="#">{s["cookies"]}</a></p>'
            f'<button type="button" class="btn btn--small btn--accent cookie-notice__ok">{s["cookie_ok"]}</button>'
            "</div></div>"
        )
    fields = {k: v for k, v in s.items() if isinstance(v, str)}
    return PAGE.format(
        lang=lang, scheme=scheme, contrast_attr=' data-contrast="high"' if contrast else "",
        app_css=os.path.relpath(APP_CSS, HERE).replace(os.sep, "/"),
        tokens_href="tokens.css" if scheme == "dark" else "tokens-light.css",
        logo="lockup-white.svg" if scheme == "dark" else "lockup.svg",
        nav_html=nav_html, docs_html=docs_html, stages_html=stages_html, steps_html=steps_html,
        news_html=news_html, cookie_html=cookie_html, **fields,
    )


VARIANTS = {
    # nazwa: (język, schemat, wysoki kontrast, szerokość, wysokość, pełna strona)
    "en-dark": ("en", "dark", False, 1200, 900, False),
    "en-dark-full": ("en", "dark", False, 1440, 900, True),
    "ar-rtl": ("ar", "dark", False, 1200, 900, True),
    "ru-long": ("ru", "dark", False, 1200, 900, True),
    "hi-tall": ("hi", "dark", False, 1200, 900, False),
    "en-light": ("en", "light", False, 1200, 900, True),
    "en-contrast": ("en", "dark", True, 1200, 900, False),
    "en-mobile": ("en", "dark", False, 390, 844, True),
    "en-print": ("en", "dark", False, 900, 1200, True),
}


def main() -> None:
    shots = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    (HERE / "tokens.css").write_text(tokens_css("dark"), encoding="utf-8", newline="\n")
    (HERE / "tokens-light.css").write_text(tokens_css("light"), encoding="utf-8", newline="\n")
    (HERE / "index.html").write_text(render("en"), encoding="utf-8", newline="\n")
    pages = {}
    for name, (lang, scheme, contrast, *_rest) in VARIANTS.items():
        path = HERE / f"preview-{name}.html"
        path.write_text(render(lang, scheme, contrast, cookie=(name == "en-mobile")), encoding="utf-8", newline="\n")
        pages[name] = path

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, (lang, scheme, contrast, w, h, full) in VARIANTS.items():
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1,
                                    color_scheme="dark")
            if name.endswith("-print"):
                page.emulate_media(media="print")
            page.goto(pages[name].as_uri())
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
    for path in pages.values():
        path.unlink()


if __name__ == "__main__":
    main()
