"""Comprobaciones de configuración reutilizables (sin importar settings, fáciles de probar)."""


def email_config_problems(email_host: str, email_backend_env: str, public_base_url: str) -> list[str]:
    """Problemas que harían que en producción los emails o sus enlaces no funcionen."""
    problems = []
    if not email_host and not email_backend_env:
        problems.append(
            "EMAIL_HOST no está definido: con el backend de consola los envíos figuran como enviados "
            "pero no salen. Define EMAIL_HOST (y EMAIL_HOST_USER/PASSWORD) o EMAIL_BACKEND."
        )
    url = (public_base_url or "").lower()
    if not url.startswith(("http://", "https://")) or any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0")):
        problems.append(
            "PUBLIC_BASE_URL debe ser la URL pública de la app (p. ej. https://app.tu-dominio.com): "
            "los enlaces de presupuestos apuntarían a localhost."
        )
    return problems
