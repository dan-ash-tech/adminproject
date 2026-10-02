from django import forms
from django.contrib import admin
from django.db import models
from django.urls import reverse
from django.utils.html import format_html
from .forms import CasePhoneNumberInlineFormSet
from .models import CasePhoneNumber, CaseRecord


class CaseMonitoringFilter(admin.SimpleListFilter):
	title = "monitoring status"
	parameter_name = "monitoring_status"
	template = "admin/monitoring_status_filter.html"

	def lookups(self, request, model_admin):
		return (
			("current", "Current monitored"),
			("stopped", "Stopped from the system"),
		)

	def queryset(self, request, queryset):
		if self.value() == "current":
			return queryset.current_monitored()
		if self.value() == "stopped":
			return queryset.stopped()
		return queryset


class CasePhoneNumberInline(admin.TabularInline):
	model = CasePhoneNumber
	formset = CasePhoneNumberInlineFormSet
	fields = ("label", "number")
	can_delete = True
	extra = 1


@admin.register(CaseRecord)
class CaseRecordAdmin(admin.ModelAdmin):
	formfield_overrides = {
		models.TextField: {"widget": forms.Textarea(attrs={"rows": 5})},
	}
	list_display = (
		"serial_number",
		"code",
		"assigned_user",
		"first_name",
		"last_name",
		"monitoring_status",
		"personal_identification_link",
		"phone_numbers_display",
		"authorizer",
		"case_type",
		"personal_identifications",
		"attached_document",
		"start_date",
		"first_termination_date",
		"reprovisioned_date",
		"second_termination_date",
	)
	list_filter = (
		CaseMonitoringFilter,
		"case_type",
		"start_date",
	)
	list_editable = (
		"first_termination_date",
		"reprovisioned_date",
		"second_termination_date",
	)
	search_fields = (
		"serial_number",
		"code",
		"assigned_user__username",
		"first_name",
		"last_name",
		"phone_numbers__number",
		"phone_numbers__label",
		"authorizer",
	)
	inlines = (CasePhoneNumberInline,)
	readonly_fields = ("serial_number", "personal_identification_preview")
	exclude = ("reprovisioned",)

	def get_queryset(self, request):
		return super().get_queryset(request).prefetch_related("phone_numbers")

	def get_readonly_fields(self, request, obj=None):
		readonly_fields = list(super().get_readonly_fields(request, obj))
		if not request.user.is_superuser:
			readonly_fields.append("assigned_user")
		return tuple(readonly_fields)

	@admin.display(description="Targets and reprovisioning")
	def phone_numbers_display(self, obj):
		return ", ".join(
			f"{phone.label}: {phone.number}" if phone.label else phone.number
			for phone in obj.phone_numbers.all()
		)
  
	@admin.display(description="Monitoring status")
	def monitoring_status(self, obj):
		return obj.monitoring_status

	def _personal_identification_url(self, obj):
		filename = obj.personal_identifications.name.removeprefix(
			"personal_identifications/"
		)
		return reverse(
			"private-identification-media",
			kwargs={"filename": filename},
		)

	@admin.display(description="Personal identification")
	def personal_identification_link(self, obj):
		if not obj.personal_identifications:
			return "Not uploaded"
		return format_html(
			'<a href="{}" target="_blank" rel="noopener">View image</a>',
			self._personal_identification_url(obj),
		)

	@admin.display(description="Identification preview")
	def personal_identification_preview(self, obj):
		if not obj or not obj.personal_identifications:
			return "No image uploaded"
		url = self._personal_identification_url(obj)
		return format_html(
			'<a href="{}" target="_blank" rel="noopener">'
			'<img src="{}" alt="Personal identification" '
			'style="max-width: 280px; max-height: 200px;">'
			"</a>",
			url,
			url,
		)
