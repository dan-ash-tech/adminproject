from mimetypes import guess_type
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
	AdminPasswordResetForm,
	CasePhoneNumberFormSet,
	CaseRecordIntakeForm,
	ManagedUserForm,
	ViewerUserCreationForm,
)
from .models import CasePhoneNumber, CaseRecord


DEFAULT_DASHBOARD_TEXT = {
	"title": "Target Monitoring",
	"message": (
		"View target records by monitoring status. "
		"Reprovisioned targets return to current monitoring."
	),
}

# Edit each account's title and message here. Add new usernames as accounts are created.
USER_DASHBOARD_TEXT = {
	"passy": {
		"title": "Target Monitoring",
		"message": (
			"View target records by monitoring status. "
			"Reprovisioned targets return to current monitoring."
		),
	},
	"user1": {
		"title": "Target Monitoring",
		"message": (
			
			"FDRL Agents And their ASSOCIATES."
		),
	},
	"user2": {
		"title": "Target Monitoring",
		"message": (
			"View target records by monitoring status. "
			"Reprovisioned targets return to current monitoring."
		),
	},
	"user3": {
		"title": "Target Monitoring",
		"message": (
		
			"SPECIAL TARGETS AND OTHER CRIME CASES."
		),
	},
}


def staff_can_add_cases(user):
	return user.is_staff and user.has_perm("Adminbackup.add_caserecord")


TARGET_EDITOR_PERMISSIONS = (
	"add_caserecord",
	"change_caserecord",
	"delete_caserecord",
	"view_caserecord",
	"add_casephonenumber",
	"change_casephonenumber",
	"delete_casephonenumber",
)
VIEW_ALL_RECORDS_PERMISSIONS = ("view_caserecord",)


def _apply_user_role(user, role):
	with transaction.atomic():
		user.is_staff = role in ("view_all_records", "target_editor")
		user.save(update_fields=["is_staff"])
		if role == "target_editor":
			permissions = Permission.objects.filter(
				content_type__app_label="Adminbackup",
				codename__in=TARGET_EDITOR_PERMISSIONS,
			)
			if permissions.count() != len(TARGET_EDITOR_PERMISSIONS):
				raise Permission.DoesNotExist(
					"Target-editor permissions are not fully configured."
				)
			user.user_permissions.set(permissions)
		elif role == "view_all_records":
			permissions = Permission.objects.filter(
				content_type__app_label="Adminbackup",
				codename__in=VIEW_ALL_RECORDS_PERMISSIONS,
			)
			if permissions.count() != len(VIEW_ALL_RECORDS_PERMISSIONS):
				raise Permission.DoesNotExist(
					"View-all-records permission is not configured."
				)
			user.user_permissions.set(permissions)
		else:
			user.user_permissions.clear()


def _can_view_all_records(user):
	return (
		user.is_staff
		and user.has_perm("Adminbackup.view_caserecord")
		and not user.has_perm("Adminbackup.change_caserecord")
	)


@login_required
@require_GET
def dashboard(request):
	if request.user.is_superuser:
		cases = CaseRecord.objects.all()
		current_target_count = CaseRecord.objects.current_monitored().count()
		stopped_target_count = CaseRecord.objects.stopped().count()
	elif _can_view_all_records(request.user):
		cases = CaseRecord.objects.all()
		current_target_count = None
		stopped_target_count = None
	else:
		cases = CaseRecord.objects.filter(assigned_user=request.user)
		current_target_count = None
		stopped_target_count = None

	status = request.GET.get("status", "current")
	if status == "stopped":
		cases = cases.stopped().order_by(
			Coalesce("second_termination_date", "first_termination_date"),
			"pk",
		)
		status = "stopped"
	else:
		cases = cases.current_monitored().order_by("start_date", "pk")
		status = "current"

	search_query = request.GET.get("q", "").strip()
	if search_query:
		matches = (
			Q(serial_number__icontains=search_query)
			| Q(code__icontains=search_query)
			| Q(first_name__icontains=search_query)
			| Q(last_name__icontains=search_query)
			| Q(phone_numbers__number__icontains=search_query)
			| Q(phone_numbers__label__icontains=search_query)
			| Q(authorizer__icontains=search_query)
			| Q(case_type__icontains=search_query)
			| Q(assigned_user__username__icontains=search_query)
			| Q(personal_identifications__icontains=search_query)
			| Q(attached_document__icontains=search_query)
		)
		search_date = parse_date(search_query)
		if search_date:
			matches |= (
				Q(start_date=search_date)
				| Q(first_termination_date=search_date)
				| Q(reprovisioned_date=search_date)
				| Q(second_termination_date=search_date)
			)
		cases = cases.filter(matches).distinct()

	cases = cases.prefetch_related("phone_numbers")
	dashboard_text = USER_DASHBOARD_TEXT.get(
		request.user.get_username(),
		DEFAULT_DASHBOARD_TEXT,
	)
	return render(
		request,
		"case_portal/dashboard.html",
		{
			"cases": cases,
			"status": status,
			"search_query": search_query,
			"dashboard_title": dashboard_text["title"],
			"dashboard_message": dashboard_text["message"],
			"show_target_totals": request.user.is_superuser,
			"current_target_count": current_target_count,
			"stopped_target_count": stopped_target_count,
			"can_edit_targets": (
				request.user.is_staff
				and request.user.has_perm("Adminbackup.change_caserecord")
				and request.user.has_perm("Adminbackup.add_casephonenumber")
				and request.user.has_perm("Adminbackup.change_casephonenumber")
				and request.user.has_perm("Adminbackup.delete_casephonenumber")
			),
			"can_delete_targets": (
				request.user.is_staff
				and request.user.has_perm("Adminbackup.delete_caserecord")
			),
		},
	)


@login_required
@require_http_methods(["GET", "POST"])
def create_users(request):
	if not request.user.is_superuser:
		raise PermissionDenied

	if request.method == "POST":
		user_form = ViewerUserCreationForm(request.POST)
		if user_form.is_valid():
			with transaction.atomic():
				user = user_form.save()
				_apply_user_role(user, user_form.cleaned_data["role"])
			messages.success(request, "User account created.")
			return redirect("manage-users")
	else:
		user_form = ViewerUserCreationForm()

	return render(
		request,
		"case_portal/create_users.html",
		{"user_form": user_form},
	)


def _managed_viewer_or_404(user_id):
	return get_object_or_404(
		get_user_model(),
		pk=user_id,
		is_superuser=False,
	)


@login_required
@require_GET
def manage_users(request):
	if not request.user.is_superuser:
		raise PermissionDenied

	managed_users = list(
		get_user_model().objects.order_by("-is_superuser", "username")
	)
	for managed_user in managed_users:
		if managed_user.is_superuser:
			managed_user.account_role = "Administrator"
		elif _can_view_all_records(managed_user):
			managed_user.account_role = "View all records only"
		elif managed_user.is_staff:
			managed_user.account_role = "Target editor"
		else:
			managed_user.account_role = "Read-only viewer"

	return render(
		request,
		"case_portal/manage_users.html",
		{"managed_users": managed_users},
	)


@login_required
@require_http_methods(["GET", "POST"])
def edit_user(request, user_id):
	if not request.user.is_superuser:
		raise PermissionDenied

	user = _managed_viewer_or_404(user_id)
	if request.method == "POST":
		form = ManagedUserForm(request.POST, instance=user)
		if form.is_valid():
			with transaction.atomic():
				form.save()
				_apply_user_role(user, form.cleaned_data["role"])
			messages.success(request, f"User {user.username} updated.")
			return redirect("manage-users")
	else:
		form = ManagedUserForm(instance=user)

	return render(
		request,
		"case_portal/edit_user.html",
		{"form": form, "managed_user": user},
	)


@login_required
@require_http_methods(["GET", "POST"])
def reset_user_password(request, user_id):
	if not request.user.is_superuser:
		raise PermissionDenied

	user = _managed_viewer_or_404(user_id)
	if request.method == "POST":
		form = AdminPasswordResetForm(user, request.POST)
		if form.is_valid():
			form.save()
			messages.success(request, f"Password reset for {user.username}.")
			return redirect("manage-users")
	else:
		form = AdminPasswordResetForm(user)

	return render(
		request,
		"case_portal/reset_user_password.html",
		{"form": form, "managed_user": user},
	)


@login_required
@require_http_methods(["GET", "POST"])
def delete_user(request, user_id):
	if not request.user.is_superuser:
		raise PermissionDenied

	user = _managed_viewer_or_404(user_id)
	if request.method == "POST":
		username = user.username
		user.delete()
		messages.success(request, f"User {username} deleted.")
		return redirect("manage-users")

	return render(
		request,
		"case_portal/delete_user.html",
		{"managed_user": user},
	)


@login_required
@require_POST
def delete_case_phone_number(request, case_id, phone_number_id):
	if not (
		request.user.is_staff
		and request.user.has_perm("Adminbackup.delete_casephonenumber")
	):
		raise PermissionDenied

	phone_number = get_object_or_404(
		CasePhoneNumber,
		pk=phone_number_id,
		case_record_id=case_id,
	)
	phone_number.delete()

	status = request.POST.get("status", "current")
	if status not in ("current", "stopped"):
		status = "current"
	redirect_params = {"status": status}
	search_query = request.POST.get("q", "").strip()
	if search_query:
		redirect_params["q"] = search_query

	return redirect(f"{reverse('dashboard')}?{urlencode(redirect_params)}")


@login_required
@require_POST
def delete_target(request, case_id):
	if not (
		request.user.is_staff
		and request.user.has_perm("Adminbackup.delete_caserecord")
	):
		raise PermissionDenied

	case_record = get_object_or_404(CaseRecord, pk=case_id)
	case_record.delete()

	status = request.POST.get("status", "current")
	if status not in ("current", "stopped"):
		status = "current"
	redirect_params = {"status": status}
	search_query = request.POST.get("q", "").strip()
	if search_query:
		redirect_params["q"] = search_query

	messages.success(request, "Target deleted. Remaining targets were renumbered.")
	return redirect(f"{reverse('dashboard')}?{urlencode(redirect_params)}")


@login_required
def submit_case(request):
	if not staff_can_add_cases(request.user):
		raise PermissionDenied

	if request.method == "POST":
		case_form = CaseRecordIntakeForm(
			request.POST,
			request.FILES,
			can_assign=request.user.is_superuser,
		)
		phone_formset = CasePhoneNumberFormSet(
			request.POST,
			prefix="phone_numbers",
		)
		if case_form.is_valid() and phone_formset.is_valid():
			with transaction.atomic():
				case_record = case_form.save()
				phone_formset.instance = case_record
				phone_formset.save()
			return redirect("case-submission-complete")
	else:
		case_form = CaseRecordIntakeForm(can_assign=request.user.is_superuser)
		phone_formset = CasePhoneNumberFormSet(prefix="phone_numbers")

	return render(
		request,
		"case_portal/case_form.html",
		{"case_form": case_form, "phone_formset": phone_formset},
	)


@login_required
def case_submission_complete(request):
	if not staff_can_add_cases(request.user):
		raise PermissionDenied
	return render(request, "case_portal/submission_complete.html")


@staff_member_required
def private_identification_media(request, filename):
	relative_path = f"personal_identifications/{filename}"
	record = CaseRecord.objects.filter(
		personal_identifications=relative_path
	).first()
	if record is None:
		raise Http404

	if not (
		request.user.has_perm("Adminbackup.view_caserecord")
		or request.user.has_perm("Adminbackup.change_caserecord")
	):
		raise PermissionDenied

	return FileResponse(
		record.personal_identifications.open("rb"),
		content_type=guess_type(record.personal_identifications.name)[0]
		or "application/octet-stream",
	)


@login_required
@require_GET
def private_target_file(request, case_id, field_name):
	if field_name not in ("personal_identifications", "attached_document"):
		raise Http404

	record = get_object_or_404(CaseRecord, pk=case_id)
	can_view_all = request.user.is_staff and (
		request.user.has_perm("Adminbackup.view_caserecord")
		or request.user.has_perm("Adminbackup.change_caserecord")
	)
	if not (
		request.user.is_superuser
		or can_view_all
		or record.assigned_user_id == request.user.pk
	):
		raise Http404

	target_file = getattr(record, field_name)
	if not target_file:
		raise Http404

	is_report = field_name == "attached_document"
	return FileResponse(
		target_file.open("rb"),
		as_attachment=is_report,
		filename=target_file.name.rsplit("/", 1)[-1],
		content_type=guess_type(target_file.name)[0]
		or "application/octet-stream",
	)


@login_required
@require_GET
def private_target_report(request, case_id):
	record = get_object_or_404(CaseRecord, pk=case_id)
	can_view_all = request.user.is_staff and (
		request.user.has_perm("Adminbackup.view_caserecord")
		or request.user.has_perm("Adminbackup.change_caserecord")
	)
	if not (
		request.user.is_superuser
		or can_view_all
		or record.assigned_user_id == request.user.pk
	):
		raise Http404

	if not record.attached_document:
		raise Http404

	if record.attached_document.name.rsplit(".", 1)[-1].lower() != "docx":
		raise Http404("In-app preview is available for .docx reports only.")

	namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
	try:
		with record.attached_document.open("rb") as report_file:
			with ZipFile(report_file) as archive:
				document = ElementTree.fromstring(
					archive.read("word/document.xml")
				)
	except (BadZipFile, KeyError, ElementTree.ParseError) as error:
		raise Http404("The report could not be read as a valid .docx file.") from error

	body = document.find(f"{namespace}body")
	if body is None:
		raise Http404("The report does not contain readable document content.")

	blocks = []
	for element in body:
		if element.tag == f"{namespace}p":
			text = _word_paragraph_text(element, namespace)
			if text:
				blocks.append({"type": "paragraph", "text": text})
		elif element.tag == f"{namespace}tbl":
			rows = []
			for row in element.findall(f"{namespace}tr"):
				rows.append(
					[
						" ".join(
							text
							for paragraph in cell.findall(f"{namespace}p")
							if (
								text := _word_paragraph_text(paragraph, namespace)
							)
						)
						for cell in row.findall(f"{namespace}tc")
					]
				)
			if rows:
				blocks.append({"type": "table", "rows": rows})

	return render(
		request,
		"case_portal/report_preview.html",
		{"case": record, "blocks": blocks},
	)


def _word_paragraph_text(paragraph, namespace):
	parts = []
	for element in paragraph.iter():
		if element.tag == f"{namespace}t" and element.text:
			parts.append(element.text)
		elif element.tag == f"{namespace}tab":
			parts.append("\t")
		elif element.tag == f"{namespace}br":
			parts.append("\n")
	return "".join(parts).strip()
