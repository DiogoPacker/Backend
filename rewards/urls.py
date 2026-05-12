from django.urls import path

from . import views

urlpatterns = [
    path("customer/<str:customer_email>/", views.get_customer_rewards, name="get_customer_rewards"),
    path("customer/<str:customer_email>/history/", views.get_rewards_history, name="get_rewards_history"),
    path("apply/", views.apply_rewards, name="apply_rewards"),
]
