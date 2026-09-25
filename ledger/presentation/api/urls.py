from django.urls import path

from ledger.presentation.api import views

app_name = "ledger_api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("batches/", views.BatchCollectionView.as_view(), name="batch-list"),
    path("batches/<uuid:batch_id>/", views.BatchDetailView.as_view(), name="batch-detail"),
    path(
        "batches/<uuid:batch_id>/process/",
        views.ProcessBatchView.as_view(),
        name="batch-process",
    ),
    path(
        "batches/<uuid:batch_id>/approve/",
        views.ApproveBatchView.as_view(),
        name="batch-approve",
    ),
    path(
        "batches/<uuid:batch_id>/reject/",
        views.RejectBatchView.as_view(),
        name="batch-reject",
    ),
]
