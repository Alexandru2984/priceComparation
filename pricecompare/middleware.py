class SecurityHeadersMiddleware:
    """Add restrictive browser policies to both public and private responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
            "object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'",
        )
        camera = "(self)" if request.path == "/app/catalog/scaneaza-ean/" else "()"
        response.setdefault(
            "Permissions-Policy", f"camera={camera}, microphone=(), geolocation=(), payment=()"
        )
        if request.path.startswith(("/app/", "/admin/")):
            response["Cache-Control"] = "private, no-store"
        return response
