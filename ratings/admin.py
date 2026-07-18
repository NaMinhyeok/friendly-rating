from django.contrib import admin

from .models import Participant, PushDevice, RelationshipScore, ScoreChange


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("display_name", "slot", "user")
    ordering = ("slot",)


@admin.register(RelationshipScore)
class RelationshipScoreAdmin(admin.ModelAdmin):
    list_display = ("rater", "recipient", "value", "updated_at")
    readonly_fields = ("rater", "recipient", "value", "updated_at")

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
        "score",
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
    list_display = ("participant", "active", "updated_at", "created_at")
    list_filter = ("active", "participant")
    search_fields = ("participant__display_name", "fid")
    readonly_fields = ("participant", "fid", "user_agent", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False
