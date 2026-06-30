from django.urls import path

from .views import JobCancelView, JobCreateView, JobResultView, JobStatusView, SuggestPatternsView

urlpatterns = [
    path("jobs/", JobCreateView.as_view(), name="job-create"),
    path("jobs/suggest-patterns/", SuggestPatternsView.as_view(), name="job-suggest-patterns"),
    path("jobs/<uuid:job_id>/status/", JobStatusView.as_view(), name="job-status"),
    path("jobs/<uuid:job_id>/results/", JobResultView.as_view(), name="job-results"),
    path("jobs/<uuid:job_id>/cancel/", JobCancelView.as_view(), name="job-cancel"),
]
