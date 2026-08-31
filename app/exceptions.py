from typing import Any


class ApiError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: Any = None):
        self.message = message
        self.details = details
        super().__init__(message)


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"

    def __init__(self, entity: str, identifier: Any):
        super().__init__(f"{entity} '{identifier}' introuvable.")


class ConflictError(ApiError):
    status_code = 409
    code = "conflict"


class UnauthorizedError(ApiError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(ApiError):
    status_code = 403
    code = "forbidden"


class InvalidFilterError(ApiError):
    status_code = 400
    code = "invalid_filter"


class InvalidSortError(ApiError):
    status_code = 400
    code = "invalid_sort"


class MailError(ApiError):
    status_code = 502
    code = "mail_error"


class MailTemplateNotFoundError(MailError):
    code = "mail_template_not_found"

    def __init__(self, template: str):
        super().__init__(f"Gabarit d'email '{template}' introuvable.")


class MailSendError(MailError):
    code = "mail_send_error"

    def __init__(self, reason: str):
        super().__init__(f"Échec de l'envoi de l'email : {reason}")


class MailAssetNotFoundError(MailError):
    code = "mail_asset_not_found"

    def __init__(self, cid: str, template: str):
        super().__init__(
            f"Image '{cid}' référencée par le gabarit '{template}' introuvable "
            f"(cherchée dans mails/{template}/assets/, app/mail/templates/{template}/assets/, "
            f"mails/assets/ et app/mail/templates/assets/)."
        )
