from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", include("apps.core.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/competitions/", include("apps.competitions.urls")),
    path("api/", include("apps.submissions.urls")),
    path("api/grading/", include("apps.grading.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
