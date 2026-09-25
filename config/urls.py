from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("ledger.presentation.api.urls")),
    path("", include("ledger.presentation.web.urls")),
]

handler500 = "ledger.presentation.api.errors.server_error"
