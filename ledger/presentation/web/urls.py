from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from ledger.presentation.web import analytics_views, assets, views

app_name = "web"

urlpatterns = [
    path("", views.batch_list, name="batch-list"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            redirect_authenticated_user=True, extra_context={"demo_users": settings.DEMO_USERS}
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("batches/new/", views.batch_upload, name="batch-upload"),
    path("batches/<uuid:batch_id>/", views.batch_detail, name="batch-detail"),
    path("batches/<uuid:batch_id>/process/", views.batch_process, name="batch-process"),
    path("batches/<uuid:batch_id>/approve/", views.batch_approve, name="batch-approve"),
    path("batches/<uuid:batch_id>/reject/", views.batch_reject, name="batch-reject"),
    path(f"assets/{assets.CHART_JS}", assets.chart_js, name="chart-js"),
    path("analytics/", analytics_views.overview, name="analytics"),
    path("analytics/timeline/", analytics_views.timeline, name="analytics-timeline"),
    path("analytics/aml/", analytics_views.aml, name="analytics-aml"),
    path(
        "analytics/reconciliation/",
        analytics_views.reconciliation,
        name="analytics-reconciliation",
    ),
]
