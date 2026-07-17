from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


LEGAL_TEMPLATE_LINK_COUNTS = {
    "templates/businesses/signup_request_form.html": 1,
    "templates/customers/client_email_verified.html": 1,
    "templates/customers/client_invitation_activate.html": 1,
    "templates/customers/client_register.html": 1,
    "templates/legal/professional_onboarding.html": 3,
    "templates/professional/appointment_assistant.html": 1,
    "templates/professional/clients/list.html": 1,
    "templates/public/booking.html": 1,
}

CHECKBOX_LABEL_TARGETS = {
    "templates/businesses/signup_request_form.html": {
        "{{ form.privacy_acknowledged.id_for_label }}",
    },
    "templates/customers/client_email_verified.html": {
        "{{ verification_form.privacy_acknowledged.id_for_label }}",
    },
    "templates/legal/professional_onboarding.html": {
        "{{ onboarding_form.platform_privacy_acknowledged.id_for_label }}",
        "{{ onboarding_form.terms_accepted.id_for_label }}",
        "{{ onboarding_form.data_processing_accepted.id_for_label }}",
        "{{ onboarding_form.authority_declared.id_for_label }}",
    },
    "templates/public/booking.html": {"privacy-acknowledged"},
}

INFORMATION_ONLY_TEMPLATES = (
    "templates/customers/client_register.html",
    "templates/customers/client_invitation_activate.html",
)


class _ConsentMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.label_depth = 0
        self.labels_for = set()
        self.links_inside_labels = 0
        self.new_tab_links = []
        self.current_new_tab_link = None
        self.checkbox_inputs = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "label":
            self.label_depth += 1
            if attributes.get("for"):
                self.labels_for.add(attributes["for"])
        elif tag == "input" and attributes.get("type") == "checkbox":
            self.checkbox_inputs += 1
        elif tag == "a":
            if self.label_depth:
                self.links_inside_labels += 1
            if attributes.get("target") == "_blank":
                self.current_new_tab_link = {
                    "rel": set((attributes.get("rel") or "").split()),
                    "has_note": False,
                    "text": [],
                }
        elif tag == "span" and self.current_new_tab_link is not None:
            classes = set((attributes.get("class") or "").split())
            if "legal-new-tab-note" in classes:
                self.current_new_tab_link["has_note"] = True

    def handle_data(self, data):
        if self.current_new_tab_link is not None:
            self.current_new_tab_link["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current_new_tab_link is not None:
            self.current_new_tab_link["text"] = " ".join(
                " ".join(self.current_new_tab_link["text"]).split()
            )
            self.new_tab_links.append(self.current_new_tab_link)
            self.current_new_tab_link = None
        elif tag == "label":
            self.label_depth -= 1


def _parse_template(relative_path):
    source = (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")
    parser = _ConsentMarkupParser()
    parser.feed(source)
    parser.close()
    return source, parser


class LegalConsentMarkupTests(SimpleTestCase):
    def test_legal_links_are_outside_labels_and_explain_the_new_tab(self):
        for relative_path, expected_links in LEGAL_TEMPLATE_LINK_COUNTS.items():
            with self.subTest(template=relative_path):
                _, parser = _parse_template(relative_path)

                self.assertEqual(parser.links_inside_labels, 0)
                self.assertEqual(len(parser.new_tab_links), expected_links)
                for link in parser.new_tab_links:
                    self.assertIn("noopener", link["rel"])
                    self.assertTrue(link["has_note"])
                    self.assertIn("se abre en otra pestaña", link["text"])

    def test_checkbox_declarations_keep_explicit_label_targets(self):
        for relative_path, expected_targets in CHECKBOX_LABEL_TARGETS.items():
            with self.subTest(template=relative_path):
                _, parser = _parse_template(relative_path)

                self.assertTrue(expected_targets.issubset(parser.labels_for))

    def test_registration_and_invitation_remain_informational(self):
        for relative_path in INFORMATION_ONLY_TEMPLATES:
            with self.subTest(template=relative_path):
                source, parser = _parse_template(relative_path)

                self.assertNotIn("privacy_acknowledged", source)
                self.assertEqual(parser.checkbox_inputs, 0)
                self.assertIn("te pediremos confirmar", source)
