from django.db import migrations


def classify_full_scans(apps, schema_editor):
    MetroScrapeJob = apps.get_model("comparator", "MetroScrapeJob")
    MetroScrapeJob.objects.filter(
        status="COMPLETED",
        total_queries__gt=0,
        completed_queries__gt=0,
        scan_type="MANUAL",
    ).update(scan_type="FULL")


class Migration(migrations.Migration):

    dependencies = [
        ("comparator", "0020_metro_automation_and_anomalies"),
    ]

    operations = [
        migrations.RunPython(classify_full_scans, migrations.RunPython.noop),
    ]
