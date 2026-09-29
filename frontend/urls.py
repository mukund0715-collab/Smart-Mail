from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("email/<int:pk>/", views.email_detail, name="email-detail"),
    path("action-item/<int:pk>/toggle/", views.toggle_action_item, name="toggle-action-item"),
    path("email/<int:email_id>/reply/", views.update_reply, name="update-reply"),
]
