from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet

from .models import CasePhoneNumber, CaseRecord


USER_ROLE_CHOICES = (
	("viewer", "Read-only viewer"),
	("view_all_records", "View all records only"),
	("target_editor", "Target editor"),
)


class ViewerUserCreationForm(UserCreationForm):
	role = forms.ChoiceField(
		choices=USER_ROLE_CHOICES,
		label="Permissions",
		help_text=(
			"Read-only viewers see assigned targets. View-all users can view "
			"all targets without editing. Target editors can manage target records."
		),
	)

	class Meta(UserCreationForm.Meta):
		model = get_user_model()
		fields = ("username",)


class ManagedUserForm(forms.ModelForm):
	role = forms.ChoiceField(
		choices=USER_ROLE_CHOICES,
		label="Permissions",
		help_text=(
			"Read-only viewers see assigned targets. View-all users can view "
			"all targets without editing. Target editors can manage target records."
		),
	)

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if self.instance and self.instance.pk:
			if not self.instance.is_staff:
				role = "viewer"
			elif self.instance.has_perm("Adminbackup.change_caserecord"):
				role = "target_editor"
			else:
				role = "view_all_records"
			self.fields["role"].initial = role

	class Meta:
		model = get_user_model()
		fields = ("username", "first_name", "last_name", "email", "is_active")
		widgets = {
			"username": forms.TextInput(attrs={"autocomplete": "username"}),
			"email": forms.EmailInput(attrs={"autocomplete": "email"}),
		}


class AdminPasswordResetForm(SetPasswordForm):
	pass


class CaseRecordIntakeForm(forms.ModelForm):
	def __init__(self, *args, can_assign=False, **kwargs):
		super().__init__(*args, **kwargs)
		if not can_assign:
			self.fields.pop("assigned_user")
		else:
			self.fields["assigned_user"].queryset = get_user_model().objects.filter(
				is_active=True,
				is_superuser=False,
			)

	class Meta:
		model = CaseRecord
		fields = (
			"code",
			"assigned_user",
			"first_name",
			"last_name",
			"authorizer",
			"case_type",
			"personal_identifications",
			"attached_document",
			"start_date",
			"first_termination_date",
			"reprovisioned_date",
			"second_termination_date",
		)
		widgets = {
			"case_type": forms.Textarea(attrs={"rows": 5}),
			"personal_identifications": forms.ClearableFileInput(
				attrs={"accept": "image/*"}
			),
			"start_date": forms.DateInput(attrs={"type": "date"}),
			"first_termination_date": forms.DateInput(attrs={"type": "date"}),
			"reprovisioned_date": forms.DateInput(attrs={"type": "date"}),
			"second_termination_date": forms.DateInput(attrs={"type": "date"}),
		}


class CasePhoneNumberInlineFormSet(BaseInlineFormSet):
	def clean(self):
		super().clean()
		seen_numbers = set()

		for form in self.forms:
			if not hasattr(form, "cleaned_data") or form.errors:
				continue
			if form.cleaned_data.get("DELETE"):
				continue

			number = form.cleaned_data.get("number")
			if not number:
				continue
			normalized_number = "".join(
				character for character in number if character.isdigit()
			)
			if normalized_number in seen_numbers:
				raise ValidationError(
					"A target number can only be entered once per target."
				)
			seen_numbers.add(normalized_number)


CasePhoneNumberFormSet = inlineformset_factory(
	CaseRecord,
	CasePhoneNumber,
	fields=("label", "number"),
	formset=CasePhoneNumberInlineFormSet,
	can_delete=False,
	extra=1,
)