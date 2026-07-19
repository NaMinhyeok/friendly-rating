from django.db import migrations


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("ratings", "0005_rename_relationship_and_push_fields"),
    ]

    # Match the application's RelationshipScore -> Participant lock order so a
    # concurrent score write cannot deadlock with this atomic migration. The
    # reverse order is only safe while application writes are stopped.
    operations = [
        migrations.AlterModelTable(
            name="relationshipscore",
            table="relationship_score",
        ),
        migrations.AlterModelTable(
            name="participant",
            table="participant",
        ),
        migrations.AlterModelTable(
            name="pushdevice",
            table="push_device",
        ),
        migrations.AlterModelTable(
            name="scorechange",
            table="score_change",
        ),
    ]
