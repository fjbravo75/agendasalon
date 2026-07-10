import re

from django import template


register = template.Library()


@register.filter
def format_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("34"):
        local = digits[2:]
        return f"+34 {local[:3]} {local[3:6]} {local[6:]}"
    if len(digits) == 9:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    return value
