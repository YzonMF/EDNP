"""
URL configuration for ednp project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from . import views

# No blanket static()/MEDIA_URL serving here on purpose — every uploaded file
# (clergy documents, generated reports, backup archives) is sensitive and is
# served exclusively through authenticated, permission-checked views instead
# (see registrar.views.view_document/download_document and friends). Serving
# MEDIA_ROOT directly would bypass all of those checks.

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.index, name='index'),
    path('logout/', views.logout_view, name='logout'),
    path('registrar/', include('registrar.urls')),
    path('clergy/', include('clergy.urls')),
]
