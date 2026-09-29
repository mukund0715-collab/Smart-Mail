from django.contrib import admin

from .models import ActionItem, EmailMetadata, EmailRecord, GeneratedReply


class EmailMetadataInline(admin.StackedInline):
    model = EmailMetadata
    can_delete = False
    readonly_fields = ("deanonymization_map", "processed_at")


class ActionItemInline(admin.TabularInline):
    model = ActionItem
    extra = 0


class GeneratedReplyInline(admin.StackedInline):
    model = GeneratedReply
    can_delete = False


@admin.register(EmailRecord)
class EmailRecordAdmin(admin.ModelAdmin):
    list_display = ("message_id", "sender_email", "subject", "received_at", "priority")
    search_fields = ("message_id", "sender_email", "subject")
    list_filter = ("received_at",)
    inlines = [EmailMetadataInline, ActionItemInline, GeneratedReplyInline]

    @admin.display(boolean=True, description="Priority")
    def priority(self, obj):
        return getattr(getattr(obj, "metadata", None), "priority_flag", False)


@admin.register(ActionItem)
class ActionItemAdmin(admin.ModelAdmin):
    list_display = ("task_description", "email", "deadline", "is_completed")
    list_filter = ("is_completed",)
