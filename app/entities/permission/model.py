from app.crud.model import EntityModel, register_model
from app.crud.relationships import ManyToMany, ManyToOne


@register_model
class PermissionModel(EntityModel):
    name = "permission"
    table_name = "permissions"
    relationships = [
    ]
    anonymized_fields = ("uid",)
