"""URL raíz del proyecto Invernadero Nona Olimpia."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('App.urls')),
]
