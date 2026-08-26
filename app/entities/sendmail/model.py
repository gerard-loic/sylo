from app.crud.model import EntityModel, register_model


@register_model
class SendmailModel(EntityModel):
    name = "sendmail"
    table_name = "sendmails"
    relationships = []
