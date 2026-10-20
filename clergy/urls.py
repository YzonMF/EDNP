from django.urls import path

from . import views

app_name = 'clergy'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('my-profile/', views.my_profile, name='my_profile'),
    path('my-profile/print/', views.print_own_record, name='print_own_record'),
    path('my-profile/print/download/', views.download_own_record, name='download_own_record'),
    path('my-requirements/', views.my_requirements, name='my_requirements'),
    path('my-requirements/ember-letters/upload/', views.upload_ember_letter, name='upload_ember_letter'),
    path('notifications/', views.notifications, name='notifications'),
]
