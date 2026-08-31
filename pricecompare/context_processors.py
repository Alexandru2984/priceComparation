from django.conf import settings


def deployment_environment(request):
    return {"deployment_environment": settings.DEPLOYMENT_ENVIRONMENT}
