from app.crud.model import EntityModel, register_model


@register_model
class UsertokenModel(EntityModel):
    name = "usertoken"
    table_name = "user_tokens"
    relationships = []
