from app.crud.model import EntityModel, register_model
from app.crud.relationships import ManyToMany, ManyToOne


@register_model
class RoleModel(EntityModel):
    name = "role"
    table_name = "roles"
    relationships = [
        ManyToMany(
            attribute="permissions",
            target="permission",
            association_table="role_permission",
            local_key="role_id",
            remote_key="permission_id",
        )
    ]
    anonymized_fields = ("uid",)