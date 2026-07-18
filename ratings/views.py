from django.http import HttpResponse


def home(request):
    return HttpResponse("Friendly Rating")


def health(request):
    return HttpResponse("ok", content_type="text/plain")
