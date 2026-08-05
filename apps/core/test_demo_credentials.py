import secrets

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.core.demo_credentials import (
    DEMO_PROFESSIONAL_PASSWORD_SETTINGS,
    DEMO_SUPERADMIN_PASSWORD_SETTING,
    load_demo_passwords,
)
from apps.core.demo_scenario import BUSINESS_MARI, BUSINESS_NORTE


class DemoPasswordConfigurationTests(SimpleTestCase):
    setting_names = (
        DEMO_SUPERADMIN_PASSWORD_SETTING,
        *DEMO_PROFESSIONAL_PASSWORD_SETTINGS.values(),
    )

    def _valid_settings(self):
        return {setting_name: secrets.token_urlsafe(24) for setting_name in self.setting_names}

    def test_loads_all_three_passwords_from_settings(self):
        configured = self._valid_settings()

        with override_settings(**configured):
            superadmin_password, professional_passwords = load_demo_passwords()

        self.assertEqual(
            superadmin_password,
            configured[DEMO_SUPERADMIN_PASSWORD_SETTING],
        )
        self.assertEqual(
            professional_passwords,
            {
                BUSINESS_MARI: configured[
                    DEMO_PROFESSIONAL_PASSWORD_SETTINGS[BUSINESS_MARI]
                ],
                BUSINESS_NORTE: configured[
                    DEMO_PROFESSIONAL_PASSWORD_SETTINGS[BUSINESS_NORTE]
                ],
            },
        )

    def test_rejects_each_missing_password(self):
        for setting_name in self.setting_names:
            configured = self._valid_settings()
            configured[setting_name] = ""
            with self.subTest(setting_name=setting_name), override_settings(**configured):
                with self.assertRaisesRegex(ImproperlyConfigured, setting_name):
                    load_demo_passwords()

    def test_rejects_each_short_password(self):
        for setting_name in self.setting_names:
            configured = self._valid_settings()
            configured[setting_name] = "too-short"
            with self.subTest(setting_name=setting_name), override_settings(**configured):
                with self.assertRaisesRegex(ImproperlyConfigured, setting_name):
                    load_demo_passwords()
