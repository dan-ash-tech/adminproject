from django.db import migrations, models
import django.core.validators


def populate_telephone_numbers(apps, schema_editor):
	database = schema_editor.connection.alias
	CaseRecord = apps.get_model("Adminbackup", "CaseRecord")
	CasePhoneNumber = apps.get_model("Adminbackup", "CasePhoneNumber")

	for record in CaseRecord.objects.using(database).iterator():
		first_number = (
			CasePhoneNumber.objects.using(database)
			.filter(case_record_id=record.pk)
			.order_by("pk")
			.values_list("number", flat=True)
			.first()
		)
		if first_number:
			record.telephone_number = first_number
			record.save(using=database, update_fields=["telephone_number"])


class Migration(migrations.Migration):

	dependencies = [
		("Adminbackup", "0009_caserecord_assigned_user"),
	]

	operations = [
		migrations.AddField(
			model_name="caserecord",
			name="serial_number",
			field=models.CharField(
				blank=True,
				default="",
				max_length=50,
				verbose_name="S/N",
			),
		),
		migrations.AddField(
			model_name="caserecord",
			name="telephone_number",
			field=models.CharField(
				blank=True,
				max_length=100,
				verbose_name="Telephone number",
			),
		),
		migrations.AlterField(
			model_name="caserecord",
			name="code",
			field=models.CharField(
				max_length=50,
				unique=True,
				verbose_name="Call-sign",
			),
		),
		migrations.AlterField(
			model_name="caserecord",
			name="personal_identifications",
			field=models.ImageField(
				upload_to="personal_identifications/",
				verbose_name="Image",
			),
		),
		migrations.AlterField(
			model_name="caserecord",
			name="attached_document",
			field=models.FileField(
				blank=True,
				upload_to="case_documents/",
				validators=[
					django.core.validators.FileExtensionValidator(["doc", "docx"])
				],
				verbose_name="Report",
			),
		),
		migrations.RunPython(populate_telephone_numbers, migrations.RunPython.noop),
	]
