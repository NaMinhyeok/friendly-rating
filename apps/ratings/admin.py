from django.contrib import admin

from .models import Participant, PushDevice, RelationshipScore, ScoreChange


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("display_name", "slot", "user")
    ordering = ("slot",)


@admin.register(RelationshipScore)
class RelationshipScoreAdmin(admin.ModelAdmin):
    list_display = (
        "source_participant",
        "target_participant",
        "current_score",
        "updated_at",
    )
    readonly_fields = (
        "source_participant",
        "target_participant",
        "current_score",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ScoreChange)
class ScoreChangeAdmin(admin.ModelAdmin):
    list_display = (
        "changed_by",
        "delta",
        "resulting_score",
        "reason",
        "created_at",
    )
    readonly_fields = (
        "relationship_score",
        "changed_by",
        "delta",
        "reason",
        "resulting_score",
        "created_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ("participant", "is_active", "updated_at", "created_at")
    list_filter = ("is_active", "participant")
    search_fields = ("participant__display_name", "firebase_installation_id")
    readonly_fields = (
        "participant",
        "firebase_installation_id",
        "user_agent",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False
