from apps.legal.services import platform_legal_context


def legal_context(request):
    return {"platform_legal": platform_legal_context()}
