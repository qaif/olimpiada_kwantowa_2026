"""Panel recenzenta: szablony komentarzy, licznik czasu, uwagi do linii i zgłoszenia problemów."""

import json
from io import BytesIO

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.grading.models import CommentSnippet, ReviewStatus, WorkIssue, WorkIssueKind
from apps.grading.snippets import add_own_snippet, set_problem_snippets
from apps.grading.tests.factories import ReviewFactory
from apps.grading.worklog import heartbeat
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db


def _review(stage, problem, reviewer, *, status=ReviewStatus.ASSIGNED, **kwargs):
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=stage),
        problem=problem,
        status=SubmissionStatus.IN_REVIEW,
    )
    return ReviewFactory(submission=submission, reviewer=reviewer, status=status, **kwargs)


def _store_python(submission, source: str):
    key = f"1/1/OLM-TEST/{submission.pk}-py/{'e' * 64}.py"
    get_submission_storage().put(key, BytesIO(source.encode("utf-8")), "text/x-python")
    return SubmissionFileFactory(
        submission=submission,
        object_key=key,
        mime="text/x-python",
        av_status=AvStatus.CLEAN,
        size_bytes=len(source),
    )


# --- szablony komentarzy ------------------------------------------------------------------------


def test_strona_oceny_pokazuje_szablony_wspolne_i_wlasne(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    set_problem_snippets(problems[0], [{"title": "Wspólny", "text": "treść wspólna"}])
    add_own_snippet(reviewer, "Własny", "treść własna")
    web_client.force_login(reviewer.user)

    body = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "treść wspólna" in body
    assert "treść własna" in body
    # Bez JavaScriptu treść jest do skopiowania – dlatego stoi w HTML-u, a nie tylko w atrybucie.
    assert "data-snippet-insert" in body


def test_recenzent_dopisuje_wlasny_szablon(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    web_client.force_login(reviewer.user)

    response = web_client.post(
        reverse("web:review-snippet-add"),
        {"review": review.pk, "title": "Brak jednostek", "text": "Podaj jednostki."},
    )

    assert response.status_code == 302
    snippet = CommentSnippet.objects.get(owner=reviewer)
    assert snippet.title == "Brak jednostek"
    assert snippet.problem_id is None


def test_zaznaczony_zakres_wiaze_szablon_z_zadaniem(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    web_client.force_login(reviewer.user)

    web_client.post(
        reverse("web:review-snippet-add"),
        {"review": review.pk, "title": "T", "text": "t", "scope": "problem"},
    )

    assert CommentSnippet.objects.get(owner=reviewer).problem_id == problems[0].pk


def test_recenzent_kasuje_wlasny_szablon(web_client, elim_stage):
    reviewer = ActiveReviewerFactory()
    snippet = add_own_snippet(reviewer, "Mój", "m")
    web_client.force_login(reviewer.user)

    web_client.post(reverse("web:review-snippet-delete", kwargs={"pk": snippet.pk}), {})

    assert not CommentSnippet.objects.filter(pk=snippet.pk).exists()


def test_kasowanie_cudzego_szablonu_to_404(web_client, elim_stage, problems):
    mine = ActiveReviewerFactory()
    snippet = add_own_snippet(ActiveReviewerFactory(), "Cudzy", "c")
    web_client.force_login(mine.user)

    response = web_client.post(reverse("web:review-snippet-delete", kwargs={"pk": snippet.pk}), {})

    assert response.status_code == 404
    assert CommentSnippet.objects.filter(pk=snippet.pk).exists()


def test_kasowanie_szablonu_wspolnego_to_404(web_client, elim_stage, problems):
    """Wspólny szablon należy do zadania i kasuje go koordynator, nie recenzent."""
    reviewer = ActiveReviewerFactory()
    set_problem_snippets(problems[0], [{"title": "Wspólny", "text": "w"}])
    shared = CommentSnippet.objects.get(problem=problems[0])
    web_client.force_login(reviewer.user)

    response = web_client.post(reverse("web:review-snippet-delete", kwargs={"pk": shared.pk}), {})

    assert response.status_code == 404


def test_koordynator_zapisuje_szablony_na_stronie_zadania(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)
    problem = problems[0]

    response = web_client.post(
        reverse("web:coordinator-problem-edit", kwargs={"pk": problem.pk}),
        {
            "number": problem.number,
            "title": problem.title,
            "allowed_formats": problem.allowed_formats,
            "max_file_mb": problem.max_file_mb,
            "scoring_values": "",
            "max_points": "",
            "rubric": "",
            "reviewer_notes": "",
            "comment_snippets": "Brak jednostek;Podaj jednostki wyniku.",
        },
    )

    assert response.status_code == 302
    assert CommentSnippet.objects.get(problem=problem, owner=None).text == "Podaj jednostki wyniku."


# --- czas pracy ---------------------------------------------------------------------------------


def test_heartbeat_zaklada_licznik_i_zwraca_opis(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    web_client.force_login(reviewer.user)

    response = web_client.post(reverse("web:review-heartbeat", kwargs={"pk": review.pk}))

    assert response.status_code == 200
    assert json.loads(response.content)["label"] == "poniżej minuty"


def test_heartbeat_do_cudzej_recenzji_to_404(web_client, elim_stage, problems):
    review = _review(elim_stage, problems[0], ActiveReviewerFactory())
    intruder = ActiveReviewerFactory()
    web_client.force_login(intruder.user)

    response = web_client.post(reverse("web:review-heartbeat", kwargs={"pk": review.pk}))

    assert response.status_code == 404


def test_strona_oceny_pokazuje_zmierzony_czas(web_client, elim_stage, problems):
    from datetime import timedelta

    from django.utils import timezone

    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    start = timezone.now()
    heartbeat(review, now=start - timedelta(seconds=90))
    heartbeat(review, now=start - timedelta(seconds=30))
    web_client.force_login(reviewer.user)

    body = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "Czas pracy: 1 min" in body


def test_recenzja_zamknieta_nie_uruchamia_licznika(web_client, elim_stage, problems):
    """Mierzenie czasu oglądania recenzji, której nie wolno zmieniać, nie jest mierzeniem pracy."""
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer, status=ReviewStatus.CANCELLED)
    web_client.force_login(reviewer.user)

    body = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "review-worklog.js" not in body


# --- podgląd kodu i uwagi do linii ---------------------------------------------------------------


def test_rozwiazanie_w_pythonie_ma_listing_z_numerami_linii(web_client, elim_stage):
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=elim_stage, number=7, allowed_formats=["py"])
    review = _review(elim_stage, problem, reviewer)
    _store_python(review.submission, "import math\nprint(math.pi)\n")
    web_client.force_login(reviewer.user)

    body = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "code-listing" in body
    assert "import math" in body
    assert "Dodaj uwagę do linii" in body


def test_rozwiazanie_w_pdf_nie_ma_sekcji_kodu(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    SubmissionFileFactory(submission=review.submission, av_status=AvStatus.CLEAN, mime="application/pdf")
    web_client.force_login(reviewer.user)

    body = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "code-listing" not in body


def test_recenzent_dopisuje_uwage_do_linii(web_client, elim_stage):
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=elim_stage, number=8, allowed_formats=["py"])
    review = _review(elim_stage, problem, reviewer)
    _store_python(review.submission, "a = 1\nb = 2\n")
    web_client.force_login(reviewer.user)

    response = web_client.post(
        reverse("web:review-line-note", kwargs={"pk": review.pk}),
        {"line": "2", "text": "b nigdy nie jest używane", "public": "1"},
    )

    assert response.status_code == 302
    review.refresh_from_db()
    assert review.annotations == [{"line": 2, "text": "b nigdy nie jest używane", "public": True}]


def test_uwaga_do_cudzej_recenzji_to_404(web_client, elim_stage, problems):
    review = _review(elim_stage, problems[0], ActiveReviewerFactory())
    web_client.force_login(ActiveReviewerFactory().user)

    response = web_client.post(
        reverse("web:review-line-note", kwargs={"pk": review.pk}), {"line": "1", "text": "x"}
    )

    assert response.status_code == 404


# --- zgłoszenia problemów -------------------------------------------------------------------------


def test_recenzent_zglasza_problem_i_widzi_baner(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    web_client.force_login(reviewer.user)

    web_client.post(
        reverse("web:review-issue-add", kwargs={"pk": review.pk}),
        {"kind": WorkIssueKind.UNREADABLE, "text": "Skan jest nieczytelny."},
    )

    assert WorkIssue.objects.filter(review=review).count() == 1
    body = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()
    assert "Zgłosiłeś problem z tą pracą" in body


def test_zgloszenie_nie_blokuje_wystawienia_oceny(web_client, elim_stage, problems):
    """Zablokowanie formularza zamieniłoby sygnał w ultimatum – i wypchnęło komitet do poczty."""
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    web_client.force_login(reviewer.user)
    web_client.post(
        reverse("web:review-issue-add", kwargs={"pk": review.pk}),
        {"kind": WorkIssueKind.UNREADABLE, "text": "nieczytelne"},
    )

    response = web_client.post(
        reverse("web:review-submit", kwargs={"pk": review.pk}),
        {"score": 2, "comment_internal": "", "comment_for_participant": "", "annotations": ""},
    )

    assert response.status_code == 302
    review.refresh_from_db()
    assert review.status == ReviewStatus.SUBMITTED


def test_lista_przydzialow_oznacza_prace_ze_zgloszeniem(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    web_client.force_login(reviewer.user)
    web_client.post(
        reverse("web:review-issue-add", kwargs={"pk": review.pk}),
        {"kind": WorkIssueKind.OTHER, "text": "coś nie gra"},
    )

    body = web_client.get(reverse("web:review-list")).content.decode()

    assert "zgłoszony problem" in body


def test_zgloszenie_do_cudzej_recenzji_to_404(web_client, elim_stage, problems):
    review = _review(elim_stage, problems[0], ActiveReviewerFactory())
    web_client.force_login(ActiveReviewerFactory().user)

    response = web_client.post(
        reverse("web:review-issue-add", kwargs={"pk": review.pk}),
        {"kind": WorkIssueKind.OTHER, "text": "x"},
    )

    assert response.status_code == 404
