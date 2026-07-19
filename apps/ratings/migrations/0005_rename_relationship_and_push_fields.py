from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ratings", "0004_pushdevice"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="relationshipscore",
            name="relationship_score_between_0_and_100",
        ),
        migrations.RemoveConstraint(
            model_name="relationshipscore",
            name="relationship_score_between_different_participants",
        ),
        migrations.RenameField(
            model_name="relationshipscore",
            old_name="rater",
            new_name="source_participant",
        ),
        migrations.RenameField(
            model_name="relationshipscore",
            old_name="recipient",
            new_name="target_participant",
        ),
        migrations.RenameField(
            model_name="relationshipscore",
            old_name="value",
            new_name="current_score",
        ),
        migrations.RenameField(
            model_name="scorechange",
            old_name="score",
            new_name="relationship_score",
        ),
        migrations.RenameField(
            model_name="pushdevice",
            old_name="fid",
            new_name="firebase_installation_id",
        ),
        migrations.RenameField(
            model_name="pushdevice",
            old_name="active",
            new_name="is_active",
        ),
        migrations.AddConstraint(
            model_name="relationshipscore",
            constraint=models.CheckConstraint(
                condition=models.Q(current_score__gte=0)
                & models.Q(current_score__lte=100),
                name="relationship_score_between_0_and_100",
            ),
        ),
        migrations.AddConstraint(
            model_name="relationshipscore",
            constraint=models.CheckConstraint(
                condition=~models.Q(source_participant=models.F("target_participant")),
                name="relationship_score_between_different_participants",
            ),
        ),
    ]
