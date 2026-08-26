from app.crud.methods import BaseCRUDMethods


class SendmailMethods(BaseCRUDMethods):
    """CRUD générique : ce log est uniquement alimenté par les entités qui envoient
    des emails (ex: UserMethods.create), aucune route HTTP n'est exposée dessus."""
