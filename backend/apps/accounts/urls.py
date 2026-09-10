from django.urls import path

from .api import (
    CommitteeApproveView,
    CommitteePendingListView,
    CommitteeVerifyDistrictView,
    ConsentSetView,
    LoginView,
    LogoutView,
    MeView,
    RegisterCommitteeView,
    RegisterParticipantView,
)

app_name = "accounts"

urlpatterns = [
    path("register/participant/", RegisterParticipantView.as_view(), name="register-participant"),
    path("register/committee/", RegisterCommitteeView.as_view(), name="register-committee"),
    # Treść zgód rejestracyjnych – publicznie, żeby klient zewnętrzny pokazał to samo brzmienie.
    path("consents/", ConsentSetView.as_view(), name="consents"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("committee/pending/", CommitteePendingListView.as_view(), name="committee-pending"),
    path("committee/<int:pk>/approve/", CommitteeApproveView.as_view(), name="committee-approve"),
    path(
        "committee/<int:pk>/verify-district/",
        CommitteeVerifyDistrictView.as_view(),
        name="committee-verify-district",
    ),
]
