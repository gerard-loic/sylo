from app.crud.model import EntityModel, register_model
from app.crud.relationships import ManyToMany, ManyToOne


@register_model
class UserModel(EntityModel):
    name = "user"
    table_name = "users"
    hidden_fields = frozenset({"password", "initial_token", "deleted_at"})
    relationships = [
        ManyToMany(
            attribute="roles",
            target="role",
            association_table="user_role",
            local_key="user_id",
            remote_key="role_id",
        ),
    ]
