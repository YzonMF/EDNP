from django.urls import path

from . import views

app_name = 'registrar'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('clergy-records/', views.clergy_records, name='clergy_records'),
    path('clergy-records/create/', views.create_record, name='create_record'),
    path('clergy-records/<str:record_id>/link-account/', views.link_account, name='link_account'),
    path('clergy-records/<str:record_id>/', views.record_detail, name='record_detail'),
    path('documents/', views.documents_overview, name='documents_overview'),
    path('ordination-requirements/', views.ordination_requirements, name='ordination_requirements'),
    path('documents/<int:document_id>/view/', views.view_document, name='view_document'),
    path('documents/<int:document_id>/download/', views.download_document, name='download_document'),
    path('contract-review/', views.contract_review, name='contract_review'),
    path('generate-report/', views.generate_report, name='generate_report'),
    path('generate-report/<int:report_id>/view/', views.view_generated_report, name='view_generated_report'),
    path('generate-report/<int:report_id>/preview/', views.preview_generated_report, name='preview_generated_report'),
    path('generate-report/<int:report_id>/download/', views.download_generated_report, name='download_generated_report'),
    path('change-password/', views.change_password, name='change_password'),
    path('backup-records/', views.backup_records, name='backup_records'),
    path('backup-records/database/', views.download_database_backup, name='download_database_backup'),
    path('backup-records/<int:archive_id>/download/', views.download_backup_archive, name='download_backup_archive'),
]
