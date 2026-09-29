import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.conf import settings

from core.models import ActionItem, EmailRecord

logger = logging.getLogger(__name__)


@login_required
def dashboard(request):
    """
    Main inbox view: every ingested email with its derived intent,
    sentiment, priority flag, and reply status. Supports simple
    querystring filters so the JS-free fallback still works without
    JavaScript (filters are also re-applied client-side for instant
    feedback — see dashboard.html).
    """
    emails = (
        EmailRecord.objects.select_related("metadata", "generated_reply")
        .prefetch_related("action_items")
        .all()
    )

    priority_only = request.GET.get("priority") == "1"
    if priority_only:
        emails = emails.filter(metadata__priority_flag=True)

    intent = request.GET.get("intent", "")
    if intent:
        emails = emails.filter(metadata__intent_category=intent)

    total_count = EmailRecord.objects.count()
    priority_count = EmailRecord.objects.filter(metadata__priority_flag=True).count()
    pending_reply_count = EmailRecord.objects.filter(
        generated_reply__isnull=False, generated_reply__approved=False
    ).count()
    unprocessed_count = EmailRecord.objects.filter(metadata__isnull=True).count()

    from core.models import EmailMetadata

    context = {
        "emails": emails,
        "priority_only": priority_only,
        "selected_intent": intent,
        "intent_choices": EmailMetadata.IntentCategory.choices,
        "total_count": total_count,
        "priority_count": priority_count,
        "pending_reply_count": pending_reply_count,
        "unprocessed_count": unprocessed_count,
    }

    return render(request, "frontend/dashboard.html", context)


@login_required
def email_detail(request, pk):
    email = get_object_or_404(
        EmailRecord.objects.select_related("metadata", "generated_reply").prefetch_related(
            "action_items"
        ),
        pk=pk,
    )
    return render(request, "frontend/email_detail.html", {"email": email})


@login_required
@require_POST
def toggle_action_item(request, pk):
    """JS fetch() target: flips is_completed and returns the new state as JSON."""
    item = get_object_or_404(ActionItem, pk=pk)
    item.is_completed = not item.is_completed
    item.save(update_fields=["is_completed"])
    return JsonResponse({"id": item.id, "is_completed": item.is_completed})


@login_required
@require_POST
def update_reply(request, email_id):
    """
    JS fetch() target: saves edits to the draft reply body and/or flips
    the approved flag. Accepts JSON body: {draft_body, approved}.
    If approved is True, dispatches the email via SMTP.
    """
    email = get_object_or_404(EmailRecord, pk=email_id)
    reply = getattr(email, "generated_reply", None)
    
    if reply is None:
        return JsonResponse({"error": "No generated reply exists for this email yet."}, status=404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Malformed JSON body."}, status=400)

    update_fields = []

    if "draft_body" in payload:
        reply.draft_body = payload["draft_body"]
        update_fields.append("draft_body")
        
    if "approved" in payload:
        is_approved = bool(payload["approved"])
        reply.approved = is_approved
        update_fields.append("approved")

        # Trigger outbound SMTP dispatch if approved and not previously sent
        if is_approved and not reply.is_sent:
            try:
                msg = EmailMessage(
                    subject=f"Re: {email.subject}",
                    body=reply.draft_body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[email.sender_email],
                )
                msg.send(fail_silently=False)
                
                # Update tracking fields
                reply.is_sent = True
                reply.sent_at = timezone.now()
                update_fields.extend(["is_sent", "sent_at"])
                
            except Exception as e:
                logger.error(f"Failed to dispatch email {email.id} via SMTP: {e}")
                return JsonResponse({"error": "Failed to send email via SMTP."}, status=500)

    if update_fields:
        reply.save(update_fields=update_fields)

    return JsonResponse(
        {
            "id": reply.id, 
            "draft_body": reply.draft_body, 
            "approved": reply.approved,
            "is_sent": reply.is_sent
        }
    )

def login_redirect_root(request):
    return redirect("dashboard")