from datetime import date
from io import BytesIO
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .admin import (
	CaseMonitoringFilter,
	CasePhoneNumberInline,
	CaseRecordAdmin,
)
from .forms import (
	CasePhoneNumberFormSet,
	CaseRecordIntakeForm,
)
from .models import CasePhoneNumber, CaseRecord


class CaseMonitoringFilterTests(TestCase):
	def setUp(self):
		self.current_case = self.create_case("CURRENT")
		self.reprovisioned_target = CasePhoneNumber.objects.create(
			case_record=self.current_case,
			label="SIM",
			number="5550101",
			reprovisioned_date=date(2026, 4, 1),
		)
		self.other_target = CasePhoneNumber.objects.create(
			case_record=self.current_case,
			label="Voice",
			number="5550102",
			reprovisioned=False,
		)
		self.first_terminated_case = self.create_case(
			"FIRST-TERM", first_termination_date=date(2026, 1, 10)
		)
		self.second_terminated_case = self.create_case(
			"SECOND-TERM", second_termination_date=date(2026, 2, 10)
		)
		self.reprovisioned_after_termination_case = self.create_case(
			"REPROVISIONED",
			first_termination_date=date(2026, 3, 10),
			reprovisioned_date=date(2026, 4, 2),
		)
		self.reterminated_after_reprovision_case = self.create_case(
			"RETERMINATED",
			first_termination_date=date(2026, 3, 10),
			reprovisioned_date=date(2026, 4, 2),
			second_termination_date=date(2026, 5, 10),
		)

	def create_case(self, code, **dates):
		return CaseRecord.objects.create(
			code=code,
			first_name="Test",
			last_name="Record",
			authorizer="Test Authorizer",
			case_type="Test",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 1),
			**dates,
		)

	def filter_codes(self, status):
		request = RequestFactory().get(
			"/admin/Adminbackup/caserecord/",
			{"monitoring_status": status},
		)
		case_admin = CaseRecordAdmin(CaseRecord, admin.site)
		status_filter = CaseMonitoringFilter(
			request,
			request.GET.copy(),
			CaseRecord,
			case_admin,
		)
		return set(
			status_filter.queryset(request, CaseRecord.objects.all()).values_list(
				"code", flat=True
			)
		)

	def test_reprovisioned_case_returns_to_current_monitoring(self):
		self.assertEqual(
			self.filter_codes("current"),
			{
				self.current_case.code,
				self.reprovisioned_after_termination_case.code,
			},
		)

	def test_stopped_filter_includes_either_termination_date(self):
		self.assertEqual(
			self.filter_codes("stopped"),
			{
				self.first_terminated_case.code,
				self.second_terminated_case.code,
				self.reterminated_after_reprovision_case.code,
			},
		)

	def test_reprovisioned_case_is_not_stopped(self):
		self.assertNotIn(
			self.reprovisioned_after_termination_case.code,
			self.filter_codes("stopped"),
		)

	def test_reprovisioned_case_displays_as_current(self):
		case_admin = CaseRecordAdmin(CaseRecord, admin.site)

		self.assertEqual(
			case_admin.monitoring_status(
				self.reprovisioned_after_termination_case
			),
			"Current monitored",
		)

	def test_case_type_is_in_admin_list_display(self):
		case_admin = CaseRecordAdmin(CaseRecord, admin.site)

		self.assertIn("case_type", case_admin.get_list_display(None))

	def test_target_reprovision_date_does_not_change_case_status(self):
		CasePhoneNumber.objects.create(
			case_record=self.first_terminated_case,
			label="SIM",
			number="5550199",
			reprovisioned_date=date(2026, 4, 1),
		)
		self.assertIn(
			self.first_terminated_case.code,
			self.filter_codes("stopped"),
		)

	def test_deleting_one_inline_number_keeps_case_and_sibling_number(self):
		request = RequestFactory().get("/admin/Adminbackup/caserecord/")
		request.user = get_user_model().objects.create_superuser(
			username="inline-admin",
			password="valid-test-password",
		)
		inline = CasePhoneNumberInline(CaseRecord, admin.site)
		formset_class = inline.get_formset(request, self.current_case)
		initial_formset = formset_class(instance=self.current_case)
		post_data = {
			f"{initial_formset.prefix}-TOTAL_FORMS": str(
				initial_formset.total_form_count()
			),
			f"{initial_formset.prefix}-INITIAL_FORMS": str(
				initial_formset.initial_form_count()
			),
			f"{initial_formset.prefix}-MIN_NUM_FORMS": str(
				initial_formset.min_num
			),
			f"{initial_formset.prefix}-MAX_NUM_FORMS": str(
				initial_formset.max_num
			),
		}
		for index, form in enumerate(initial_formset.forms):
			for field_name in form.fields:
				key = f"{initial_formset.prefix}-{index}-{field_name}"
				if field_name == "DELETE":
					post_data[key] = (
						"on"
						if form.instance.pk == self.reprovisioned_target.pk
						else ""
					)
				else:
					post_data[key] = form[field_name].value() or ""

		formset = formset_class(post_data, instance=self.current_case)
		self.assertTrue(formset.is_valid(), formset.errors)
		formset.save()

		self.assertTrue(CaseRecord.objects.filter(pk=self.current_case.pk).exists())
		self.assertFalse(
			CasePhoneNumber.objects.filter(pk=self.reprovisioned_target.pk).exists()
		)
		self.assertTrue(
			CasePhoneNumber.objects.filter(pk=self.other_target.pk).exists()
		)


class ClientCaseIntakeTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			username="data-entry",
			password="valid-test-password",
		)

	def test_regular_user_cannot_submit_case(self):
		self.client.force_login(self.user)
		response = self.client.post(
			reverse("submit-case"),
		)

		self.assertFalse(self.user.is_staff)
		self.assertEqual(response.status_code, 403)
		self.assertFalse(CaseRecord.objects.exists())

	def test_case_type_accepts_long_text_and_uses_textarea(self):
		long_case_type = "Detailed case type " * 40
		case = CaseRecord.objects.create(
			code="LONG-TYPE",
			first_name="Long",
			last_name="Text",
			authorizer="Office",
			case_type=long_case_type,
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 5),
		)
		case.refresh_from_db()

		self.assertEqual(case.case_type, long_case_type)
		self.assertIsInstance(
			CaseRecordIntakeForm().fields["case_type"].widget,
			forms.Textarea,
		)
		self.assertNotIn("telephone_number", CaseRecordIntakeForm().fields)
		self.assertIn("number", CasePhoneNumberFormSet().forms[0].fields)
		self.assertIn("label", CasePhoneNumberFormSet().forms[0].fields)

	def test_serial_number_is_generated_and_increases_for_new_targets(self):
		first = CaseRecord.objects.create(
			code="AUTO-SN-1",
			first_name="First",
			last_name="Target",
			authorizer="Office",
			case_type="Test",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 1),
		)
		second = CaseRecord.objects.create(
			code="AUTO-SN-2",
			first_name="Second",
			last_name="Target",
			authorizer="Office",
			case_type="Test",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 1),
		)

		self.assertEqual(first.serial_number, "1")
		self.assertEqual(second.serial_number, "2")
		self.assertEqual(
			list(CaseRecord.objects.values_list("serial_number", flat=True)),
			[first.serial_number, second.serial_number],
		)
		self.assertNotIn("serial_number", CaseRecordIntakeForm().fields)

	def test_deleting_target_compacts_serial_numbers_and_next_target_follows(self):
		records = [
			CaseRecord.objects.create(
				code=f"AUTO-DELETE-{number}",
				first_name="Target",
				last_name=str(number),
				authorizer="Office",
				case_type="Test",
				personal_identifications="personal_identifications/test.png",
				start_date=date(2026, 1, 1),
			)
			for number in range(1, 4)
		]

		records[1].delete()
		remaining = list(CaseRecord.objects.order_by("pk"))
		self.assertEqual(
			[(record.code, record.serial_number) for record in remaining],
			[("AUTO-DELETE-1", "1"), ("AUTO-DELETE-3", "2")],
		)

		CaseRecord.objects.filter(code="AUTO-DELETE-3").delete()
		new_record = CaseRecord.objects.create(
			code="AUTO-DELETE-4",
			first_name="New",
			last_name="Target",
			authorizer="Office",
			case_type="Test",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 1),
		)
		self.assertEqual(new_record.serial_number, "2")
		self.assertEqual(
			list(CaseRecord.objects.values_list("serial_number", flat=True)),
			["1", "2"],
		)

	def test_only_superusers_can_assign_users_in_the_intake_form(self):
		viewer_form = CaseRecordIntakeForm()
		admin_form = CaseRecordIntakeForm(can_assign=True)
		admin = get_user_model().objects.create_superuser(
			username="excluded-admin",
			password="test-password-123",
		)
		ordinary_user = get_user_model().objects.create_user(
			username="assignable-viewer",
			password="test-password-123",
		)

		self.assertNotIn("assigned_user", viewer_form.fields)
		self.assertIn("assigned_user", admin_form.fields)
		self.assertNotIn(admin, admin_form.fields["assigned_user"].queryset)
		self.assertIn(ordinary_user, admin_form.fields["assigned_user"].queryset)

	def test_submission_confirmation_links_back_to_dashboard(self):
		staff_user = get_user_model().objects.create_user(
			username="case-submitter",
			password="valid-test-password",
			is_staff=True,
		)
		add_case_permission = Permission.objects.get(
			content_type__app_label="Adminbackup",
			codename="add_caserecord",
		)
		staff_user.user_permissions.add(add_case_permission)
		self.client.force_login(staff_user)

		response = self.client.get(reverse("case-submission-complete"))

		self.assertContains(response, "Record submitted")
		self.assertContains(
			response,
			f'href="{reverse("dashboard")}">Back to dashboard</a>',
		)

	def test_duplicate_targets_with_different_spacing_are_rejected(self):
		formset = CasePhoneNumberFormSet(
			{
				"phone_numbers-TOTAL_FORMS": "2",
				"phone_numbers-INITIAL_FORMS": "0",
				"phone_numbers-MIN_NUM_FORMS": "0",
				"phone_numbers-MAX_NUM_FORMS": "1000",
				"phone_numbers-0-label": "First entry",
				"phone_numbers-0-number": "0780000001",
				"phone_numbers-1-label": "Repeated entry",
				"phone_numbers-1-number": "078 000 0001",
			},
			instance=CaseRecord(),
			prefix="phone_numbers",
		)

		self.assertFalse(formset.is_valid())
		self.assertIn(
			"A target number can only be entered once per target.",
			formset.non_form_errors(),
		)


class ClientDashboardTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			username="viewer",
			password="valid-test-password",
		)
		self.current_case = self.create_case("CURRENT-001")
		CasePhoneNumber.objects.create(
			case_record=self.current_case,
			label="Work",
			number="0780000001",
		)
		CasePhoneNumber.objects.create(
			case_record=self.current_case,
			label="Personal",
			number="0780000002",
		)
		self.stopped_case = self.create_case(
			"STOPPED-001",
			first_termination_date=date(2026, 2, 1),
		)
		CasePhoneNumber.objects.create(
			case_record=self.stopped_case,
			label="Personal",
			number="0780000002",
		)

	def create_case(self, code, **dates):
		assigned_user = dates.pop("assigned_user", self.user)
		return CaseRecord.objects.create(
			code=code,
			assigned_user=assigned_user,
			first_name="Example",
			last_name="Client",
			authorizer="Office",
			case_type="Personal",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 5),
			**dates,
		)

	def make_docx_report(self):
		document_xml = (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			'<w:document xmlns:w="http://schemas.openxmlformats.org/'
			'wordprocessingml/2006/main"><w:body>'
			'<w:p><w:r><w:t>Confidential report text</w:t></w:r></w:p>'
			'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table detail</w:t></w:r>'
			'</w:p></w:tc></w:tr></w:tbl>'
			'</w:body></w:document>'
		)
		report = BytesIO()
		with ZipFile(report, "w") as archive:
			archive.writestr("word/document.xml", document_xml)
		return report.getvalue()

	def test_anonymous_user_is_redirected_to_login(self):
		response = self.client.get(reverse("dashboard"))

		self.assertRedirects(
			response,
			f"{reverse('login')}?next={reverse('dashboard')}",
		)

	def test_root_url_redirects_to_login(self):
		response = self.client.get("/")

		self.assertRedirects(response, reverse("login"))

	def test_account_url_redirects_to_dashboard(self):
		self.client.force_login(self.user)
		response = self.client.get("/account", follow=True)

		self.assertEqual(
			response.redirect_chain,
			[
				(reverse("account"), 301),
				(reverse("dashboard"), 302),
			],
		)
		self.assertEqual(response.status_code, 200)

	def test_login_redirects_to_read_only_dashboard(self):
		response = self.client.post(
			reverse("login"),
			{"username": "viewer", "password": "valid-test-password"},
		)

		self.assertRedirects(response, reverse("dashboard"))

	def test_viewer_sees_grouped_phone_numbers_but_not_stopped_records(self):
		self.client.force_login(self.user)
		self.current_case.case_type = "Personal investigation"
		self.current_case.save(update_fields=["case_type"])
		response = self.client.get(reverse("dashboard"))

		self.assertContains(response, self.current_case.code)
		self.assertContains(response, "Case type")
		self.assertContains(response, "Personal investigation")
		self.assertNotContains(response, "Current monitored targets")
		self.assertNotContains(response, "Stopped targets")
		self.assertContains(response, "Target Monitoring")
		self.assertContains(response, "0780000001")
		self.assertContains(response, "0780000002")
		self.assertContains(response, "Phone numbers")
		self.assertEqual(response.content.decode().count("0780000001"), 1)
		self.assertNotContains(response, "data-copy=")
		self.assertEqual(response.content.decode().count("CURRENT-001"), 1)
		self.assertNotContains(response, self.stopped_case.code)
		self.assertNotContains(response, "Submit client record")
		edit_url = reverse(
			"admin:Adminbackup_caserecord_change",
			args=[self.current_case.pk],
		)
		self.assertNotContains(response, edit_url)

	def test_viewer_can_open_stopped_targets(self):
		self.client.force_login(self.user)
		response = self.client.get(reverse("dashboard"), {"status": "stopped"})

		self.assertContains(response, self.stopped_case.code)
		self.assertContains(response, "0780000002")
		self.assertNotContains(response, self.current_case.code)

	def test_viewer_only_sees_targets_assigned_to_their_account(self):
		other_user = get_user_model().objects.create_user(
			username="other-viewer",
			password="test-password",
		)
		other_case = self.create_case(
			"OTHER-USER",
			assigned_user=other_user,
		)
		unassigned_case = CaseRecord.objects.create(
			code="UNASSIGNED",
			first_name="Unassigned",
			last_name="Target",
			authorizer="Office",
			case_type="Personal",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 5),
		)
		self.client.force_login(self.user)

		response = self.client.get(reverse("dashboard"))

		self.assertContains(response, self.current_case.code)
		self.assertNotContains(response, other_case.code)
		self.assertNotContains(response, unassigned_case.code)

	def test_viewer_dashboard_uses_their_custom_statement(self):
		self.client.force_login(self.user)

		with patch.dict(
			"Adminbackup.views.USER_DASHBOARD_TEXT",
			{
				"viewer": {
					"title": "Viewer-specific heading",
					"message": "Welcome, viewer. Your statement is private.",
				}
			},
			clear=True,
		):
			response = self.client.get(reverse("dashboard"))

		self.assertContains(
			response,
			"Welcome, viewer. Your statement is private.",
		)
		self.assertContains(response, "Viewer-specific heading")
		self.assertNotContains(response, "Target Monitoring")

	def test_unlisted_user_gets_default_dashboard_text(self):
		self.client.force_login(self.user)

		response = self.client.get(reverse("dashboard"))

		self.assertContains(response, "Target Monitoring")
		self.assertContains(response, "View target records by monitoring status.")

	def test_superuser_sees_targets_assigned_to_others_and_unassigned(self):
		other_user = get_user_model().objects.create_user(
			username="other-viewer",
			password="valid-test-password",
		)
		other_case = self.create_case(
			"OTHER-USER",
			assigned_user=other_user,
		)
		unassigned_case = CaseRecord.objects.create(
			code="UNASSIGNED",
			first_name="Unassigned",
			last_name="Target",
			authorizer="Office",
			case_type="Personal",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 5),
		)
		admin_user = get_user_model().objects.create_superuser(
			username="admin-viewer",
			password="valid-test-password",
		)
		self.client.force_login(admin_user)

		response = self.client.get(reverse("dashboard"))

		self.assertContains(response, self.current_case.code)
		self.assertContains(response, other_case.code)
		self.assertContains(response, unassigned_case.code)
		self.assertContains(response, "Current monitored targets")
		self.assertContains(response, "<p>3</p>", html=True)
		self.assertContains(response, "Stopped targets")
		self.assertContains(response, "<p>1</p>", html=True)

		filtered_response = self.client.get(
			reverse("dashboard"),
			{"status": "current", "q": "UNASSIGNED"},
		)
		self.assertContains(filtered_response, "<p>3</p>", html=True)
		self.assertContains(filtered_response, "<p>1</p>", html=True)

	def test_view_all_records_user_sees_every_target_without_edit_controls(self):
		other_user = get_user_model().objects.create_user(
			username="other-viewer",
			password="valid-test-password",
		)
		other_case = self.create_case(
			"OTHER-USER",
			assigned_user=other_user,
		)
		unassigned_case = CaseRecord.objects.create(
			code="UNASSIGNED",
			first_name="Unassigned",
			last_name="Target",
			authorizer="Office",
			case_type="Personal",
			personal_identifications="personal_identifications/test.png",
			start_date=date(2026, 1, 5),
		)
		view_all_user = get_user_model().objects.create_user(
			username="view-all",
			password="valid-test-password",
			is_staff=True,
		)
		view_all_permission = Permission.objects.get(
			content_type__app_label="Adminbackup",
			codename="view_caserecord",
		)
		view_all_user.user_permissions.add(view_all_permission)
		self.client.force_login(view_all_user)

		response = self.client.get(reverse("dashboard"))

		self.assertContains(response, self.current_case.code)
		self.assertContains(response, other_case.code)
		self.assertContains(response, unassigned_case.code)
		self.assertNotContains(response, "<th scope=\"col\">Actions</th>")
		self.assertNotContains(response, "Current monitored targets")

		stopped_assigned_case = self.create_case(
			"OTHER-STOPPED",
			assigned_user=self.user,
			first_termination_date=date(2026, 2, 1),
		)
		stopped_response = self.client.get(
			reverse("dashboard"),
			{"status": "stopped"},
		)
		self.assertContains(stopped_response, stopped_assigned_case.code)

	def test_view_all_records_user_can_read_any_target_report(self):
		with TemporaryDirectory() as media_root, override_settings(
			MEDIA_ROOT=media_root
		):
			self.current_case.attached_document.save(
				"target-report.docx",
				ContentFile(self.make_docx_report()),
				save=True,
			)
			view_all_user = get_user_model().objects.create_user(
				username="view-all-report",
				password="valid-test-password",
				is_staff=True,
			)
			view_all_permission = Permission.objects.get(
				content_type__app_label="Adminbackup",
				codename="view_caserecord",
			)
			view_all_user.user_permissions.add(view_all_permission)
			self.client.force_login(view_all_user)

			response = self.client.get(
				reverse("private-target-report", args=[self.current_case.pk])
			)

			self.assertEqual(response.status_code, 200)
			self.assertContains(response, "Confidential report text")

	def test_dashboard_assignment_is_read_only_for_non_superuser_admins(self):
		case_admin = CaseRecordAdmin(CaseRecord, admin.site)
		request = RequestFactory().get("/admin/Adminbackup/caserecord/")
		request.user = get_user_model().objects.create_user(
			username="case-editor-readonly",
			password="test-password",
			is_staff=True,
		)

		self.assertIn(
			"assigned_user",
			case_admin.get_readonly_fields(request, self.current_case),
		)
		self.assertIn(
			"serial_number",
			case_admin.get_readonly_fields(request, self.current_case),
		)
		self.assertNotIn(
			"assigned_user",
			case_admin.get_form(request, self.current_case).base_fields,
		)

		request.user = get_user_model().objects.create_superuser(
			username="assignment-admin",
			password="test-password",
		)
		self.assertNotIn(
			"assigned_user",
			case_admin.get_readonly_fields(request, self.current_case),
		)
		self.assertIn(
			"serial_number",
			case_admin.get_readonly_fields(request, self.current_case),
		)
		self.assertIn(
			"assigned_user",
			case_admin.get_form(request, self.current_case).base_fields,
		)

	def test_dashboard_search_matches_name_code_phone_and_date(self):
		self.client.force_login(self.user)
		CasePhoneNumber.objects.create(
			case_record=self.current_case,
			label="Additional",
			number="078 111 2222",
		)
		self.current_case.authorizer = "Registry office"
		self.current_case.case_type = "Sensitive investigation"
		self.current_case.attached_document = "case_documents/annual-report.docx"
		self.current_case.save(
			update_fields=(
				"authorizer",
				"case_type",
				"attached_document",
			)
		)
		searches = (
			("Example", self.current_case.code),
			("CURRENT-001", self.current_case.code),
			(self.current_case.serial_number, self.current_case.code),
			("078 111 2222", self.current_case.code),
			("0780000001", self.current_case.code),
			("Registry office", self.current_case.code),
			("Sensitive investigation", self.current_case.code),
			("viewer", self.current_case.code),
			("test.png", self.current_case.code),
			("annual-report.docx", self.current_case.code),
			("2026-01-05", self.current_case.code),
		)

		for query, expected_code in searches:
			with self.subTest(query=query):
				response = self.client.get(reverse("dashboard"), {"q": query})
				self.assertContains(response, expected_code)
				self.assertNotContains(response, self.stopped_case.code)
				self.assertContains(response, f'value="{query}"')

	def test_dashboard_search_stays_within_selected_status(self):
		self.client.force_login(self.user)
		response = self.client.get(
			reverse("dashboard"),
			{"status": "stopped", "q": "0780000002"},
		)

		self.assertContains(response, self.stopped_case.code)
		self.assertNotContains(response, self.current_case.code)

	def test_only_assigned_user_can_download_target_report(self):
		with TemporaryDirectory() as media_root, override_settings(
			MEDIA_ROOT=media_root
		):
			self.current_case.attached_document.save(
				"target-report.docx",
				ContentFile(b"private report"),
				save=True,
			)
			file_url = reverse(
				"private-target-file",
				args=[self.current_case.pk, "attached_document"],
			)
			unassigned_user = get_user_model().objects.create_user(
				username="unassigned-viewer",
				password="test-password-123",
			)

			self.client.force_login(unassigned_user)
			self.assertEqual(self.client.get(file_url).status_code, 404)

			self.client.force_login(self.user)
			response = self.client.get(file_url)
			self.assertEqual(response.status_code, 200)
			self.assertIn(
				"attachment",
				response["Content-Disposition"],
			)
			self.assertEqual(b"".join(response.streaming_content), b"private report")

	def test_assigned_user_and_admin_can_read_docx_report_in_app(self):
		with TemporaryDirectory() as media_root, override_settings(
			MEDIA_ROOT=media_root
		):
			self.current_case.attached_document.save(
				"target-report.docx",
				ContentFile(self.make_docx_report()),
				save=True,
			)
			preview_url = reverse(
				"private-target-report",
				args=[self.current_case.pk],
			)
			self.client.force_login(self.user)
			response = self.client.get(preview_url)
			self.assertEqual(response.status_code, 200)
			self.assertContains(response, "Confidential report text")
			self.assertContains(response, "Table detail")
			self.assertContains(response, "Download original report")

			dashboard_response = self.client.get(reverse("dashboard"))
			self.assertContains(
				dashboard_response,
				f'href="{preview_url}">Read report</a>',
			)

			other_viewer = get_user_model().objects.create_user(
				username="report-unassigned-viewer",
				password="report-viewer-password",
			)
			self.client.force_login(other_viewer)
			self.assertEqual(self.client.get(preview_url).status_code, 404)

			admin_user = get_user_model().objects.create_superuser(
				username="report-preview-admin",
				password="report-admin-password",
			)
			self.client.force_login(admin_user)
			self.assertEqual(self.client.get(preview_url).status_code, 200)
			admin_dashboard = self.client.get(reverse("dashboard"))
			self.assertContains(
				admin_dashboard,
				f'href="{preview_url}">Read report</a>',
			)

			self.current_case.attached_document.save(
				"legacy-report.doc",
				ContentFile(b"legacy Word document"),
				save=True,
			)
			self.client.force_login(self.user)
			self.assertEqual(self.client.get(preview_url).status_code, 404)
			download_response = self.client.get(
				reverse(
					"private-target-file",
					args=[self.current_case.pk, "attached_document"],
				)
			)
			self.assertEqual(download_response.status_code, 200)
			self.assertIn("attachment", download_response["Content-Disposition"])
			self.assertEqual(
				b"".join(download_response.streaming_content),
				b"legacy Word document",
			)

	def test_staff_can_delete_one_phone_number_without_deleting_case_or_sibling(self):
		staff_user = get_user_model().objects.create_user(
			username="target-editor",
			password="valid-test-password",
			is_staff=True,
		)
		delete_permission = Permission.objects.get(
			content_type__app_label="Adminbackup",
			codename="delete_casephonenumber",
		)
		staff_user.user_permissions.add(delete_permission)
		self.current_case.assigned_user = staff_user
		self.current_case.save(update_fields=["assigned_user"])
		self.client.force_login(staff_user)
		selected_target = self.current_case.phone_numbers.get(
			number="0780000001"
		)
		dashboard_response = self.client.get(reverse("dashboard"))
		self.assertContains(
			dashboard_response,
			f'action="{reverse("delete-case-phone-number", args=[self.current_case.pk, selected_target.pk])}"',
		)

		response = self.client.post(
			reverse(
				"delete-case-phone-number",
				args=[self.current_case.pk, selected_target.pk],
			),
			{"status": "current", "q": "CURRENT-001"},
		)

		self.assertRedirects(
			response,
			f"{reverse('dashboard')}?status=current&q=CURRENT-001",
		)
		self.assertTrue(CaseRecord.objects.filter(pk=self.current_case.pk).exists())
		self.assertFalse(
			CasePhoneNumber.objects.filter(pk=selected_target.pk).exists()
		)
		self.assertTrue(
			CasePhoneNumber.objects.filter(
				case_record=self.current_case,
				number="0780000002",
			).exists()
		)

	def test_viewer_cannot_delete_a_phone_number(self):
		self.client.force_login(self.user)
		selected_target = self.current_case.phone_numbers.get(
			number="0780000001"
		)

		response = self.client.post(
			reverse(
				"delete-case-phone-number",
				args=[self.current_case.pk, selected_target.pk],
			),
		)

		self.assertEqual(response.status_code, 403)
		self.assertTrue(
			CasePhoneNumber.objects.filter(pk=selected_target.pk).exists()
		)

	def test_admin_deletes_targets_from_both_dashboards_and_numbers_each_status(self):
		third_case = self.create_case("CURRENT-003")
		admin_user = get_user_model().objects.create_superuser(
			username="dashboard-delete-admin",
		)
		self.client.force_login(admin_user)

		current_dashboard = self.client.get(reverse("dashboard"))
		self.assertContains(
			current_dashboard,
			f'action="{reverse("delete-target", args=[self.current_case.pk])}"',
		)

		response = self.client.post(
			reverse("delete-target", args=[self.current_case.pk]),
			{"status": "current", "q": "CURRENT"},
		)
		self.assertRedirects(
			response,
			f"{reverse('dashboard')}?status=current&q=CURRENT",
		)
		self.assertFalse(CaseRecord.objects.filter(pk=self.current_case.pk).exists())

		current_dashboard = self.client.get(
			reverse("dashboard"),
			{"status": "current"},
		)
		self.assertContains(current_dashboard, third_case.code)
		self.assertContains(current_dashboard, "<td>1</td>", html=True)
		stopped_dashboard = self.client.get(
			reverse("dashboard"),
			{"status": "stopped"},
		)
		self.assertContains(stopped_dashboard, self.stopped_case.code)
		self.assertContains(stopped_dashboard, "<td>1</td>", html=True)

		response = self.client.post(
			reverse("delete-target", args=[self.stopped_case.pk]),
			{"status": "stopped"},
		)
		self.assertRedirects(
			response,
			f"{reverse('dashboard')}?status=stopped",
		)
		current_dashboard = self.client.get(
			reverse("dashboard"),
			{"status": "current"},
		)
		self.assertContains(current_dashboard, third_case.code)
		self.assertContains(current_dashboard, "<td>1</td>", html=True)
		self.assertEqual(
			list(CaseRecord.objects.values_list("serial_number", flat=True)),
			["1"],
		)

	def test_target_gets_a_status_local_number_when_moved_to_stopped(self):
		admin_user = get_user_model().objects.create_superuser(
			username="dashboard-status-admin",
		)
		self.client.force_login(admin_user)
		self.current_case.first_termination_date = date(2026, 6, 1)
		self.current_case.save(update_fields=["first_termination_date"])

		current_dashboard = self.client.get(
			reverse("dashboard"),
			{"status": "current"},
		)
		self.assertNotContains(current_dashboard, self.current_case.code)

		stopped_dashboard = self.client.get(
			reverse("dashboard"),
			{"status": "stopped"},
		)
		self.assertEqual(
			list(stopped_dashboard.context["cases"].values_list("code", flat=True)),
			["STOPPED-001", "CURRENT-001"],
		)
		data_rows = re.findall(
			r"<tr>(.*?)</tr>",
			stopped_dashboard.content.decode(),
			flags=re.DOTALL,
		)[1:]
		row_text = [
			" ".join(
				re.sub(r"<[^>]+>", " ", row).split()
			)
			for row in data_rows
		]
		self.assertTrue(row_text[0].startswith("1 STOPPED-001"), row_text[0])
		self.assertTrue(row_text[1].startswith("2 CURRENT-001"), row_text[1])

	def test_viewer_cannot_delete_target_from_dashboard(self):
		self.client.force_login(self.user)
		response = self.client.post(
			reverse("delete-target", args=[self.current_case.pk]),
			{"status": "current"},
		)

		self.assertEqual(response.status_code, 403)
		self.assertTrue(CaseRecord.objects.filter(pk=self.current_case.pk).exists())

	def test_termination_moves_case_from_current_to_stopped(self):
		self.client.force_login(self.user)
		current_response = self.client.get(reverse("dashboard"))
		self.assertContains(current_response, self.current_case.code)

		self.current_case.first_termination_date = date(2026, 6, 1)
		self.current_case.save(update_fields=["first_termination_date"])

		current_response = self.client.get(reverse("dashboard"))
		stopped_response = self.client.get(
			reverse("dashboard"),
			{"status": "stopped"},
		)
		self.assertNotContains(current_response, self.current_case.code)
		self.assertContains(stopped_response, self.current_case.code)

	def test_reprovision_date_moves_case_from_stopped_to_current(self):
		self.client.force_login(self.user)
		stopped_response = self.client.get(
			reverse("dashboard"),
			{"status": "stopped"},
		)
		self.assertContains(stopped_response, self.stopped_case.code)

		self.stopped_case.reprovisioned_date = date(2026, 3, 1)
		self.stopped_case.save(update_fields=["reprovisioned_date"])

		current_response = self.client.get(reverse("dashboard"))
		stopped_response = self.client.get(
			reverse("dashboard"),
			{"status": "stopped"},
		)
		self.assertContains(current_response, self.stopped_case.code)
		self.assertNotContains(stopped_response, self.stopped_case.code)

	def test_viewer_cannot_modify_dashboard_records(self):
		self.client.force_login(self.user)
		response = self.client.post(
			reverse("dashboard"),
			{"status": "stopped", "code": "CHANGED"},
		)

		self.assertEqual(response.status_code, 405)
		self.current_case.refresh_from_db()
		self.assertEqual(self.current_case.code, "CURRENT-001")

	def test_case_editor_gets_link_with_target_add_and_delete_controls(self):
		editor = get_user_model().objects.create_user(
			username="case-editor",
			password="valid-test-password",
			is_staff=True,
		)
		permissions = Permission.objects.filter(
			content_type__app_label="Adminbackup",
			codename__in=(
				"change_caserecord",
				"add_casephonenumber",
				"change_casephonenumber",
				"delete_casephonenumber",
			),
		)
		editor.user_permissions.add(*permissions)
		self.current_case.assigned_user = editor
		self.current_case.save(update_fields=["assigned_user"])
		self.client.force_login(editor)

		edit_url = reverse(
			"admin:Adminbackup_caserecord_change",
			args=[self.current_case.pk],
		)
		dashboard_response = self.client.get(reverse("dashboard"))
		self.assertContains(dashboard_response, f'href="{edit_url}"')

		edit_response = self.client.get(edit_url)
		self.assertEqual(edit_response.status_code, 200)
		self.assertContains(edit_response, 'name="phone_numbers-0-DELETE"')
		self.assertContains(edit_response, 'name="phone_numbers-2-number"')


class SuperuserAccountCreationTests(TestCase):
	def setUp(self):
		self.admin_user = get_user_model().objects.create_superuser(
			username="user-admin",
			password="test-password",
		)

	def test_superuser_can_create_one_viewer_account(self):
		self.client.force_login(self.admin_user)
		response = self.client.post(
			reverse("create-users"),
			{
				"username": "viewer-one",
				"password1": "River!Stone4582",
				"password2": "River!Stone4582",
				"role": "viewer",
			},
		)

		self.assertRedirects(response, reverse("manage-users"))
		self.assertTrue(
			get_user_model().objects.get(username="viewer-one").check_password(
				"River!Stone4582"
			)
		)
		viewer = get_user_model().objects.get(username="viewer-one")
		self.assertFalse(viewer.is_staff)
		self.assertEqual(viewer.user_permissions.count(), 0)

	def test_superuser_can_create_a_target_editor(self):
		self.client.force_login(self.admin_user)
		response = self.client.post(
			reverse("create-users"),
			{
				"username": "target-editor",
				"password1": "River!Stone4582",
				"password2": "River!Stone4582",
				"role": "target_editor",
			},
		)

		self.assertRedirects(response, reverse("manage-users"))
		editor = get_user_model().objects.get(username="target-editor")
		self.assertTrue(editor.is_staff)
		self.assertFalse(editor.is_superuser)
		for codename in (
			"add_caserecord",
			"view_caserecord",
			"change_caserecord",
			"delete_caserecord",
			"add_casephonenumber",
			"change_casephonenumber",
			"delete_casephonenumber",
		):
			with self.subTest(permission=codename):
				self.assertTrue(editor.has_perm(f"Adminbackup.{codename}"))
		self.assertFalse(editor.has_perm("auth.add_user"))

	def test_superuser_can_create_a_view_all_records_only_user(self):
		self.client.force_login(self.admin_user)
		response = self.client.post(
			reverse("create-users"),
			{
				"username": "all-records-viewer",
				"password1": "River!Stone4582",
				"password2": "River!Stone4582",
				"role": "view_all_records",
			},
		)

		self.assertRedirects(response, reverse("manage-users"))
		viewer = get_user_model().objects.get(username="all-records-viewer")
		self.assertTrue(viewer.is_staff)
		self.assertFalse(viewer.is_superuser)
		self.assertTrue(viewer.has_perm("Adminbackup.view_caserecord"))
		for codename in (
			"add_caserecord",
			"change_caserecord",
			"delete_caserecord",
			"add_casephonenumber",
			"change_casephonenumber",
			"delete_casephonenumber",
		):
			with self.subTest(permission=codename):
				self.assertFalse(viewer.has_perm(f"Adminbackup.{codename}"))

	def test_duplicate_username_is_rejected(self):
		get_user_model().objects.create_user(username="existing-viewer")
		self.client.force_login(self.admin_user)
		response = self.client.post(
			reverse("create-users"),
			{
				"username": "existing-viewer",
				"password1": "River!Stone4582",
				"password2": "River!Stone4582",
				"role": "viewer",
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "A user with that username already exists.")
		self.assertEqual(
			get_user_model().objects.filter(username="existing-viewer").count(),
			1,
		)

	def test_create_user_page_displays_only_one_form(self):
		self.client.force_login(self.admin_user)
		response = self.client.get(reverse("create-users"))

		self.assertContains(response, 'name="username"')
		self.assertContains(response, 'name="password1"')
		self.assertContains(response, 'name="password2"')
		self.assertContains(response, 'name="role"')
		self.assertNotContains(response, "User 2")
		self.assertNotContains(response, "TOTAL_FORMS")
		self.assertNotContains(response, "dashboard_message")

	def test_non_superuser_cannot_create_accounts(self):
		viewer = get_user_model().objects.create_user(
			username="not-an-admin",
			password="test-password",
		)
		self.client.force_login(viewer)

		response = self.client.get(reverse("create-users"))

		self.assertEqual(response.status_code, 403)


class SuperuserUserManagementTests(TestCase):
	def setUp(self):
		self.admin_user = get_user_model().objects.create_superuser(
			username="manage-admin",
			password="Admin-valid-password-123",
		)
		self.viewer = get_user_model().objects.create_user(
			username="managed-viewer",
			password="Viewer-valid-password-123",
			email="viewer@example.com",
		)
		self.client.force_login(self.admin_user)

	def test_superuser_can_view_users_and_available_actions(self):
		response = self.client.get(reverse("manage-users"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, self.viewer.username)
		self.assertContains(response, self.admin_user.username)
		self.assertContains(response, "Reset password")
		self.assertContains(response, "Protected")
		self.assertContains(response, "Create user")
		self.assertNotContains(response, "Name</th>")
		self.assertNotContains(response, "Email</th>")
		self.assertContains(response, "Read-only viewer")
		self.assertEqual(
			response.content.decode().count('class="record-edit-link"'),
			3,
		)

	def test_superuser_can_edit_viewer_details(self):
		response = self.client.post(
			reverse("edit-user", args=[self.viewer.pk]),
			{
				"username": "renamed-viewer",
				"first_name": "Jane",
				"last_name": "Viewer",
				"email": "jane@example.com",
				"is_active": "on",
				"role": "viewer",
			},
		)

		self.assertRedirects(response, reverse("manage-users"))
		self.viewer.refresh_from_db()
		self.assertEqual(self.viewer.username, "renamed-viewer")
		self.assertEqual(self.viewer.first_name, "Jane")
		self.assertEqual(self.viewer.last_name, "Viewer")
		self.assertEqual(self.viewer.email, "jane@example.com")
		self.assertTrue(self.viewer.is_active)
		self.assertFalse(self.viewer.is_staff)
		self.assertFalse(self.viewer.is_superuser)
		self.assertEqual(self.viewer.user_permissions.count(), 0)

	def test_superuser_can_change_viewer_to_target_editor_and_back(self):
		edit_url = reverse("edit-user", args=[self.viewer.pk])
		editor_response = self.client.post(
			edit_url,
			{
				"username": self.viewer.username,
				"first_name": "",
				"last_name": "",
				"email": self.viewer.email,
				"is_active": "on",
				"role": "target_editor",
			},
		)
		self.assertRedirects(editor_response, reverse("manage-users"))
		self.viewer.refresh_from_db()
		self.assertTrue(self.viewer.is_staff)
		self.assertTrue(self.viewer.has_perm("Adminbackup.add_caserecord"))

		viewer_response = self.client.post(
			edit_url,
			{
				"username": self.viewer.username,
				"first_name": "",
				"last_name": "",
				"email": self.viewer.email,
				"is_active": "on",
				"role": "viewer",
			},
		)
		self.assertRedirects(viewer_response, reverse("manage-users"))
		self.viewer.refresh_from_db()
		self.assertFalse(self.viewer.is_staff)
		self.assertEqual(self.viewer.user_permissions.count(), 0)

	def test_superuser_can_set_a_new_viewer_password(self):
		response = self.client.post(
			reverse("reset-user-password", args=[self.viewer.pk]),
			{
				"new_password1": "New-viewer-password-984!",
				"new_password2": "New-viewer-password-984!",
			},
		)

		self.assertRedirects(response, reverse("manage-users"))
		self.viewer.refresh_from_db()
		self.assertTrue(self.viewer.check_password("New-viewer-password-984!"))

	def test_superuser_can_confirm_viewer_deletion(self):
		delete_url = reverse("delete-user", args=[self.viewer.pk])
		self.assertEqual(self.client.get(delete_url).status_code, 200)

		response = self.client.post(delete_url)

		self.assertRedirects(response, reverse("manage-users"))
		self.assertFalse(get_user_model().objects.filter(pk=self.viewer.pk).exists())

	def test_management_actions_cannot_modify_superuser_accounts(self):
		for url_name in ("edit-user", "reset-user-password", "delete-user"):
			with self.subTest(action=url_name):
				response = self.client.get(
					reverse(url_name, args=[self.admin_user.pk])
				)
				self.assertEqual(response.status_code, 404)

		self.assertTrue(
			get_user_model().objects.filter(pk=self.admin_user.pk).exists()
		)

	def test_non_superuser_cannot_access_user_management(self):
		viewer_client = self.client_class()
		viewer_client.force_login(self.viewer)

		for url in (
			reverse("manage-users"),
			reverse("edit-user", args=[self.viewer.pk]),
			reverse("reset-user-password", args=[self.viewer.pk]),
			reverse("delete-user", args=[self.viewer.pk]),
		):
			with self.subTest(url=url):
				self.assertEqual(viewer_client.get(url).status_code, 403)
