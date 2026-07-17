from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.holidays.models import HolidaySyncRun, OfficialHoliday


class HolidayAdminReadOnlyTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            normalized_phone="+34910000902",
            phone="+34910000902",
            password="test-pass-123",
            full_name="Administración BOE",
        )
        self.holiday = OfficialHoliday.objects.create(
            date="2026-01-01",
            name="Año Nuevo",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="BOE - calendario laboral nacional",
        )
        self.run = HolidaySyncRun.objects.create(
            year=2026,
            source_name="BOE - calendario laboral nacional",
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            items_loaded=1,
        )
        self.client.force_login(self.superuser)

    def _admin_url(self, model, action, *args):
        opts = model._meta
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_{action}", args=args)

    def test_catalogue_and_runs_are_visible_without_mutation_permissions(self):
        request = RequestFactory().get("/admin/")
        request.user = self.superuser

        for model, instance in (
            (OfficialHoliday, self.holiday),
            (HolidaySyncRun, self.run),
        ):
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request, instance))
                self.assertFalse(model_admin.has_delete_permission(request, instance))
                self.assertEqual(model_admin.get_actions(request), {})
                self.assertEqual(
                    self.client.get(self._admin_url(model, "changelist")).status_code,
                    200,
                )
                self.assertEqual(
                    self.client.get(
                        self._admin_url(model, "change", instance.pk)
                    ).status_code,
                    200,
                )

    def test_catalogue_and_runs_reject_post_mutations(self):
        for model, instance in (
            (OfficialHoliday, self.holiday),
            (HolidaySyncRun, self.run),
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(
                    self.client.post(self._admin_url(model, "add"), {}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        self._admin_url(model, "change", instance.pk),
                        {},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        self._admin_url(model, "delete", instance.pk),
                        {"post": "yes"},
                    ).status_code,
                    403,
                )

        self.assertTrue(OfficialHoliday.objects.filter(pk=self.holiday.pk).exists())
        self.assertTrue(HolidaySyncRun.objects.filter(pk=self.run.pk).exists())

    @patch("apps.holidays.models.timezone.now")
    def test_run_admin_uses_the_operational_status_everywhere(self, mocked_now):
        current_time = timezone.make_aware(datetime(2026, 7, 17, 8, 0))
        mocked_now.return_value = current_time
        running = HolidaySyncRun.objects.create(
            year=2027,
            source_name="BOE - calendario laboral nacional",
            status=HolidaySyncRun.Status.FAILED,
            started_at=current_time - timedelta(minutes=5),
            finished_at=None,
        )
        interrupted = HolidaySyncRun.objects.create(
            year=2028,
            source_name="BOE - calendario laboral nacional",
            status=HolidaySyncRun.Status.FAILED,
            started_at=current_time - timedelta(minutes=16),
            finished_at=None,
        )
        finished_failure = HolidaySyncRun.objects.create(
            year=2029,
            source_name="BOE - calendario laboral nacional",
            status=HolidaySyncRun.Status.FAILED,
            started_at=current_time - timedelta(minutes=20),
            finished_at=current_time - timedelta(minutes=19),
        )
        run_admin = admin.site._registry[HolidaySyncRun]

        self.assertEqual(run_admin.visible_status(running), "En curso")
        self.assertEqual(run_admin.visible_status(interrupted), "Interrumpida")
        self.assertEqual(run_admin.visible_status(finished_failure), "Fallida")
        self.assertIn("(En curso)", str(running))
        self.assertNotIn("(failed)", str(running))

        change_response = self.client.get(
            self._admin_url(HolidaySyncRun, "change", running.pk)
        )
        self.assertContains(change_response, "Estado operativo")
        self.assertContains(change_response, "En curso")
        self.assertNotContains(change_response, "Fallida")

        changelist_url = self._admin_url(HolidaySyncRun, "changelist")
        running_response = self.client.get(
            changelist_url,
            {"presentation_status": "running"},
        )
        interrupted_response = self.client.get(
            changelist_url,
            {"presentation_status": "interrupted"},
        )
        failed_response = self.client.get(
            changelist_url,
            {"presentation_status": HolidaySyncRun.Status.FAILED},
        )

        self.assertEqual(
            tuple(running_response.context["cl"].result_list),
            (running,),
        )
        self.assertEqual(
            tuple(interrupted_response.context["cl"].result_list),
            (interrupted,),
        )
        self.assertEqual(
            tuple(failed_response.context["cl"].result_list),
            (finished_failure,),
        )
