from app.crud.methods import BaseCRUDMethods


class UsertokenMethods(BaseCRUDMethods):
    """CRUD générique : alimenté uniquement par UserMethods.login (voir
    app/entities/user/methods.py), aucune route HTTP n'est exposée dessus."""
