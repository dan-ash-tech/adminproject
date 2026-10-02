from django.db import models
from django.db.models import F, Q
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import router, transaction
from django.conf import settings


class CaseRecordQuerySet(models.QuerySet):
	@staticmethod
	def _reprovisioned_after_termination():
		return (
			Q(reprovisioned_date__isnull=False)
			& (
				Q(first_termination_date__isnull=True)
				| Q(reprovisioned_date__gte=F("first_termination_date"))
			)
			& (
				Q(second_termination_date__isnull=True)
				| Q(reprovisioned_date__gte=F("second_termination_date"))
			)
		)

	def current_monitored(self):
		return self.filter(
			Q(
				first_termination_date__isnull=True,
				second_termination_date__isnull=True,
			)
			| self._reprovisioned_after_termination()
		)

	def stopped(self):
		return self.filter(
			Q(first_termination_date__isnull=False)
			| Q(second_termination_date__isnull=False)
		).exclude(self._reprovisioned_after_termination())

	def delete(self):
		using = self.db
		with transaction.atomic(using=using):
			result = super().delete()
			renumber_case_records(using)
			return result


class CaseRecord(models.Model):
	objects = CaseRecordQuerySet.as_manager()

	serial_number = models.CharField(
		max_length=50,
		blank=True,
		default="",
		unique=True,
		verbose_name="S/N",
	)
	code = models.CharField(
		max_length=50,
		unique=True,
		verbose_name="Call-sign",
	)
	assigned_user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		related_name="assigned_cases",
		blank=True,
		null=True,
		verbose_name="Assigned user",
	)
	first_name = models.CharField(max_length=150)
	last_name = models.CharField(max_length=150)
	authorizer = models.CharField(max_length=150)
	case_type = models.TextField()
	personal_identifications = models.ImageField(
		upload_to="personal_identifications/",
		verbose_name="Image",
	)
	attached_document = models.FileField(
		upload_to="case_documents/",
		blank=True,
		verbose_name="Report",
		validators=[FileExtensionValidator(["doc", "docx"])],
	)
	start_date = models.DateField()
	first_termination_date = models.DateField(blank=True, null=True)
	reprovisioned_date = models.DateField(
		blank=True,
		null=True,
		verbose_name="Reprovisioned on",
	)
	reprovisioned = models.BooleanField(
		default=False,
		verbose_name="Case reprovisioned",
	)
	second_termination_date = models.DateField(blank=True, null=True)

	class Meta:
		ordering = ["pk"]

	def save(self, *args, **kwargs):
		using = kwargs.get("using") or self._state.db or router.db_for_write(
			type(self), instance=self
		)
		with transaction.atomic(using=using):
			if self._state.adding:
				self.serial_number = str(
					type(self).objects.using(using).count() + 1
				)
			super().save(*args, **kwargs)

	def delete(self, *args, **kwargs):
		using = kwargs.get("using") or self._state.db or router.db_for_write(
			type(self), instance=self
		)
		with transaction.atomic(using=using):
			result = super().delete(*args, **kwargs)
			renumber_case_records(using)
			return result

	def __str__(self):
		return f"{self.code} - {self.last_name}, {self.first_name}"

	@property
	def monitoring_status(self):
		termination_dates = [
			termination_date
			for termination_date in (
				self.first_termination_date,
				self.second_termination_date,
			)
			if termination_date is not None
		]
		if not termination_dates or (
			self.reprovisioned_date
			and self.reprovisioned_date >= max(termination_dates)
		):
			return "Current monitored"
		return "Stopped from the system"


def renumber_case_records(using):
	records = list(
		CaseRecord.objects.using(using).order_by("pk").only("pk", "serial_number")
	)
	for record in records:
		record.serial_number = f"__renumber__{record.pk}"
	CaseRecord.objects.using(using).bulk_update(records, ["serial_number"])

	for number, record in enumerate(records, start=1):
		record.serial_number = str(number)
	CaseRecord.objects.using(using).bulk_update(records, ["serial_number"])


class CasePhoneNumber(models.Model):
	case_record = models.ForeignKey(
		CaseRecord,
		on_delete=models.CASCADE,
		related_name="phone_numbers",
	)
	label = models.CharField(max_length=50, blank=True)
	number = models.CharField(max_length=100)
	reprovisioned = models.BooleanField(
		default=False,
		verbose_name="Target reprovisioned",
	)
	reprovisioned_date = models.DateField(
		blank=True,
		null=True,
		verbose_name="Target reprovisioned on (legacy)",
	)

	class Meta:
		ordering = ["id"]
		constraints = [
			models.UniqueConstraint(
				fields=("case_record", "number"),
				name="unique_target_number_per_case",
			),
		]

	def __str__(self):
		return f"{self.label}: {self.number}" if self.label else self.number
