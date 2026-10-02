from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("Adminbackup", "0007_casephonenumber_unique_target_number_per_case"),
	]

	operations = [
		migrations.AlterField(
			model_name="caserecord",
			name="case_type",
			field=models.TextField(),
		),
	]