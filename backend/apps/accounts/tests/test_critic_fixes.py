"""Testy do findings Critica z T-02."""

import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from apps.accounts.models import CommitteeStatus, User

from .factories import CoordinatorFactory, PendingReviewerFactory, UserFactory


@pytest.mark.django_db
def test_email_unikalny_bez_rozrozniania_wielkosci_liter():
    UserFactory(email="foo@example.test")
    with pytest.raises(IntegrityError):
        User.objects.create(email="Foo@Example.test")


@pytest.mark.django_db
def test_save_normalizuje_email_do_lowercase():
    u = User(email="  Foo@Example.test ")
    u.set_password("Silne.Haslo.123")
    u.save()
    assert u.email == "foo@example.test"


@pytest.mark.django_db
def test_approve_nie_odwiesza_zawieszonego():
    member = PendingReviewerFactory()
    member.status = CommitteeStatus.SUSPENDED
    member.save()
    client = APIClient()
    client.force_authenticate(CoordinatorFactory())
    resp = client.post(f"/api/auth/committee/{member.pk}/approve/")
    assert resp.status_code == 400
    assert resp.json()["code"] == "NOT_PENDING"
    member.refresh_from_db()
    assert member.status == CommitteeStatus.SUSPENDED
