from django.db import migrations


def compact_serial_numbers(apps, schema_editor):
	database = schema_editor.connection.alias
	CaseRecord = apps.get_model("Adminbackup", "CaseRecord")
	record_ids = list(
		CaseRecord.objects.using(database).order_by("pk").values_list(
			"pk", flat=True
		)
	)

	for record_id in record_ids:
		CaseRecord.objects.using(database).filter(pk=record_id).update(
			serial_number=f"__renumber__{record_id}"
		)

	for number, record_id in enumerate(record_ids, start=1):
		CaseRecord.objects.using(database).filter(pk=record_id).update(
			serial_number=str(number)
		)


class Migration(migrations.Migration):

	dependencies = [
		("Adminbackup", "0011_auto_target_serial_numbers"),
	]

	operations = [
		migrations.RunPython(compact_serial_numbers, migrations.RunPython.noop),
	]
