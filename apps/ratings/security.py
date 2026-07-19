from ipaddress import ip_address

from django.conf import settings


def get_client_ip_address(request):
    """Return only an IP address supplied by the trusted deployment boundary."""
    meta_name = "HTTP_X_REAL_IP" if settings.IS_RAILWAY else "REMOTE_ADDR"
    candidate = request.META.get(meta_name, "").strip()
    if not candidate:
        return None

    try:
        return str(ip_address(candidate))
    except ValueError:
        return None
