import mimetypes
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import NamedTuple

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape
from sqlalchemy.orm import Session

from app.config import get_settings
from app.exceptions import MailAssetNotFoundError, MailSendError, MailTemplateNotFoundError
from app.crud.model import get_model
from app.entities.sendmail.methods import SendmailMethods

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES_DIR = _PROJECT_ROOT / "mails"
_SHARED_ASSETS_DIR = _TEMPLATES_DIR / "assets"

_CID_PATTERN = re.compile(r'cid:([\w.-]+)')

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

settings = get_settings()


class SentMail(NamedTuple):
    from_address: str
    to: str
    subject: str
    body: str


class MailService:
    """Envoie des emails à partir de gabarits Jinja2 rangés dans `mails/` (racine du projet).

    Un gabarit `<nom>` est un dossier `mails/<nom>/` composé de `subject.j2` (sujet) et
    `html.j2` (corps HTML). `txt.j2` est optionnel et fournit l'alternative texte brut ;
    à défaut elle est dérivée du HTML.

    Une image référencée dans le HTML via `<img src="cid:<clé>">` est automatiquement
    intégrée à l'email : le fichier `<clé>.*` est cherché dans `mails/<nom>/assets/`
    (spécifique au gabarit) puis dans `mails/assets/` (partagé, ex: mails/gabarit.j2).
    """

    def send(
        self,
        db: Session,
        *,
        to: str | list[str],
        template: str,
        context: dict | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> SentMail:
        context = context or {}
        subject = self._render(f"{template}/subject.j2", context).strip()
        html_body = self._render(f"{template}/html.j2", context)
        text_body = self._render_optional(f"{template}/txt.j2", context)

        recipients = [to] if isinstance(to, str) else list(to)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{settings.mail_from_name} <{settings.mail_from_address}>"
        message["To"] = ", ".join(recipients)
        if cc:
            message["Cc"] = ", ".join(cc)

        message.set_content(text_body or _strip_html(html_body))
        message.add_alternative(html_body, subtype="html")
        self._embed_images(message, html_body, template)

        try:
            self._deliver(message, recipients + (cc or []) + (bcc or []))
        except (smtplib.SMTPException, OSError) as exc:
            raise MailSendError(str(exc)) from exc

        sent = SentMail(
            from_address=message["From"],
            to=message["To"],
            subject=subject,
            body=html_body,
        )
        SendmailMethods(get_model("sendmail")).create(db, {
            "from": sent.from_address,
            "to": sent.to,
            "subject": sent.subject,
            "content": sent.body,
        })
        return sent

    def _embed_images(self, message: EmailMessage, html_body: str, template: str) -> None:
        """Intègre chaque image référencée en `cid:<nom>` dans le HTML. Le fichier est
        cherché par nom (`<nom>.*`, extension libre) d'abord dans `mails/<template>/assets/`
        (propre au gabarit), puis dans `mails/assets/` (partagé, ex: gabarit.j2). Aucune
        liste à maintenir côté Python : il suffit de déposer le fichier au bon endroit.
        """
        cids = dict.fromkeys(_CID_PATTERN.findall(html_body))
        if not cids:
            return

        html_part = message.get_body(preferencelist=("html",))
        search_dirs = (_TEMPLATES_DIR / template / "assets", _SHARED_ASSETS_DIR)
        for cid in cids:
            path = next(
                (match for directory in search_dirs for match in sorted(directory.glob(f"{cid}.*"))),
                None,
            )
            if path is None:
                raise MailAssetNotFoundError(cid, template)

            mime_type, _ = mimetypes.guess_type(path.name)
            maintype, _, subtype = (mime_type or "application/octet-stream").partition("/")
            html_part.add_related(path.read_bytes(), maintype=maintype, subtype=subtype, cid=f"<{cid}>")

    def _deliver(self, message: EmailMessage, recipients: list[str]) -> None:
        smtp_cls = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
        with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls and not settings.smtp_use_ssl:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(message, to_addrs=recipients)

    def _render(self, filename: str, context: dict) -> str:
        try:
            return _env.get_template(filename).render(**context)
        except TemplateNotFound as exc:
            raise MailTemplateNotFoundError(filename) from exc

    def _render_optional(self, filename: str, context: dict) -> str | None:
        try:
            return _env.get_template(filename).render(**context)
        except TemplateNotFound:
            return None


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


_mail_service = MailService()


def get_mail_service() -> MailService:
    return _mail_service
